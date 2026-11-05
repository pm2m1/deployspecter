"""EXPERIMENTAL read-only corpus selection tool: apply the frozen selection rules (CORPUS_SHORTLIST.json v2) to pinned
candidates and emit an auditable report. It never installs, imports or runs a candidate's code.

    python research_tools/corpus_select.py --candidates FILE.json --out DIR

``FILE.json`` is a list of ``{"pypi": name, "version": v, "repository": url, "tag": t, "commit": optional sha}``.

For each candidate, in this order:

1. Refuse a frozen protocol held-out application by name, before any network access, and classify a frozen
   development application or a prototype fixture as development (never a prospective blind case).
2. PyPI JSON for that exact version: licence, requires_python, requires_dist; the sdist's published sha256.
3. Download the sdist into an empty directory and verify its sha256; ``git ls-remote`` the tag and compare with the
   given commit (a mismatch is refused).
4. Migration layout, discovered statically from the archive:
   * file names only, for every layout;
   * Django: ``INSTALLED_APPS`` parsed with ``ast`` from settings modules (literal lists only; anything computed is
     reported as unresolved), matched to app packages with a ``migrations/`` directory of ``NNNN_*.py`` files;
   * Alembic: ``script_location`` from ``alembic.ini``, and from each revision file only its ``revision`` and
     ``down_revision`` assignments (via ``ast``), giving the revision graph's bases and heads; nothing else in a
     revision file is kept.
5. The frozen migration-area rule, applied literally: the highest-numbered file of the core package's
   ``migrations/`` directory (plugins excluded) plus the module that applies migrations; ``NONE_BY_RULE`` without such
   a directory; ``NEEDS_PROTOCOL_RULING`` where the frozen rule doesn't define an answer (several app-level
   ``migrations/`` directories, hash-named Alembic revisions). No new rule is applied silently.

Every step records what it read (the exposure ledger), and the report carries the tool's own sha256.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path
from typing import Any

HELD_OUT = {"saleor", "healthchecks", "netbox", "authentik"}  # frozen protocol held-out: sources stay unread
DEVELOPMENT = {"inventree": "frozen protocol development application", "paperless-ngx": "frozen protocol development "
               "application", "langgraph-checkpoint-postgres": "frozen protocol development application",
               "procrastinate": "frozen protocol development application",
               "pgqueuer": "prototype compatibility target (adapter written from its source)",
               "chancy": "prototype compatibility target (adapter written from its source)"}
OSI = {"MIT", "Apache-2.0", "BSD-3-Clause", "BSD-2-Clause", "GPL-3.0-or-later", "GPL-3.0-only", "GPL-2.0-or-later",
       "AGPL-3.0-only", "AGPL-3.0-or-later", "LGPL-3.0-or-later", "LGPL-2.1-or-later", "MPL-2.0", "ISC", "PSF-2.0"}
OSI_CLASSIFIERS = {"MIT License": "MIT", "Apache Software License": "Apache-2.0", "BSD License": "BSD",
                   "GNU General Public License v3 (GPLv3)": "GPL-3.0",
                   "Mozilla Public License 2.0 (MPL 2.0)": "MPL-2.0",
                   "GNU Affero General Public License v3": "AGPL-3.0", "ISC License (ISCL)": "ISC"}
DJANGO_MIGRATION = re.compile(r"^\d{4}_.*\.py$")
NUMBERED = re.compile(r"^v?(\d+)(?:_.*)?\.py$")


class Refused(RuntimeError):
    pass


# ------------------------------------------------------------------ metadata


def classify_licence(info: dict[str, Any]) -> dict[str, Any]:
    expr = info.get("license_expression")
    classifiers = [c.split("::")[-1].strip() for c in info.get("classifiers", []) if c.startswith("License")]
    if expr:
        osi = all(part.strip("() ") in OSI for part in re.split(r"\s+(?:OR|AND|WITH)\s+", expr))
        return {"declared": expr, "source": "license_expression", "osi_approved": osi}
    mapped = [OSI_CLASSIFIERS[c] for c in classifiers if c in OSI_CLASSIFIERS]
    if mapped:
        return {"declared": mapped, "source": "classifier", "osi_approved": True}
    return {"declared": classifiers or info.get("license"), "source": "classifier or free text",
            "osi_approved": False if any("Proprietary" in c for c in classifiers) else None}


def classify_dependencies(requires: list[str]) -> dict[str, Any]:
    def name(r: str) -> str:
        return re.match(r"(?i)[a-z0-9_.-]+", r).group(0).lower().replace("_", "-")

    def extra(r: str) -> str | None:
        m = re.search(r"extra\s*==\s*['\"]([^'\"]+)['\"]", r)
        return m.group(1) if m else None
    drivers = [{"name": name(r), "requirement": r, "extra": extra(r)} for r in requires
               if name(r).split("[")[0] in ("psycopg", "psycopg2", "psycopg2-binary", "asyncpg", "pg8000")]
    psycopg3 = [d for d in drivers if d["name"].startswith("psycopg") and not d["name"].startswith("psycopg2")]
    frameworks = sorted({name(r) for r in requires if name(r) in ("django", "sqlalchemy", "alembic", "flask",
                                                                  "fastapi", "sqlmodel", "tortoise-orm")})
    return {"drivers": drivers, "frameworks": frameworks,
            "psycopg3": "required" if any(d["extra"] is None for d in psycopg3)
            else ("extra_only:" + ",".join(sorted({d["extra"] for d in psycopg3})) if psycopg3 else "absent")}


# ------------------------------------------------------------------ the archive, statically


def discover(archive: Path, exposure: list[str]) -> dict[str, Any]:
    with tarfile.open(archive) as tf:
        members = [m for m in tf.getmembers() if m.isfile()]
        names = [m.name for m in members]
        exposure.append(f"file names of {len(names)} archive members")
        root = names[0].split("/")[0] if names else ""
        listing_sha = hashlib.sha256("\n".join(sorted(names)).encode()).hexdigest()

        def read(name: str) -> str:
            f = tf.extractfile(name)
            return f.read().decode("utf-8", "replace") if f else ""

        rel = [n[len(root) + 1:] for n in names]
        out: dict[str, Any] = {"archive_listing_sha256": listing_sha, "layouts": []}
        # Django apps with migrations
        app_dirs = sorted({r.rsplit("/migrations/", 1)[0] for r in rel if "/migrations/" in r
                           and DJANGO_MIGRATION.match(r.rsplit("/", 1)[1]) and "/tests/" not in r})
        installed: list[str] | None = None
        unresolved = False
        for r in rel:
            if re.search(r"(^|/)settings(/[a-z_]+)?\.py$|(^|/)settings_?[a-z_]*\.py$", r) and "/tests/" not in r:
                exposure.append(f"INSTALLED_APPS literal parsed from {r}")
                try:
                    tree = ast.parse(read(f"{root}/{r}"))
                except SyntaxError:
                    continue
                for node in ast.walk(tree):
                    if isinstance(node, (ast.Assign, ast.AugAssign)):
                        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                        if any(isinstance(t, ast.Name) and t.id == "INSTALLED_APPS" for t in targets):
                            try:
                                value = ast.literal_eval(node.value)
                                installed = sorted(set(installed or []) | {v for v in value if isinstance(v, str)})
                            except ValueError:
                                unresolved = True
        if app_dirs:
            counts = {d: sum(1 for r in rel if r.startswith(d + "/migrations/")
                             and DJANGO_MIGRATION.match(r.rsplit("/", 1)[1])) for d in app_dirs}
            out["layouts"].append({"kind": "django", "app_migration_dirs": counts, "installed_apps": installed,
                                   "installed_apps_unresolved": unresolved})
        # Alembic
        inis = [r for r in rel if r.endswith("alembic.ini")]
        version_dirs = sorted({r.rsplit("/", 1)[0] for r in rel if re.search(r"/versions/[^/]+\.py$", r)
                               and "/tests/" not in r})
        for vd in version_dirs:
            if not any(r == vd.rsplit("/", 1)[0] + "/env.py" for r in rel):
                continue
            graph = {}
            for r in rel:
                if r.startswith(vd + "/") and r.endswith(".py") and "/" not in r[len(vd) + 1:]:
                    try:
                        tree = ast.parse(read(f"{root}/{r}"))
                    except SyntaxError:
                        continue
                    fields: dict[str, Any] = {}
                    for node in tree.body:
                        if isinstance(node, (ast.Assign, ast.AnnAssign)):
                            t = node.targets[0] if isinstance(node, ast.Assign) else node.target
                            if isinstance(t, ast.Name) and t.id in ("revision", "down_revision") and node.value:
                                try:
                                    fields[t.id] = ast.literal_eval(node.value)
                                except ValueError:
                                    fields[t.id] = "<unresolved>"
                    if "revision" in fields:
                        graph[fields["revision"]] = fields.get("down_revision")
            exposure.append(f"revision/down_revision headers only, from {len(graph)} files under {vd}")
            parents = {p for d in graph.values() for p in (d if isinstance(d, (list, tuple)) else [d]) if p}
            out["layouts"].append({"kind": "alembic", "versions_dir": vd, "alembic_ini": inis,
                                   "revisions": len(graph), "heads": sorted(r for r in graph if r not in parents),
                                   "bases": sorted(r for r, d in graph.items() if not d)})
        # plain numbered migrations/ directories (not Django apps)
        plain = sorted({r.rsplit("/", 1)[0] for r in rel if r.split("/")[-2:-1] == ["migrations"]
                        and NUMBERED.match(r.rsplit("/", 1)[1]) and not DJANGO_MIGRATION.match(r.rsplit("/", 1)[1])
                        and "/tests/" not in r})
        for d in plain:
            files = sorted((int(NUMBERED.match(r.rsplit("/", 1)[1]).group(1)), r) for r in rel
                           if r.startswith(d + "/") and "/" not in r[len(d) + 1:]
                           and NUMBERED.match(r.rsplit("/", 1)[1]))
            out["layouts"].append({"kind": "numbered_dir", "dir": d, "files": len(files),
                                   "highest": files[-1][1] if files else None,
                                   "plugin": "/plugins/" in f"/{d}/"})
        return out


def apply_frozen_rule(layout: dict[str, Any], package: str) -> dict[str, Any]:
    """The migration-area rule exactly as frozen; anything it doesn't define needs a ruling."""
    kinds = {lay["kind"] for lay in layout["layouts"]}
    if "alembic" in kinds:
        return {"verdict": "NEEDS_PROTOCOL_RULING", "reason": "Alembic revisions are hash-named: the frozen "
                "'highest-numbered file' is undefined for them"}
    django = [lay for lay in layout["layouts"] if lay["kind"] == "django"]
    if django:
        dirs = django[0]["app_migration_dirs"]
        core = [d for d in dirs if d == package]
        if core:
            return {"verdict": "SELECTED", "area": f"{core[0]}/migrations (highest-numbered)"}
        return {"verdict": "NEEDS_PROTOCOL_RULING", "reason": f"{len(dirs)} app-level Django migrations/ "
                "directories and none at the core package: the frozen rule doesn't say which is 'core'"}
    core_dirs = [lay for lay in layout["layouts"] if lay["kind"] == "numbered_dir" and not lay["plugin"]
                 and lay["dir"] == f"{package}/migrations"]
    if core_dirs:
        return {"verdict": "SELECTED", "area": core_dirs[0]["highest"], "rule": "highest-numbered file of the core "
                "package's migrations/ directory, plugins excluded, plus the module that applies migrations"}
    return {"verdict": "NONE_BY_RULE", "reason": "no migrations/ directory in the core package"}


