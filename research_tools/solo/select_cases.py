"""Solo r3 design: the frozen, seeded case selection that replaces the human curator.

    python research_tools/solo/select_cases.py fetch  --mirrors DIR
    python research_tools/solo/select_cases.py select --mirrors DIR --registration REGISTRATION.json --out DIR \
        [--s4-verdicts S4.json] [--refused REFUSED.json]

Inputs are frozen: the candidate list and rules (``docs/experimental_mvp/solo/CANDIDATES_FROZEN.json``), the sealed
selection seed (only its sha256 is committed, in ``osf/FREEZE.json``), and this code. ``select`` refuses to run until
REGISTRATION.json exists and names the sha256 of the FREEZE.json it was registered with.

Procedure (deterministic given the seed and the mirrors):
1. Applications in ascending ``sha256("<seed>:solo-selection:app:<owner>/<repo>")``.
2. Candidate commits of an application: non-merge commits on the default branch inside the window whose message
   matches the frozen topic **and** fix expressions (``research_tools/nbc/scan.py``'s ``TOPIC`` and ``FIX``) and that
   change a non-test ``.py`` or ``.sql`` file. Ordered by ``sha256("<seed>:solo-selection:commit:<repo>:<commit>")``.
3. Screening, at most ``per_app_screen_cap`` per application, each candidate in order; the first failing check is the
   skip code:
   * ``root_commit``; ``driver`` (the parent revision declares neither psycopg 3 nor, for rule S4, psycopg2 with
     Django 4.2 or later); ``s4_failed`` (the application failed its S4 check);
   * ``no_regression_test``: the fix adds or changes no test function (``test_*``) containing an assertion;
   * ``adapter_refused``: listed in REFUSED.json (``maintainer_oracle.py`` refused the case; the entry's
     ``reason``, for example ``oracle_depends_on_tool``, is copied into the skip record);
   * ``app_cap``: the application already has ``per_app_admit_cap`` admitted cases.
   Otherwise the case is **admitted**, with its test file, blob id and changed test functions.
4. Stop when at least ``target_cases`` are admitted from at least ``min_apps`` applications, or after
   ``screen_budget`` screens.

It never prints or stores a commit message, a diff or test source: only commit ids, paths, function names and codes.
An S4 application without a verdict stops the selection (exit 4) and names it, so that the investigator runs the S4
check and re-runs ``select``; earlier decisions don't change.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(REPO / "research_tools" / "nbc"))
import maintainer_oracle as mo  # noqa: E402
import scan  # noqa: E402  (frozen TOPIC, FIX and PSYCOPG3 expressions)
import sealed  # noqa: E402

CANDIDATES = REPO / "docs/experimental_mvp/solo/CANDIDATES_FROZEN.json"
FREEZE = REPO / "docs/experimental_mvp/osf/FREEZE.json"
TEST_PATH = re.compile(r"(^|/)(tests?|testing)/.*\.py$|(^|/)test_[^/]*\.py$|_tests?\.py$")
NON_SOURCE = re.compile(r"(^|/)(tests?|testing|docs|examples)/|(^|/)test_[^/]*$")
PSYCOPG2 = re.compile(r"psycopg2")
DJANGO = re.compile(r"""(?i)\bdjango\s*(?:\[[^\]]*\])?\s*(==|>=|~=|>)\s*(\d+)\.(\d+)""")
PACKAGING = re.compile(r"(^|/)(pyproject\.toml|setup\.py|setup\.cfg|requirements[^/]*\.(txt|in)|"
                       r"requirements/[^/]+\.(txt|in))$")


class Stop(Exception):
    pass


def git(repo: Path, *a: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True, check=True).stdout


def order_key(seed: str, kind: str, *parts: str) -> str:
    return hashlib.sha256(f"{seed}:solo-selection:{kind}:{':'.join(parts)}".encode()).hexdigest()


def candidates(mirror: Path, rules: dict[str, Any]) -> list[str]:
    log = git(mirror, "log", "--no-merges", "--first-parent", f"--since={rules['window_start']}",
              f"--until={rules['window_end']}", "--format=%H%x00%s%x00%B%x1e", "HEAD")
    out = []
    for rec in log.split("\x1e"):
        rec = rec.strip("\n")
        if not rec:
            continue
        commit, subject, body = (rec.split("\x00") + ["", ""])[:3]
        if not (scan.TOPIC.search(subject + "\n" + body) and scan.FIX.search(subject)):
            continue
        files = git(mirror, "diff-tree", "--no-commit-id", "--name-only", "-r", "--root", commit).split()
        if any(f.endswith((".py", ".sql")) and not NON_SOURCE.search(f) for f in files):
            out.append(commit)
    return out


def driver(mirror: Path, rev: str) -> str:
    """psycopg3, s4 (psycopg2 with Django >= 4.2), or none, from the packaging files declared at ``rev``."""
    names = [n for n in git(mirror, "ls-tree", "-r", "--name-only", rev).splitlines()
             if PACKAGING.search(n) and n.count("/") <= 3]
    text = "\n".join(git(mirror, "show", f"{rev}:{n}") for n in names)
    if scan.PSYCOPG3.search(text):
        return "psycopg3"
    if PSYCOPG2.search(text):
        for m in DJANGO.finditer(text):
            if (int(m.group(2)), int(m.group(3))) >= (4, 2):
                return "s4"
    return "none"


def changed_test_functions(mirror: Path, parent: str, commit: str) -> list[dict[str, Any]]:
    """Test files the fix adds or changes, with the ``test_*`` functions that overlap added lines and contain an
    assertion. Reads the test source only to parse it; nothing of it is printed or stored."""
    out = []
    for line in git(mirror, "diff", "--name-status", parent, commit).splitlines():
        status, *paths = line.split("\t")
        path = paths[-1]
        if status[0] not in "AM" or not TEST_PATH.search(path):
            continue
        added: set[int] = set()
        for h in re.finditer(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", git(mirror, "diff", "-U0", parent, commit,
                                                                               "--", path), re.M):
            start, n = int(h.group(1)), int(h.group(2) or 1)
            added.update(range(start, start + n))
        src = git(mirror, "show", f"{commit}:{path}")
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        fns = sorted({n.name for n in ast.walk(tree)
                      if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith("test")
                      and added & set(range(n.lineno, (n.end_lineno or n.lineno) + 1)) and mo._assert_nodes(n)})
        if fns:
            out.append({"test_path": path, "test_blob": mo.git_blob_id(src.encode()), "test_functions": fns})
    return out


def select(mirrors: Path, seed: str, rules: dict[str, Any], s4: dict[str, str],
           refused: set[tuple[str, str]] | dict[tuple[str, str], str]
           ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    apps = sorted(rules["applications"], key=lambda a: order_key(seed, "app", a["repo"]))
    admitted: list[dict[str, Any]] = []
    skips: list[dict[str, Any]] = []
    screens = 0

    def done() -> bool:
        return (len(admitted) >= rules["target_cases"]
                and len({c["repo"] for c in admitted}) >= rules["min_apps"]) or screens >= rules["screen_budget"]

    for app in apps:
        if done():
            break
        repo = app["repo"]
        mirror = mirrors / (repo.replace("/", "__") + ".git")
        if not mirror.exists():
            skips.append({"repo": repo, "commit": None, "code": "no_mirror"})
            continue
        cands = sorted(candidates(mirror, rules), key=lambda c: order_key(seed, "commit", repo, c))
        for commit in cands[: rules["per_app_screen_cap"]]:
            if done():
                break
            screens += 1
            parents = git(mirror, "rev-list", "--parents", "-n", "1", commit).split()[1:]
            code = None
            if not parents:
                code = "root_commit"
            else:
                d = driver(mirror, parents[0])
                if d == "none":
                    code = "driver"
                elif d == "s4":
                    if repo not in s4:
                        raise Stop(f"S4 verdict needed for {repo} (run the S4 check, record it, re-run select)")
                    if s4[repo] != "pass":
                        code = "s4_failed"
            tests = [] if code else changed_test_functions(mirror, parents[0], commit)
            if not code and not tests:
                code = "no_regression_test"
            if not code and (repo, commit) in refused:
                code = "adapter_refused"
            if not code and sum(1 for c in admitted if c["repo"] == repo) >= rules["per_app_admit_cap"]:
                code = "app_cap"
            if code:
                skip = {"repo": repo, "commit": commit, "code": code}
                if code == "adapter_refused" and isinstance(refused, dict) and refused[(repo, commit)]:
                    skip["reason"] = refused[(repo, commit)]
                skips.append(skip)
                continue
            admitted.append({"repo": repo, "fix_commit": commit, "parent": parents[0], "driver": d,
                             "screen": screens, "tests": tests})
    counts = {"screens": screens, "admitted": len(admitted), "apps": len({c["repo"] for c in admitted}),
              "skips": {k: sum(1 for s in skips if s["code"] == k) for k in sorted({s["code"] for s in skips})}}
    return admitted, skips, counts


def _registered(registration: Path, freeze: Path) -> None:
    if not registration.exists():
        raise SystemExit("refused: no registration record; register on OSF first (osf/REGISTER_NOW.md)")
    reg = json.loads(registration.read_text())
    want = hashlib.sha256(freeze.read_bytes()).hexdigest()
    if not reg.get("osf_url") or reg.get("freeze_sha256") != want:
        raise SystemExit("refused: the registration record doesn't name this FREEZE.json's sha256 and an OSF URL")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch")
    f.add_argument("--mirrors", type=Path, required=True)
    f.add_argument("--candidates", type=Path, default=CANDIDATES)
    s = sub.add_parser("select")
    s.add_argument("--mirrors", type=Path, required=True)
    s.add_argument("--out", type=Path, required=True)
    s.add_argument("--registration", type=Path, required=True)
    s.add_argument("--candidates", type=Path, default=CANDIDATES)
    s.add_argument("--freeze", type=Path, default=FREEZE)
    s.add_argument("--seeds", type=Path, default=sealed.DEFAULT_SEEDS)
    s.add_argument("--s4-verdicts", type=Path)
    s.add_argument("--refused", type=Path)
    a = ap.parse_args(argv)
    rules = json.loads(a.candidates.read_text())
    if a.cmd == "fetch":
        a.mirrors.mkdir(parents=True, exist_ok=True)
        for app in rules["applications"]:
            dest = a.mirrors / (app["repo"].replace("/", "__") + ".git")
            if not dest.exists():
                subprocess.run(["git", "clone", "--quiet", "--mirror", app["url"], str(dest)], check=True)
            print(json.dumps({"repo": app["repo"], "mirror": str(dest)}))
        return 0
    _registered(a.registration, a.freeze)
    expected = json.loads(a.freeze.read_text())["sealed_seeds"]["selection_seed_sha256"]
    seed = sealed.load_seed("selection_seed", expected, a.seeds)
    s4 = json.loads(a.s4_verdicts.read_text()) if a.s4_verdicts else {}
    refused = ({(r["repo"], r["fix_commit"]): r.get("reason", "") for r in json.loads(a.refused.read_text())}
               if a.refused else {})
    try:
        admitted, skips, counts = select(a.mirrors, seed, rules, s4, refused)
    except Stop as e:
        print(f"stopped: {e}", file=sys.stderr)
        return 4
    a.out.mkdir(parents=True, exist_ok=False)
    doc = {"format": "ds-solo-selection/1", "candidates_sha256": hashlib.sha256(a.candidates.read_bytes()).hexdigest(),
           "selection_seed_sha256": expected, "counts": counts, "admitted": admitted}
    (a.out / "SELECTION.json").write_text(json.dumps(doc, indent=1) + "\n")
    (a.out / "SKIPS.jsonl").write_text("".join(json.dumps(x, sort_keys=True) + "\n" for x in skips))
    print(json.dumps(counts, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
