"""Solo r3 design: a seeded mutant generator for the six operators of ``benchmark_prep/MUTANTS.md``, sealed store.

    python research_tools/solo/mutants.py generate    --app NAME (--tree DIR | --archive R.tar) --store DIR
    python research_tools/solo/mutants.py materialize --app NAME --id ID (--tree DIR | --archive R.tar) --out DIR
    python research_tools/solo/mutants.py decode      --app NAME --id ID --results RESULTS.json --store DIR

``generate`` enumerates every site per operator, orders the sites by
``sha256("<mutant seed>:deployspecter-ra-r3:mutation:<app>:<op>:<path>:<line>:<col>")`` and keeps the first 5 per
operator (MUTANTS.md's ordering, salted with the sealed seed so the investigator can't predict the sample). Each
mutant is written **encrypted** to ``STORE/<app>/<id>.sealed``; ``STORE/<app>/INDEX.json`` holds ids and record hashes
only, without operators, paths or lines. It prints counts and hashes, nothing else.

``materialize`` writes a mutated copy of the tree for the runner and prints only the id and the tree's hash.
``decode`` prints a mutant's record, and **refuses** until RESULTS.json exists and gives that mutant a final verdict
(``VIOLATED``, ``EQUIVALENT-SUSPECT`` or ``UNREACHABLE``, from ``auto_oracles.py``). Every materialize and decode is
appended to ``STORE/<app>/ACCESS.log``.

Operators (sites found mechanically; a ``.py`` mutant that no longer parses is dropped and counted):
* **RM1** remove a row-locking clause (``FOR UPDATE``/``FOR SHARE``/``FOR NO KEY UPDATE``/``FOR KEY SHARE``, with
  ``OF``/``SKIP LOCKED``/``NOWAIT``), or only ``SKIP LOCKED``, in SQL text; remove a Django ``.select_for_update(...)``
  call, or its ``skip_locked`` argument.
* **RM2** remove an idempotency guard in SQL: an ``ON CONFLICT ... DO ...`` clause, or ``NOT EXISTS (...)`` (made
  ``TRUE``).
* **RM3** widen a backfill predicate: drop one top-level ``AND`` conjunct of the ``WHERE`` of an ``UPDATE`` or
  ``INSERT ... SELECT`` in a file whose path names a migration or backfill.
* **RM4** reorder the two writes of a dual write: swap two consecutive statements that are both write calls
  (``execute``, ``save``, ``create``, ...).
* **RM5** drop one branch of a trigger function: a ``TG_OP = '<op>'`` condition becomes ``FALSE``.
* **RM6** make a conditional ``UPDATE`` unconditional: drop a top-level ``WHERE`` conjunct that reads a state column
  (``status``, ``state``, ``version``, ``claim*``, ``lock*``, ``attempts``, ``heartbeat*``).
SQL text is the source of string literals in ``.py`` files, and the whole of ``.sql`` files. The Django ORM is covered
by RM1 only; ORM-level guards and conditional updates aren't mutated (a stated limit).
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import io
import json
import re
import shutil
import sys
import tarfile
import tempfile
import time
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sealed  # noqa: E402

OPERATORS = ("RM1", "RM2", "RM3", "RM4", "RM5", "RM6")
PER_OPERATOR = 5
FINAL_VERDICTS = ("VIOLATED", "EQUIVALENT-SUSPECT", "UNREACHABLE")
FREEZE = Path(__file__).resolve().parents[2] / "docs/experimental_mvp/osf/FREEZE.json"
WRITE_CALLS = {"execute", "executemany", "save", "create", "bulk_create", "update", "delete", "insert", "add",
               "put", "enqueue", "copy"}
STATE_COLUMN = re.compile(r"\b(status|state|version|claim\w*|lock\w*|attempts|heartbeat\w*)\b", re.I)
F = re.I | re.S

LOCK_CLAUSE = re.compile(r"\s+FOR\s+(?:NO\s+KEY\s+UPDATE|UPDATE|KEY\s+SHARE|SHARE)"
                         r"(?:\s+OF\s+[\w\".]+(?:\s*,\s*[\w\".]+)*)?(?:\s+(?:SKIP\s+LOCKED|NOWAIT))?", F)
SKIP_LOCKED = re.compile(r"\s+SKIP\s+LOCKED", F)
NOT_EXISTS = re.compile(r"\bNOT\s+EXISTS\s*\(", F)
TG_OP = re.compile(r"TG_OP\s*=\s*'(?:INSERT|UPDATE|DELETE)'", F)
UPDATE_WHERE = re.compile(r"\bUPDATE\b.*?\bSET\b.*?\bWHERE\b", F)
INSERT_SELECT_WHERE = re.compile(r"\bINSERT\s+INTO\b.*?\bSELECT\b.*?\bWHERE\b", F)
LITERAL_OPEN = re.compile(r"[rRbBuUfF]*(\"\"\"|'''|\"|')")
CLAUSE_END = re.compile(r"\b(RETURNING|ORDER\s+BY|GROUP\s+BY|LIMIT|ON\s+CONFLICT|FOR\s+UPDATE|FOR\s+SHARE)\b|;", F)


@dataclass(frozen=True)
class Site:
    op: str
    path: str
    line: int
    col: int
    start: int
    end: int
    replacement: str

    def key(self, seed: str, app: str) -> str:
        return hashlib.sha256(f"{seed}:deployspecter-ra-r3:mutation:{app}:{self.op}:{self.path}:{self.line}:"
                              f"{self.col}".encode()).hexdigest()


def _balanced_end(text: str, open_at: int) -> int | None:
    """Index just past the parenthesis that closes the one at ``open_at``."""
    depth = 0
    for i in range(open_at, len(text)):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return i + 1
    return None


def _on_conflict(q: str) -> re.Pattern[str]:
    stop = "|" + re.escape(q) if q else ""
    return re.compile(r"\s+ON\s+CONFLICT\b.*?\bDO\s+(?:NOTHING|UPDATE\s+SET\b.*?)(?=\s+RETURNING\b|\s*;" + stop
                      + r"|\Z)", F)


def _word_start(text: str, i: int) -> bool:
    return i == 0 or not (text[i - 1].isalnum() or text[i - 1] == "_")


def _conjuncts(text: str, start: int, q: str = "") -> list[tuple[int, int]]:
    """Top-level AND conjunct spans of the WHERE clause starting at ``start``. ``q`` is the Python literal's own
    delimiter (empty for a ``.sql`` file). SQL string literals ('...') are skipped unless ``'`` is that delimiter."""
    depth, i, end = 0, start, len(text)
    skip_sql_strings = not q.startswith("'")
    ands: list[int] = []
    while i < len(text):
        if q and text.startswith(q, i):
            end = i
            break
        ch = text[i]
        if ch == "'" and skip_sql_strings:
            j = text.find("'", i + 1)
            i = (j if j != -1 else len(text) - 1) + 1
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            if depth == 0:
                end = i
                break
            depth -= 1
        elif depth == 0:
            if ch == ";":
                end = i
                break
            if _word_start(text, i) and CLAUSE_END.match(text, i):
                end = i
                break
            if _word_start(text, i) and re.match(r"AND\b", text[i:i + 4], re.I):
                ands.append(i)
        i += 1
    spans, last = [], start
    for pos in ands:
        spans.append((last, pos))
        last = pos + 3
    spans.append((last, start + len(text[start:end].rstrip())))
    return [(a, b) for a, b in spans if text[a:b].strip()]