# ------------------------------------------------------------------ one candidate


def fetch(url: str, timeout: int = 120) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "deployspecter-corpus-select/1"})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - https URLs from PyPI only
        return r.read()


def select(c: dict[str, Any], work: Path) -> dict[str, Any]:
    name = c["pypi"].lower()
    exposure: list[str] = []
    row: dict[str, Any] = {"pypi": name, "version": c["version"], "repository": c.get("repository"),
                           "tag": c.get("tag")}
    if name in HELD_OUT:
        return {**row, "role": "EXCLUDED_HELD_OUT", "reason": "frozen protocol held-out application; nothing fetched",
                "exposure": ["name only"]}
    meta = json.loads(fetch(f"https://pypi.org/pypi/{name}/{c['version']}/json"))
    exposure.append("PyPI JSON metadata for this version")
    info = meta["info"]
    sdist = next((u for u in meta["urls"] if u["packagetype"] == "sdist"), None)
    if sdist is None:
        raise Refused(f"{name} {c['version']}: no sdist")
    data = fetch(sdist["url"])
    got = hashlib.sha256(data).hexdigest()
    if got != sdist["digests"]["sha256"]:
        raise Refused(f"{name}: the sdist's sha256 isn't PyPI's")
    archive = work / sdist["filename"]
    archive.write_bytes(data)
    commit = None
    if c.get("repository") and c.get("tag"):
        refs = subprocess.run(["git", "ls-remote", "--tags", c["repository"]], capture_output=True, text=True,
                              timeout=120).stdout
        peeled = {line.split("\t")[1]: line.split("\t")[0] for line in refs.splitlines() if "\t" in line}
        commit = peeled.get(f"refs/tags/{c['tag']}^{{}}") or peeled.get(f"refs/tags/{c['tag']}")
        exposure.append("git ls-remote of the repository's tags")
        if c.get("commit") and commit != c["commit"]:
            raise Refused(f"{name}: tag {c['tag']} is {commit}, not the pinned {c['commit']}")
    layout = discover(archive, exposure)
    package = re.sub(r"[-.]", "_", name)
    deps = classify_dependencies(info.get("requires_dist") or [])
    licence = classify_licence(info)
    role = ("DEVELOPMENT" if name in DEVELOPMENT else
            "PROSPECTIVE_CANDIDATE" if deps["psycopg3"] != "absent" and licence["osi_approved"] else "EXCLUDED_BY_RULE")
    reasons = []
    if deps["psycopg3"] == "absent":
        reasons.append("no psycopg 3 declared")
    if not licence["osi_approved"]:
        reasons.append("licence not shown OSI-approved")
    if deps["psycopg3"].startswith("extra_only") and role == "PROSPECTIVE_CANDIDATE":
        reasons.append("psycopg 3 only under an extra: eligibility needs a protocol ruling")
    return {**row, "role": role, "development_reason": DEVELOPMENT.get(name), "reasons": reasons,
            "sdist": {"filename": sdist["filename"], "sha256": got, "upload_time": sdist.get("upload_time_iso_8601")},
            "tag_commit": commit, "licence": licence, "requires_python": info.get("requires_python"),
            "dependencies": deps, "migration_layout": layout, "migration_area": apply_frozen_rule(layout, package),
            "defect_labels": "none read; none collected", "exposure": exposure}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidates", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    candidates = json.loads(a.candidates.read_text())
    a.out.mkdir(parents=True, exist_ok=False)
    rows = []
    with tempfile.TemporaryDirectory(prefix="ds_corpus_") as work:
        for c in candidates:
            try:
                rows.append(select(c, Path(work)))
            except Refused as exc:
                rows.append({"pypi": c["pypi"], "version": c["version"], "role": "REFUSED", "reason": str(exc)})
    report = {"tool": "research_tools/corpus_select.py",
              "tool_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "rules": "CORPUS_SHORTLIST.json v2 (frozen), applied literally; no new rule",
              "candidates_sha256": hashlib.sha256(a.candidates.read_bytes()).hexdigest(), "results": rows}
    (a.out / "selection_report.json").write_text(json.dumps(report, indent=1, default=str) + "\n")
    for r in rows:
        print(f"{r['pypi']:10} {r['role']:22} {r.get('migration_area', {}).get('verdict', '-'):22} "
              f"{r.get('dependencies', {}).get('psycopg3', '-')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
