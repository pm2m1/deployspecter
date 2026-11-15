"""Solo r3 design: run a maintainer regression test's assertions as an oracle over a run's final database state.

    python research_tools/solo/maintainer_oracle.py check-spec --spec SPEC.json --test-file FILE
    python research_tools/solo/maintainer_oracle.py evaluate --spec SPEC.json --test-file FILE --report report.json

A **spec** (``ds-maintainer-spec/1``) transcribes the regression test that a case's fix commit added or changed:

* ``upstream``: repository, fix commit, the test file's path and **git blob id** at the fix commit, and the test
  functions that are the regression test;
* ``assertions``: **every** assertion in those functions, by line, each either
  ``{"kind": "final_state", "predicate": ...}`` (expressed over the run's final database state), or
  ``{"kind": "not_expressible", "reason": ...}`` with one of ``REASONS``;
* ``precondition`` (optional): a predicate that must hold for a verdict at all (otherwise ``NO_VERDICT``).

``check_spec`` parses the test file with ``ast`` and refuses a spec that leaves out an assertion, invents one, or
names a file whose blob id differs: no assertion can be dropped silently. ``compile_oracle`` **refuses** a case whose
spec expresses no assertion over final state, and logs the reasons. Otherwise the oracle is ``VIOLATED`` when any
expressed assertion fails, ``HOLDS`` when all hold, ``NO_VERDICT`` when the precondition fails.

Predicates are JSON, evaluated here without ``eval`` (``evaluate_predicate``):
``{"all": [p...]}``, ``{"any": [p...]}``, ``{"not": p}``, ``{"eq"|"ne"|"lt"|"le"|"gt"|"ge": [e, e]}``,
``{"in": [e, e]}``, ``{"forall"|"exists": {"rows": e, "as": "r", "where": p?, "check": p?}}``.
Expressions: ``{"lit": v}``, ``{"path": "final_state.jobs_detail"}`` or ``{"path": "r.state"}`` (a bound row),
``{"count": {"rows": e, "as": "r", "where": p?}}``, ``{"pluck": {"rows": e, "as": "r", "where": p?, "value": e,
"order_by": "field"?}}``, ``{"len": e}``, ``{"get": [mapping, key]}``.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

FORMAT = "ds-maintainer-spec/1"
REASONS = {
    "in_memory_return_value": "asserts on an object or value returned in memory, not on stored state",
    "mock": "asserts on a mock or a patched hook",
    "intermediate_state": "asserts on state between steps; only the final state is observed",
    "exception": "asserts that a call raises",
    "schema_added_by_fix": "reads a column or table that only the fixed revision has",
    "private_internals": "drives or reads private internals of the library",
    "not_in_final_state": "reads data the integration's final-state read doesn't capture",
}
UNUSED_REASONS = {
    "scenario_not_exercised": "the test drives a scenario the case's workload doesn't perform",
    "other_backend": "the test exercises a backend other than PostgreSQL (for example an in-memory one)",
}
VERDICTS = ("HOLDS", "VIOLATED", "NO_VERDICT")


class Refused(Exception):
    """The case can't use this maintainer test as its oracle; the message is the logged reason."""


def git_blob_id(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def _assert_nodes(fn: ast.AST) -> list[tuple[int, str]]:
    """(line, kind) of every assertion in a test function: ``assert``, ``self.assert*``, ``pytest.raises`` and
    mock ``.assert_*`` calls. Nested functions count too."""
    out: list[tuple[int, str]] = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Assert):
            out.append((node.lineno, "assert"))
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            name = node.func.attr
            base = node.func.value
            if name.startswith("assert") and isinstance(base, ast.Name) and base.id == "self":
                out.append((node.lineno, "self.assert"))
            elif name.startswith("assert_"):
                out.append((node.lineno, "mock.assert"))
            elif name == "raises" and isinstance(base, ast.Name) and base.id == "pytest":
                out.append((node.lineno, "pytest.raises"))
    return sorted(set(out))


def test_assertions(source: str, functions: list[str]) -> dict[int, str]:
    tree = ast.parse(source)
    found: dict[str, ast.AST] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in functions:
            found[node.name] = node
    missing = [f for f in functions if f not in found]
    if missing:
        raise Refused(f"test functions not found in the file: {missing}")
    lines = source.splitlines()
    return {ln: lines[ln - 1].strip() for f in functions for ln, _ in _assert_nodes(found[f])}