def _sql_sites(text: str, base: int, path: str, line_of, q: str = "") -> Iterator[Site]:
    def site(op: str, a: int, b: int, rep: str) -> Site:
        line, col = line_of(base + a)
        return Site(op, path, line, col, base + a, base + b, rep)

    for m in LOCK_CLAUSE.finditer(text):
        yield site("RM1", m.start(), m.end(), "")
    for m in SKIP_LOCKED.finditer(text):
        yield site("RM1", m.start(), m.end(), "")
    for m in _on_conflict(q).finditer(text):
        yield site("RM2", m.start(), m.end(), "")
    for m in NOT_EXISTS.finditer(text):
        e = _balanced_end(text, m.end() - 1)
        if e:
            yield site("RM2", m.start(), e, "TRUE")
    for m in TG_OP.finditer(text):
        yield site("RM5", m.start(), m.end(), "FALSE")
    backfill = bool(re.search(r"migrat|backfill", path, re.I))
    for pattern, ops in ((UPDATE_WHERE, ("RM3", "RM6")), (INSERT_SELECT_WHERE, ("RM3",))):
        for m in pattern.finditer(text):
            spans = _conjuncts(text, m.end(), q)
            if len(spans) < 2:
                continue
            for i, (a, b) in enumerate(spans):
                # drop conjunct i together with one adjacent AND
                if i + 1 < len(spans):
                    cut = (a, spans[i + 1][0])
                else:
                    prev_end = spans[i - 1][1]
                    cut = (prev_end, b)
                if "RM3" in ops and backfill:
                    yield site("RM3", cut[0], cut[1], " " if i + 1 < len(spans) else "")
                if "RM6" in ops and STATE_COLUMN.search(text[a:b]):
                    yield site("RM6", cut[0], cut[1], " " if i + 1 < len(spans) else "")


