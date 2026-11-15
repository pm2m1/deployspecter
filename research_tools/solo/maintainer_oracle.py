"""Solo r3 design: run a maintainer regression test's assertions as an oracle over a run's final database state.

    python research_tools/solo/maintainer_oracle.py check-spec --spec SPEC.json --test-file FILE
    python research_tools/solo/maintainer_oracle.py evaluate --spec SPEC.json --test-file FILE --report report.json

A **spec** (``ds-maintainer-spec/2``; ``/1`` is still read) transcribes the regression test that a case's fix
commit added or changed:

* ``upstream``: repository, fix commit, the test file's path and **git blob id** at the fix commit, and the test
  functions that are the regression test;
* ``assertions``: **every** assertion in those functions, by line, each either
  ``{"kind": "final_state", "predicate": ...}`` (expressed over the run's final database state), or
  ``{"kind": "not_expressible", "reason": ...}`` with one of ``REASONS``;
* ``precondition`` (optional): a predicate that must hold for a verdict at all (otherwise ``NO_VERDICT``);
* (``/2``) ``sources``: ``final_state`` maps each final-state key to ``"application"`` (the application's own tables,
  read with plain SQL) or ``"deployspecter"`` (anything DeploySpecter writes or computes, such as a handler table);
  ``raw_results_workload_record`` lists the ``raw_results`` paths that are the workload's record of the operations it
  issued (the ids it pushed or cancelled and those calls' return values), the analogue of the test's own variables.
  Any other ``raw_results`` path counts as DeploySpecter's;
* (``/2``) every ``final_state`` assertion carries ``uses_deployspecter_code``. ``check_spec`` computes which sources
  the predicate reads and refuses ``false`` when it reads DeploySpecter's (``true`` is always accepted).

``/2`` also verifies the not-expressible reasons that can be checked mechanically: ``mock`` (the assertion is a mock
call ``assert_*await*``/``assert_*call*``), ``exception`` (the line is ``pytest.raises`` or inside its ``with``) and
``in_memory_return_value`` (the asserted expression makes no call, and every local name in it is bound once, from a
call's return value). A checkable reason that fails is a refusal. Other reasons are reported as
``investigator_judgement``. ``coverage`` gives the per-case record: total assertions, expressed, expressed without
DeploySpecter code, not-expressible by reason, mechanically verified, investigator judgement, and whether the case
is in the strict subset (no expressed assertion uses DeploySpecter code and no reason is investigator judgement).
A case whose expressed assertions **all** use DeploySpecter code is refused (``oracle_depends_on_tool``).

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
import re
import sys
from pathlib import Path
from typing import Any

FORMAT = "ds-maintainer-spec/2"
FORMATS = ("ds-maintainer-spec/1", "ds-maintainer-spec/2")
MECHANICAL = ("mock", "exception", "in_memory_return_value")
MOCK_ASSERT = re.compile(r"^assert_\w*(await|call)\w*$")
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


def _functions(source: str, functions: list[str]) -> dict[str, ast.AST]:
    tree = ast.parse(source)
    found: dict[str, ast.AST] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in functions:
            found[node.name] = node
    missing = [f for f in functions if f not in found]
    if missing:
        raise Refused(f"test functions not found in the file: {missing}")
    return found


def test_assertions(source: str, functions: list[str]) -> dict[int, str]:
    found = _functions(source, functions)
    lines = source.splitlines()
    return {ln: lines[ln - 1].strip() for f in functions for ln, _ in _assert_nodes(found[f])}


def _bindings(fn: ast.AST) -> dict[str, list[ast.AST]]:
    """name -> the values it is bound from in the function (None for a binding that isn't an assignment)."""
    out: dict[str, list[Any]] = {}

    def bind(target: ast.AST, value: Any) -> None:
        for n in ast.walk(target):
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
                out.setdefault(n.id, []).append(value)

    for node in ast.walk(fn):
        if isinstance(node, ast.Assign):
            for tg in node.targets:
                bind(tg, node.value)
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)) and node.value is not None:
            bind(node.target, node.value if isinstance(node, ast.AnnAssign) else None)
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            bind(node.target, None)
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                if item.optional_vars is not None:
                    bind(item.optional_vars, None)
        elif isinstance(node, ast.NamedExpr):
            bind(node.target, node.value)
    return out


def _is_call(v: Any) -> bool:
    return isinstance(v, ast.Call) or (isinstance(v, ast.Await) and isinstance(v.value, ast.Call))


