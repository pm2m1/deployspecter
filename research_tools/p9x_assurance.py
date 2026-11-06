"""EXPERIMENTAL ds-p9x/1.1 assurance evidence (A2, A3, A4) on the disposable MVP PostgreSQL.

    python research_tools/p9x_assurance.py --out DIR

Writes one JSON record per scenario, plus ``SUMMARY.json``:
* **A2:** parsed-node-tree equivalence for Chancy's two partial-index forms (expected: established), and four
  differences that must not be established (a predicate constant, a CHECK constant, a default, uniqueness);
* **A3:** one construction whose session is reconciled with the server log;
* **A4:** six injected failures, each recorded with its exception, the target state afterwards and the attempt's own
  server-log provenance:
  - before writes;
  - during writes;
  - before verification;
  - after verification but before COMMIT;
  - a ROLLBACK that can't be sent (backend terminated);
  - SIGINT.

Exit status 0 only if every scenario behaved as expected. Run it through ``research_tools/pg_lock.py``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import sys
import traceback
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

import psycopg  # noqa: E402

from deployspecter.experimental import harness as hz  # noqa: E402
from deployspecter.experimental.phase9b_ext import execute, expr_equiv, loader, pipeline_v1, provenance  # noqa: E402
from deployspecter.experimental.state_construction.typed import TV  # noqa: E402

SECRET = Path(os.path.expanduser("~/.ds_mvp_pg_password"))
PORT = 55441
DDL = """
CREATE TABLE items (id int PRIMARY KEY, label text NOT NULL, w double precision, d jsonb,
                    state varchar(25) NOT NULL DEFAULT 'pending', k text);