def _offsets(content: str) -> tuple[list[int], Any]:
    starts = [0]
    for i, ch in enumerate(content):
        if ch == "\n":
            starts.append(i + 1)

    def to_offset(line: int, col: int) -> int:
        # ast columns are UTF-8 byte offsets
        s = starts[line - 1]
        row = content[s:].split("\n", 1)[0]
        return s + len(row.encode()[:col].decode(errors="ignore"))

    def line_of(off: int) -> tuple[int, int]:
        lo, hi = 0, len(starts) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if starts[mid] <= off:
                lo = mid
            else:
                hi = mid - 1
        return lo + 1, off - starts[lo]

    return starts, (to_offset, line_of)


def file_sites(path: str, content: str) -> Iterator[Site]:
    _, (to_offset, line_of) = _offsets(content)
    if path.endswith(".sql"):
        yield from _sql_sites(content, 0, path, line_of)
        return
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.end_lineno is not None:
            a = to_offset(node.lineno, node.col_offset)
            b = to_offset(node.end_lineno, node.end_col_offset)
            seg = content[a:b]
            dm = LITERAL_OPEN.match(seg)
            yield from _sql_sites(seg, a, path, line_of, dm.group(1) if dm else "")
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.end_lineno is not None:
            if node.func.attr == "select_for_update":
                a = to_offset(node.lineno, node.col_offset)
                b = to_offset(node.end_lineno, node.end_col_offset)
                inner = node.func.value
                rep = content[to_offset(inner.lineno, inner.col_offset):to_offset(inner.end_lineno,
                                                                                  inner.end_col_offset)]
                line, col = line_of(a)
                yield Site("RM1", path, line, col, a, b, rep)
                for kw in node.keywords:
                    if kw.arg == "skip_locked" and kw.value.end_lineno is not None:
                        ka = to_offset(kw.lineno, kw.col_offset)
                        kb = to_offset(kw.value.end_lineno, kw.value.end_col_offset)
                        tail = re.match(r"\s*,", content[kb:])
                        kb = kb + tail.end() if tail else kb
                        head = re.search(r",\s*$", content[a:ka]) if not tail else None
                        ka = a + head.start() if head else ka
                        line, col = line_of(ka)
                        yield Site("RM1", path, line, col, ka, kb, "")
    lines = content.splitlines(keepends=True)
    line_start = [0]
    for ln in lines:
        line_start.append(line_start[-1] + len(ln))
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(body, list):
            continue
        for s1, s2 in zip(body, body[1:], strict=False):
            if _is_write(s1) and _is_write(s2) and s1.end_lineno < s2.lineno:
                block1 = lines[s1.lineno - 1:s1.end_lineno]
                between = lines[s1.end_lineno:s2.lineno - 1]
                block2 = lines[s2.lineno - 1:s2.end_lineno]
                if not block2[-1].endswith("\n") or not block1[-1].endswith("\n"):
                    continue  # the last line of the file; not worth a special case
                a, b = line_start[s1.lineno - 1], line_start[s2.end_lineno]
                yield Site("RM4", path, s1.lineno, 0, a, b, "".join(block2 + between + block1))