def verify_reason(fn: ast.AST, line: int, reason: str) -> tuple[str, str]:
    """("verified" | "failed" | "investigator_judgement", detail) for a not-expressible reason at ``line``."""
    if reason not in MECHANICAL:
        return "investigator_judgement", f"{reason} can't be checked mechanically"
    nodes = [n for n in ast.walk(fn) if getattr(n, "lineno", None) == line]
    if reason == "mock":
        ok = any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and MOCK_ASSERT.match(n.func.attr)
                 and not (isinstance(n.func.value, ast.Name) and n.func.value.id == "self") for n in nodes)
        return ("verified", "a mock assertion call") if ok else ("failed", "no mock assert_*await*/*call* call here")
    if reason == "exception":
        for w in ast.walk(fn):
            if isinstance(w, (ast.With, ast.AsyncWith)) and any(
                    isinstance(i.context_expr, ast.Call) and isinstance(i.context_expr.func, ast.Attribute)
                    and i.context_expr.func.attr == "raises" for i in w.items):
                if w.lineno <= line <= (w.end_lineno or w.lineno):
                    return "verified", "pytest.raises"
        return "failed", "not pytest.raises or inside it"
    # in_memory_return_value
    exprs: list[ast.AST] = []
    for n in nodes:
        if isinstance(n, ast.Assert):
            exprs.append(n.test)
        elif isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr.startswith("assert") \
                and isinstance(n.func.value, ast.Name) and n.func.value.id == "self":
            exprs.extend(n.args)
    if not exprs:
        return "failed", "no assertion expression at this line"
    if any(isinstance(x, (ast.Call, ast.Await)) for e in exprs for x in ast.walk(e)):
        return "failed", "the asserted expression makes a call (it may read the database)"
    binds = _bindings(fn)
    names = {x.id for e in exprs for x in ast.walk(e) if isinstance(x, ast.Name) and x.id in binds}
    if not names:
        return "failed", "no locally bound name is asserted"
    for nm in sorted(names):
        vals = binds[nm]
        if len(vals) != 1 or not _is_call(vals[0]):
            return "failed", f"{nm} isn't bound exactly once from a call's return value"
    return "verified", "names bound once from call return values: " + ", ".join(sorted(names))


def _paths(p: Any, scope: dict[str, str] | None = None) -> set[str]:
    """Every data path a predicate or expression reads, with bound row names resolved to their source."""
    scope = dict(scope or {})
    out: set[str] = set()

    def resolve(path: str) -> str:
        head, _, rest = path.partition(".")
        if head in scope:
            return scope[head] + ("." + rest if rest else "")
        return path

    def walk(x: Any, sc: dict[str, str]) -> None:
        nonlocal scope
        if isinstance(x, dict):
            if set(x) == {"path"} and isinstance(x["path"], str):
                head, _, rest = x["path"].partition(".")
                out.add((sc[head] + ("." + rest if rest else "")) if head in sc else x["path"])
                return
            if "rows" in x and isinstance(x.get("rows"), dict):
                src = _paths(x["rows"], sc)
                out.update(src)
                inner = dict(sc)
                rows_path = x["rows"].get("path") if isinstance(x["rows"], dict) else None
                if rows_path:
                    head, _, rest = rows_path.partition(".")
                    inner[x.get("as", "r")] = (sc[head] + ("." + rest if rest else "")) if head in sc else rows_path
                for k, v in x.items():
                    if k not in ("rows", "as"):
                        walk(v, inner)
                return
            for v in x.values():
                walk(v, sc)
        elif isinstance(x, list):
            for v in x:
                walk(v, sc)

    walk(p, scope)
    return {resolve(q) for q in out}


def reads_deployspecter(pred: Any, sources: dict[str, Any]) -> list[str]:
    """The paths of ``pred`` that read DeploySpecter's data under the spec's declared ``sources``."""
    fs = (sources or {}).get("final_state") or {}
    record = (sources or {}).get("raw_results_workload_record") or []
    out = []
    for path in sorted(_paths(pred)):
        root, _, rest = path.partition(".")
        if root == "final_state":
            key = rest.split(".")[0]
            if fs.get(key) != "application":
                out.append(path)
        elif root == "raw_results":
            if not any(rest == r or rest.startswith(r + ".") for r in record):
                out.append(path)
    return out