def check_spec(spec: dict[str, Any], test_file: bytes) -> list[str]:
    """Problems with a spec against the upstream test file; an empty list means it accounts for every assertion."""
    p: list[str] = []
    if spec.get("format") != FORMAT:
        p.append(f"format must be {FORMAT}")
    up = spec.get("upstream") or {}
    functions = up.get("test_functions") or []
    if not functions:
        return p + ["no test functions: the fix adds or changes no regression test"]
    if git_blob_id(test_file) != up.get("test_blob"):
        p.append("the test file's git blob id isn't the spec's")
    changed = up.get("changed_test_functions")
    unused = up.get("unused_test_functions") or {}
    if changed is not None:
        if set(functions) & set(unused):
            p.append("a test function is both used and unused")
        if set(functions) | set(unused) != set(changed):
            p.append("used and unused test functions must together be exactly the fix's changed test functions")
    for name, reason in unused.items():
        if reason not in UNUSED_REASONS:
            p.append(f"unused {name}: unknown reason {reason!r}")
    try:
        expected = test_assertions(test_file.decode(), functions)
    except (Refused, SyntaxError) as e:
        return p + [str(e)]
    listed: dict[int, dict[str, Any]] = {}
    for a in spec.get("assertions") or []:
        ln = a.get("line")
        if ln in listed:
            p.append(f"line {ln} listed twice")
        listed[ln] = a
        if a.get("kind") == "not_expressible" and a.get("reason") not in REASONS:
            p.append(f"line {ln}: unknown reason {a.get('reason')!r}")
        if a.get("kind") == "final_state" and not isinstance(a.get("predicate"), dict):
            p.append(f"line {ln}: a final_state assertion needs a predicate")
        if a.get("kind") not in ("final_state", "not_expressible"):
            p.append(f"line {ln}: kind must be final_state or not_expressible")
    for ln, text in expected.items():
        if ln not in listed:
            p.append(f"line {ln} isn't accounted for: {text[:80]}")
        elif " ".join(str(listed[ln].get("source", "")).split()) != " ".join(text.split()):
            p.append(f"line {ln}: the spec's source text isn't the file's")
    for ln in listed:
        if ln not in expected:
            p.append(f"line {ln} isn't an assertion in the named test functions")
    return p


# ---- predicate evaluation (JSON, no eval) ----

def _resolve(path: str, env: dict[str, Any]) -> Any:
    head, *rest = path.split(".")
    if head not in env:
        raise KeyError(f"unknown name {head!r}")
    cur = env[head]
    for part in rest:
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list) and part.isdigit():
            cur = cur[int(part)] if int(part) < len(cur) else None
        else:
            return None
    return cur


def _rows(spec: dict[str, Any], env: dict[str, Any]) -> list[tuple[dict[str, Any], Any]]:
    rows = evaluate_expr(spec["rows"], env) or []
    if not isinstance(rows, list):
        raise TypeError("rows must evaluate to a list")
    name = spec.get("as", "r")
    out = []
    for r in rows:
        e = {**env, name: r}
        if "where" not in spec or evaluate_predicate(spec["where"], e):
            out.append((e, r))
    return out


def evaluate_expr(e: Any, env: dict[str, Any]) -> Any:
    if not isinstance(e, dict) or len(e) != 1:
        raise ValueError(f"an expression is a one-key object: {e!r}")
    (op, arg), = e.items()
    if op == "lit":
        return arg
    if op == "path":
        return _resolve(arg, env)
    if op == "count":
        return len(_rows(arg, env))
    if op == "get":
        obj, key = (evaluate_expr(x, env) for x in arg)
        return obj.get(str(key)) if isinstance(obj, dict) else None
    if op == "len":
        v = evaluate_expr(arg, env)
        return len(v) if v is not None else None
    if op == "pluck":
        rows = _rows(arg, env)
        if "order_by" in arg:
            rows.sort(key=lambda t: (t[1].get(arg["order_by"]) is None, t[1].get(arg["order_by"])))
        return [evaluate_expr(arg["value"], en) for en, _ in rows]
    raise ValueError(f"unknown expression {op!r}")