def _is_write(stmt: ast.stmt) -> bool:
    if not isinstance(stmt, ast.Expr):
        return False
    v = stmt.value.value if isinstance(stmt.value, ast.Await) else stmt.value
    if not isinstance(v, ast.Call):
        return False
    f = v.func
    name = f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else None
    return name in WRITE_CALLS


def tree_files(root: Path) -> dict[str, str]:
    out = {}
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        if p.is_file() and p.suffix in (".py", ".sql") and not re.search(r"(^|/)(tests?|docs|examples)/|(^|/)test_",
                                                                           rel):
            try:
                out[rel] = p.read_text()
            except UnicodeDecodeError:
                continue
    return out


def apply(content: str, s: Site) -> str:
    return content[:s.start] + s.replacement + content[s.end:]


def enumerate_sites(files: dict[str, str]) -> tuple[dict[str, list[Site]], int]:
    by_op: dict[str, list[Site]] = {op: [] for op in OPERATORS}
    dropped = 0
    seen = set()
    for path, content in files.items():
        for s in file_sites(path, content):
            k = (s.op, s.path, s.start, s.end, s.replacement)
            if k in seen:
                continue
            seen.add(k)
            if path.endswith(".py"):
                try:
                    ast.parse(apply(content, s))
                except SyntaxError:
                    dropped += 1
                    continue
            by_op[s.op].append(s)
    return by_op, dropped


def sample(by_op: dict[str, list[Site]], seed: str, app: str) -> list[Site]:
    out = []
    for op in OPERATORS:
        out += sorted(by_op[op], key=lambda s: s.key(seed, app))[:PER_OPERATOR]
    return out


def mutant_id(app: str, s: Site, content: str) -> str:
    return hashlib.sha256(json.dumps([app, asdict(s), hashlib.sha256(content.encode()).hexdigest()]).encode()
                          ).hexdigest()[:20]


def _tree_from(args: argparse.Namespace, tmp: Path) -> Path:
    if args.tree:
        return Path(args.tree)
    with tarfile.open(fileobj=io.BytesIO(Path(args.archive).read_bytes())) as tf:
        tf.extractall(tmp, filter="data")
    return tmp


def _seed(args: argparse.Namespace) -> str:
    expected = args.seed_sha256 or json.loads(FREEZE.read_text())["sealed_seeds"]["mutant_seed_sha256"]
    return sealed.load_seed("mutant_seed", expected, Path(args.seeds))


def cmd_generate(args: argparse.Namespace) -> dict[str, Any]:
    seed = _seed(args)
    store = Path(args.store) / args.app
    if store.exists():
        raise SystemExit(f"refused: {store} exists; a store is written once")
    with tempfile.TemporaryDirectory() as t:
        files = tree_files(_tree_from(args, Path(t)))
    by_op, dropped = enumerate_sites(files)
    chosen = sample(by_op, seed, args.app)
    index = []
    for s in chosen:
        mid = mutant_id(args.app, s, files[s.path])
        record = {"id": mid, "app": args.app, **asdict(s),
                  "original_sha256": hashlib.sha256(files[s.path].encode()).hexdigest(),
                  "mutated_sha256": hashlib.sha256(apply(files[s.path], s).encode()).hexdigest()}
        blob = sealed.seal(seed, mid, json.dumps(record, sort_keys=True).encode())
        sealed.write_private(store / f"{mid}.sealed", blob)
        index.append({"id": mid, "sealed_sha256": hashlib.sha256(blob).hexdigest()})
    index.sort(key=lambda r: r["id"])
    doc = {"app": args.app, "files_scanned": len(files), "mutants": index}
    sealed.write_private(store / "INDEX.json", json.dumps(doc, indent=1).encode())
    return {"app": args.app, "files_scanned": len(files),
            "sites_found": {op: len(v) for op, v in by_op.items()},
            "sampled": {op: min(PER_OPERATOR, len(v)) for op, v in by_op.items()},
            "dropped_unparseable": dropped, "mutants": len(index),
            "index_sha256": hashlib.sha256(json.dumps(doc, indent=1).encode()).hexdigest(),
            "ids": [r["id"] for r in index]}