CREATE INDEX items_fetch ON items (state) WHERE state IN ('pending', 'retrying');
CREATE UNIQUE INDEX items_uk ON items (k) WHERE k IS NOT NULL AND state NOT IN ('succeeded', 'failed');
CREATE TABLE audit (item int NOT NULL, label text NOT NULL);
CREATE FUNCTION audit_item() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN INSERT INTO audit (item, label) VALUES (NEW.id, NEW.label); RETURN NEW; END $$;
CREATE TRIGGER items_audit AFTER INSERT ON items FOR EACH ROW EXECUTE FUNCTION audit_item();
"""
ROWS = [("items", {"id": TV("int", i), "label": TV("str", f"it's {i}"), "w": TV("float", 0.1 * i),
                   "d": TV("jsonb", f'{{"n": {i}}}'), "state": TV("str", "pending"), "k": TV("str", f"k{i}")})
        for i in (1, 2, 3)]
SCHEMA = "ds_p9x_assure"


class Injected(RuntimeError):
    pass


def write(out: Path, name: str, obj: Any, password: str) -> str:
    text = json.dumps(obj, indent=1, sort_keys=True, default=str).replace(password, "<redacted>")
    (out / name).write_text(text)
    return hashlib.sha256(text.encode()).hexdigest()


def reset(target: hz.Target, schema: str, ddl: str) -> None:
    with psycopg.connect(**target.conninfo(), autocommit=True) as c:
        c.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        c.execute(f"CREATE SCHEMA {schema}")
        c.execute(f"SET search_path = {schema}")
        c.execute(ddl)


def state(target: hz.Target, schema: str) -> dict[str, int]:
    with psycopg.connect(**target.conninfo(), autocommit=True) as c:
        return {"items": c.execute(f"SELECT count(*) FROM {schema}.items").fetchone()[0],
                "audit": c.execute(f"SELECT count(*) FROM {schema}.audit").fetchone()[0],
                "construct_sessions": c.execute("SELECT count(*) FROM pg_stat_activity WHERE application_name = %s",
                                                (provenance.APPLICATION,)).fetchone()[0],
                "locks": c.execute("SELECT count(*) FROM pg_locks l JOIN pg_class r ON r.oid = l.relation JOIN "
                                   "pg_namespace n ON n.oid = r.relnamespace WHERE n.nspname = %s",
                                   (schema,)).fetchone()[0]}


def prov_summary(p: dict[str, Any] | None) -> dict[str, Any] | None:
    if p is None:
        return None
    return {k: p.get(k) for k in ("ok", "problems", "matched", "journaled", "by_kind", "params_checked",
                                  "params_unchecked", "construct_pid")} | {
        "journal_sha256": hashlib.sha256(json.dumps(p.get("journal"), sort_keys=True, default=str).encode())
        .hexdigest() if p.get("journal") is not None else None}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=False)
    pw = SECRET.read_text().strip()
    target = hz.Target("127.0.0.1", PORT, "ds_mvp", pw)
    results: dict[str, Any] = {"identity": loader.identity()}
    ok = True
    sandbox = pipeline_v1.open_sandbox(PORT, target.user, pw)
    try:
        # A3 (and A2 on Chancy's forms, through the pipeline)
        reset(target, SCHEMA, DDL)
        o = pipeline_v1.construct_on_target(target, sandbox, SCHEMA, ROWS)
        rec = {"status": o.status, "classification": o.classification, "export": o.export,
               "provenance": prov_summary(o.provenance), "journal": (o.provenance or {}).get("journal"),
               "readback": o.readback}
        exp_ok = o.status == "constructed" and (o.provenance or {}).get("ok") and all(
            e["established"] for e in (o.export or {}).get("expression_equivalence", []))
        rec["as_expected"] = bool(exp_ok)
        results["A3_construction_provenance"] = {"sha256": write(a.out, "A3_construction.json", rec, pw),
                                                 "as_expected": rec["as_expected"], "status": o.status,
                                                 "provenance": rec["provenance"]}
        results["A2_chancy_forms"] = {"expression_equivalence": (o.export or {}).get("expression_equivalence"),
                                      "as_expected": bool(exp_ok)}
        ok &= bool(exp_ok)
        final, fp = o.report["final_snapshot"], o.fingerprint_sha256

        # A2 refusals between two schemas
        pairs = {
            "predicate_constant": ("CREATE TABLE j (s varchar(9)); CREATE INDEX i ON j (s) WHERE s IN ('a', 'b')",
                                   "CREATE TABLE j (s varchar(9)); CREATE INDEX i ON j (s) WHERE s IN ('a', 'c')",
                                   "indexes", "i"),
            "check_constant": ("CREATE TABLE j (n int, CONSTRAINT c CHECK (n > 0))",
                               "CREATE TABLE j (n int, CONSTRAINT c CHECK (n > 1))", "constraints", "c"),
            "default_value": ("CREATE TABLE j (s text DEFAULT 'x')", "CREATE TABLE j (s text DEFAULT 'y')",
                              "columns", "s"),
            "uniqueness": ("CREATE TABLE j (s varchar(9)); CREATE INDEX i ON j (s) WHERE s IN ('a', 'b')",
                           "CREATE TABLE j (s varchar(9)); CREATE UNIQUE INDEX i ON j (s) WHERE s IN ('a', 'b')",
                           "indexes", "i")}
        a2 = {}
        for name, (left, right, kind, obj) in pairs.items():
            reset(target, "ds_p9x_assure_l", left)
            reset(target, "ds_p9x_assure_r", right)
            with psycopg.connect(**target.conninfo(), autocommit=True) as c:
                r = expr_equiv.establish(c, "ds_p9x_assure_l", c, "ds_p9x_assure_r",
                                         [{"kind": kind, "object": ["j", obj], "name": obj}])[0]
            a2[name] = r | {"as_expected": r["established"] is False}
            ok &= a2[name]["as_expected"]
        results["A2_refusals"] = {"sha256": write(a.out, "A2_refusals.json", a2, pw),
                                  "all_refused": all(v["as_expected"] for v in a2.values())}

        # A4 injected failures
        def terminate_then_raise(k: int) -> None:
            if k == 1:
                with psycopg.connect(**target.conninfo(), autocommit=True) as c:
                    c.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE application_name = %s",
                              (provenance.APPLICATION,))
                raise Injected("construction backend terminated; ROLLBACK can't be sent")

        def fail(*_a: Any) -> None:
            raise Injected("injected")

        scenarios = {"before_writes": ("before_writes", fail), "during_writes": ("on_insert",
                                                                                lambda k: fail() if k == 1 else None),
                     "before_verification": ("before_verify", fail),
                     "after_verification_before_commit": ("after_verify", fail),
                     "rollback_impossible": ("on_insert", terminate_then_raise),
                     "sigint": ("before_verify", lambda: os.kill(os.getpid(), signal.SIGINT))}
        a4 = {}
        for name, (stage, hook) in scenarios.items():
            with psycopg.connect(**target.conninfo(), autocommit=True) as c:
                c.execute(f"DELETE FROM {SCHEMA}.audit")
                c.execute(f"DELETE FROM {SCHEMA}.items")
            old = signal.signal(signal.SIGINT, signal.default_int_handler)
            rec = {"stage": stage}
            try:
                execute.write_and_verify(target.conninfo(), SCHEMA, ROWS, [0, 1, 2], final, fp, hooks={stage: hook})
                rec["raised"] = None
            except BaseException as exc:  # noqa: BLE001 - every outcome is recorded
                rec["raised"] = f"{type(exc).__name__}: {exc}"
                rec["traceback_tail"] = traceback.format_exc().splitlines()[-3:]
                p = getattr(exc, "provenance", None) or getattr(exc.__context__, "provenance", None)
                rec["provenance"] = prov_summary(p)
                rec["journal"] = (p or {}).get("journal")
            finally:
                signal.signal(signal.SIGINT, old)
            rec["target_after"] = state(target, SCHEMA)
            rec["as_expected"] = rec["raised"] is not None and rec["target_after"] == {
                "items": 0, "audit": 0, "construct_sessions": 0, "locks": 0}
            ok &= rec["as_expected"]
            a4[name] = {"sha256": write(a.out, f"A4_{name}.json", rec, pw), "raised": rec["raised"],
                        "target_after": rec["target_after"], "provenance_ok": (rec.get("provenance") or {}).get("ok"),
                        "provenance_problems": (rec.get("provenance") or {}).get("problems"),
                        "as_expected": rec["as_expected"]}
        results["A4_failures"] = a4
    finally:
        sandbox.close()
        results["sandbox_teardown"] = pipeline_v1.teardown(PORT, target.user, pw)
        with psycopg.connect(**target.conninfo(), autocommit=True) as c:
            for s in (SCHEMA, "ds_p9x_assure_l", "ds_p9x_assure_r"):
                c.execute(f"DROP SCHEMA IF EXISTS {s} CASCADE")
    ok &= all(results["sandbox_teardown"].values())
    results["verdict"] = "as expected" if ok else "NOT as expected"
    write(a.out, "SUMMARY.json", results, pw)
    print(json.dumps({"verdict": results["verdict"], **{k: v.get("as_expected") for k, v in
                                                        results.get("A4_failures", {}).items()}}, indent=1))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