def coverage(spec: dict[str, Any], test_file: bytes) -> dict[str, Any]:
    """The per-case coverage record (see the module docstring)."""
    up = spec.get("upstream") or {}
    a = spec.get("assertions") or []
    expressed = [x for x in a if x.get("kind") == "final_state"]
    ne = [x for x in a if x.get("kind") == "not_expressible"]
    by_reason: dict[str, int] = {}
    verified = judgement = failed = 0
    fns: dict[str, ast.AST] = {}
    if up.get("test_functions"):
        try:
            fns = _functions(test_file.decode(), up["test_functions"])
        except (Refused, SyntaxError, UnicodeDecodeError):
            fns = {}
    for x in ne:
        by_reason[x.get("reason")] = by_reason.get(x.get("reason"), 0) + 1
        fn = next((f for f in fns.values() if f.lineno <= x["line"] <= (f.end_lineno or f.lineno)), None)
        status = verify_reason(fn, x["line"], x.get("reason")) if fn is not None else ("failed", "no function")
        verified += status[0] == "verified"
        judgement += status[0] == "investigator_judgement"
        failed += status[0] == "failed"
    flags = [x.get("uses_deployspecter_code") for x in expressed]
    known = all(isinstance(f, bool) for f in flags)
    without = sum(1 for f in flags if f is False) if known else None
    return {"case_id": spec.get("case_id"), "format": spec.get("format"), "total": len(a),
            "expressed": len(expressed), "expressed_without_deployspecter_code": without,
            "expressed_with_deployspecter_code": (len(expressed) - without) if known else None,
            "not_expressible_by_reason": dict(sorted(by_reason.items())), "mechanically_verified": verified,
            "verification_failed": failed, "investigator_judgement": judgement,
            "strict_subset": bool(known and expressed and without == len(expressed) and judgement == 0
                                  and failed == 0)}


def check_spec(spec: dict[str, Any], test_file: bytes) -> list[str]:
    """Problems with a spec against the upstream test file; an empty list means it accounts for every assertion."""
    p: list[str] = []
    if spec.get("format") not in FORMATS:
        p.append(f"format must be one of {FORMATS}")
    v2 = spec.get("format") == FORMAT
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
        if v2 and a.get("kind") == "final_state":
            flag = a.get("uses_deployspecter_code")
            if not isinstance(flag, bool):
                p.append(f"line {ln}: uses_deployspecter_code (true or false) is required")
            elif flag is False and isinstance(a.get("predicate"), dict):
                ds = reads_deployspecter(a["predicate"], spec.get("sources") or {})
                if ds:
                    p.append(f"line {ln}: uses_deployspecter_code is false but the predicate reads {ds}")
    for ln, text in expected.items():
        if ln not in listed:
            p.append(f"line {ln} isn't accounted for: {text[:80]}")
        elif " ".join(str(listed[ln].get("source", "")).split()) != " ".join(text.split()):
            p.append(f"line {ln}: the spec's source text isn't the file's")
    for ln in listed:
        if ln not in expected:
            p.append(f"line {ln} isn't an assertion in the named test functions")
    if v2:
        if not isinstance((spec.get("sources") or {}).get("final_state"), dict):
            p.append("sources.final_state must classify each final-state key")
        fns = _functions(test_file.decode(), functions)
        for ln, a in listed.items():
            if a.get("kind") != "not_expressible" or ln not in expected:
                continue
            fn = next((f for f in fns.values() if f.lineno <= ln <= (f.end_lineno or f.lineno)), None)
            status, detail = verify_reason(fn, ln, a.get("reason"))
            if status == "failed":
                p.append(f"line {ln}: reason {a.get('reason')!r} fails mechanical verification ({detail})")
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
        self.coverage: dict[str, Any] | None = None
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
        flags = {a["line"]: a.get("uses_deployspecter_code") for a in self.expressed}
        return {"verdict": "VIOLATED" if failed else "HOLDS", "failed_lines": failed,
                "failed_without_deployspecter_code": [ln for ln in failed if flags.get(ln) is False],
                "detected_only_through_deployspecter_code": bool(failed) and all(flags.get(ln) is True
                                                                                  for ln in failed),
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
        cov = coverage(spec, test_file)
        o.coverage = cov
        if o.expressed and spec.get("format") == FORMAT and all(
                a.get("uses_deployspecter_code") is True for a in o.expressed):
            reason = "oracle_depends_on_tool: every expressed assertion uses DeploySpecter code"
        elif o.expressed:
            if log is not None:
                log.append({"case_id": case, "admitted": True, "expressed": len(o.expressed),
                            "not_expressed": {str(a["line"]): a["reason"] for a in o.not_expressed},
                            "coverage": cov})
            return o
        else:
            reason = "no assertion is expressible over the final state: " + ", ".join(
                sorted({a["reason"] for a in o.not_expressed}))
    if log is not None:
        entry = {"case_id": case, "admitted": False, "reason": reason}
        if not problems:
            entry["coverage"] = coverage(spec, test_file)
        log.append(entry)
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