def _record(args: argparse.Namespace, seed: str) -> dict[str, Any]:
    p = Path(args.store) / args.app / f"{args.id}.sealed"
    return json.loads(sealed.unseal(seed, args.id, p.read_bytes()))


def _log(args: argparse.Namespace, what: str) -> None:
    with open(Path(args.store) / args.app / "ACCESS.log", "a") as f:
        f.write(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "what": what,
                            "id": args.id}) + "\n")


def cmd_materialize(args: argparse.Namespace) -> dict[str, Any]:
    seed = _seed(args)
    rec = _record(args, seed)
    out = Path(args.out)
    if out.exists():
        raise SystemExit(f"refused: {out} exists")
    with tempfile.TemporaryDirectory() as t:
        src = _tree_from(args, Path(t))
        shutil.copytree(src, out)
    target = out / rec["path"]
    content = target.read_text()
    if hashlib.sha256(content.encode()).hexdigest() != rec["original_sha256"]:
        shutil.rmtree(out)
        raise SystemExit("refused: the tree isn't the one the mutant was generated from")
    site = Site(rec["op"], rec["path"], rec["line"], rec["col"], rec["start"], rec["end"], rec["replacement"])
    target.write_text(apply(content, site))
    _log(args, "materialize")
    h = hashlib.sha256()
    for p in sorted(out.rglob("*")):
        if p.is_file():
            h.update(p.relative_to(out).as_posix().encode() + b"\0" + p.read_bytes())
    return {"id": args.id, "tree_sha256": h.hexdigest()}


def cmd_decode(args: argparse.Namespace) -> dict[str, Any]:
    results = Path(args.results)
    if not results.exists():
        raise SystemExit("refused: no results file yet; a mutant is decoded only after its verdict is recorded")
    verdicts = json.loads(results.read_text())
    v = (verdicts.get(args.id) or {}).get("verdict")
    if v not in FINAL_VERDICTS:
        raise SystemExit(f"refused: the results file has no final verdict for {args.id}")
    rec = _record(args, _seed(args))
    _log(args, "decode")
    return {**rec, "verdict": v}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("generate", "materialize", "decode"):
        s = sub.add_parser(name)
        s.add_argument("--app", required=True)
        s.add_argument("--store", default=str(Path.home() / ".deployspecter-sealed" / "mutants"))
        s.add_argument("--seeds", default=str(sealed.DEFAULT_SEEDS))
        s.add_argument("--seed-sha256", help="default: FREEZE.json's sealed_seeds.mutant_seed_sha256")
        if name in ("generate", "materialize"):
            g = s.add_mutually_exclusive_group(required=True)
            g.add_argument("--tree")
            g.add_argument("--archive")
        if name in ("materialize", "decode"):
            s.add_argument("--id", required=True)
        if name == "materialize":
            s.add_argument("--out", required=True)
        if name == "decode":
            s.add_argument("--results", required=True)
    a = ap.parse_args(argv)
    try:
        out = {"generate": cmd_generate, "materialize": cmd_materialize, "decode": cmd_decode}[a.cmd](a)
    except sealed.SealError as e:
        print(f"refused: {e}", file=sys.stderr)
        return 3
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