def evaluate_predicate(p: Any, env: dict[str, Any]) -> bool:
    if not isinstance(p, dict) or len(p) != 1:
        raise ValueError(f"a predicate is a one-key object: {p!r}")
    (op, arg), = p.items()
    if op == "all":
        return all(evaluate_predicate(x, env) for x in arg)
    if op == "any":
        return any(evaluate_predicate(x, env) for x in arg)
    if op == "not":
        return not evaluate_predicate(arg, env)
    if op in ("eq", "ne", "lt", "le", "gt", "ge", "in"):
        a, b = (evaluate_expr(x, env) for x in arg)
        if op == "eq":
            return a == b
        if op == "ne":
            return a != b
        if op == "in":
            return b is not None and a in b
        if a is None or b is None:
            return False
        return {"lt": a < b, "le": a <= b, "gt": a > b, "ge": a >= b}[op]
    if op in ("forall", "exists"):
        rows = _rows(arg, env)
        checks = (evaluate_predicate(arg["check"], en) if "check" in arg else True for en, _ in rows)
        return all(checks) if op == "forall" else any(checks)
    raise ValueError(f"unknown predicate {op!r}")


class MaintainerOracle:
    def __init__(self, spec: dict[str, Any]):
        self.spec = spec
        self.expressed = [a for a in spec.get("assertions") or [] if a.get("kind") == "final_state"]
        self.not_expressed = [a for a in spec.get("assertions") or [] if a.get("kind") == "not_expressible"]

    def evaluate(self, final_state: dict[str, Any], raw_results: dict[str, Any]) -> dict[str, Any]:
        env = {"final_state": final_state or {}, "raw_results": raw_results or {}}
        pre = self.spec.get("precondition")
        if pre is not None:
            try:
                if not evaluate_predicate(pre, env):
                    return {"verdict": "NO_VERDICT", "why": "precondition"}
            except (KeyError, TypeError, ValueError) as e:
                return {"verdict": "NO_VERDICT", "why": f"precondition error: {e}"}
        failed = []
        for a in self.expressed:
            try:
                ok = evaluate_predicate(a["predicate"], env)
            except (KeyError, TypeError, ValueError) as e:
                return {"verdict": "NO_VERDICT", "why": f"line {a['line']}: {e}"}
            if not ok:
                failed.append(a["line"])
        return {"verdict": "VIOLATED" if failed else "HOLDS", "failed_lines": failed,
                "expressed": [a["line"] for a in self.expressed],
                "not_expressed": {a["line"]: a["reason"] for a in self.not_expressed}}

    def check(self, final_state: dict[str, Any], raw_results: dict[str, Any]) -> str:
        return self.evaluate(final_state, raw_results)["verdict"]


def compile_oracle(spec: dict[str, Any], test_file: bytes, log: list[dict[str, Any]] | None = None) -> MaintainerOracle:
    """The case's oracle, or ``Refused`` (appended to ``log``) when the spec doesn't account for the test or expresses
    none of its assertions over final state."""
    case = spec.get("case_id")
    problems = check_spec(spec, test_file)
    if problems:
        reason = "; ".join(problems)
    else:
        o = MaintainerOracle(spec)
        if o.expressed:
            if log is not None:
                log.append({"case_id": case, "admitted": True, "expressed": len(o.expressed),
                            "not_expressed": {str(a["line"]): a["reason"] for a in o.not_expressed}})
            return o
        reason = "no assertion is expressible over the final state: " + ", ".join(
            sorted({a["reason"] for a in o.not_expressed}))
    if log is not None:
        log.append({"case_id": case, "admitted": False, "reason": reason})
    raise Refused(reason)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("check-spec", "evaluate"):
        s = sub.add_parser(name)
        s.add_argument("--spec", type=Path, required=True)
        s.add_argument("--test-file", type=Path, required=True)
        if name == "evaluate":
            s.add_argument("--report", type=Path, required=True, help="a run's report.json")
    a = ap.parse_args(argv)
    spec = json.loads(a.spec.read_text())
    log: list[dict[str, Any]] = []
    try:
        oracle = compile_oracle(spec, a.test_file.read_bytes(), log)
    except Refused:
        print(json.dumps(log[-1], indent=1))
        return 3
    if a.cmd == "check-spec":
        print(json.dumps(log[-1], indent=1))
        return 0
    rep = json.loads(a.report.read_text())
    print(json.dumps(oracle.evaluate(rep.get("final_state") or {}, rep.get("raw_results") or {}), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
