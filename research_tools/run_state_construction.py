"""EXPERIMENTAL: live state-construction evidence (disposable PostgreSQL only; run under research_tools/pg_lock.py).

    python research_tools/run_state_construction.py reservations --out DIR
    python research_tools/run_state_construction.py fixtures --out DIR
    python research_tools/run_state_construction.py chancy --out DIR --sdists DIR

* ``reservations``: the end-to-end pipeline (WorkloadSpec v2 → Phase 9B → typed construction → verification →
  stepped run → server-log corroboration → oracle) on Gate 2A's synthetic application, both frozen builds, plus
  documents each stage must refuse.
* ``fixtures``: ``generic.construct_typed`` on trusted fixtures written for this purpose: exact values, a
  trigger-dependent reordering, impossible, unknown-effect and type-refused cases.
* ``chancy``: Chancy 0.26.0's unmodified worker, in the isolated runner, consuming a constructed ``chancy_jobs`` state.
  It runs a form calibration against a job Chancy pushed itself, the constructed run, and a negative control whose
  corrupted row the oracle must catch.

Every attempt is written to DIR as JSON, with the database password scrubbed, whatever its outcome. The exit status
is 0 only if every attempt's outcome is the expected one.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from deployspecter.experimental import harness as hz  # noqa: E402
from deployspecter.experimental import oracle as mvp_oracle  # noqa: E402
from deployspecter.experimental.state_construction import chancy, generic, phase9b, pipeline, typed  # noqa: E402
from deployspecter.experimental.state_construction.typed import TV  # noqa: E402
from deployspecter.research.track_a import scenario as sc  # noqa: E402

PORT = 55441
SECRET = Path(os.path.expanduser("~/.ds_mvp_pg_password"))


def write(out: Path, name: str, obj: Any, password: str) -> str:
    text = json.dumps(obj, indent=1, sort_keys=True, default=str).replace(password, "<redacted>")
    (out / name).write_text(text)
    return hashlib.sha256(text.encode()).hexdigest()


# ------------------------------------------------------------------------------------------------ reservations

def doc_with(change: Any) -> dict[str, Any]:
    d = copy.deepcopy(sc.DOCUMENT)
    change(d)
    return d


def reservations(target: hz.Target, out: Path, password: str) -> list[dict[str, Any]]:
    def two_holders(d: dict[str, Any]) -> None:  # an extra state goal: one reservation already held (serial id)
        d["state_goals"].append({"entity": "reservation", "label": "held", "count": 1, "constraints": [
            {"field": "event_id", "op": "eq", "value": {"type": "int", "value": 1}},
            {"field": "holder", "op": "eq", "value": {"type": "str", "value": "carol"}},
            {"field": "qty", "op": "eq", "value": {"type": "int", "value": 1}}]})

    def unknown_field(d: dict[str, Any]) -> None:
        d["state_goals"][0]["constraints"].append({"field": "colour", "op": "eq",
                                                   "value": {"type": "str", "value": "red"}})

    def infeasible(d: dict[str, Any]) -> None:  # capacity 3 and capacity 4 for the same event
        d["state_goals"][0]["constraints"].append(copy.deepcopy(d["state_goals"][0]["constraints"][1]))
        d["state_goals"][0]["constraints"][-1]["value"] = {"type": "int", "value": 4}

    admin = target.admin()
    foreign = [list(r) for r in admin.execute(
        "SELECT n.nspname::text, c.relname::text, t.tgname::text FROM pg_trigger t JOIN pg_class c ON c.oid = "
        "t.tgrelid JOIN pg_namespace n ON n.oid = c.relnamespace WHERE NOT t.tgisinternal AND n.nspname <> 'ds_run' "
        "ORDER BY 1, 2, 3").fetchall()]
    admin.close()
    write(out, "reservations_database_triggers_probe.json", {"user_triggers_outside_ds_run": foreign}, password)
    live_expected = "refused_planning" if foreign else "ran"  # the live export is database-wide for triggers
    cases = [("track_a_document", sc.DOCUMENT, ("buggy", "fixed"), "ran", "dry_run"),
             ("with_held_reservation", doc_with(two_holders), ("buggy", "fixed"), "ran", "dry_run"),
             ("track_a_document_live_catalog", sc.DOCUMENT, ("fixed",), live_expected, "live"),
             ("unknown_field_refused", doc_with(unknown_field), ("fixed",), "refused_planning", "dry_run"),
             ("infeasible_goal_refused", doc_with(infeasible), ("fixed",), "refused_planning", "dry_run")]
    results = []
    sb = phase9b.open_sandbox(PORT, target.user, password)
    try:
        for name, doc, variants, expected, catalog in cases:
            for v in variants:
                try:
                    o = pipeline.run(target, doc, variant=v, schedule=["a", "b"] * 6, sandbox=sb, catalog=catalog)
                    rec = o.to_dict()
                except Exception as exc:  # noqa: BLE001 - a crash is an outcome, recorded, never retried
                    o, rec = None, {"status": "crashed", "detail": f"{type(exc).__name__}: {exc}"}
                rec.update(case=name, variant=v, expected_status=expected, catalog=catalog)
                ok = rec["status"] == expected
                if o is not None and o.status == "ran":
                    corr = (o.run.corroboration or {})
                    ok = ok and corr.get("ok") is True and corr.get("params_mismatched", 0) == 0
                    if name.startswith("track_a_document"):  # the frozen scenario's known verdicts
                        ok = ok and (bool(o.run.violations) == (v == "buggy"))
                    elif v == "fixed":  # other documents: the fixed build must hold; the buggy one is recorded
                        ok = ok and not o.run.violations
                rec["as_expected"] = ok
                rec["sha256"] = write(out, f"reservations_{name}_{v}.json", rec, password)
                results.append({k: rec.get(k) for k in ("case", "variant", "catalog", "status", "stage",
                                                        "expected_status", "as_expected", "sha256")} |
                               {"classification": (rec.get("construction") or {}).get("classification"),
                                "violations": (rec.get("run") or {}).get("violations"),
                                "corroborated": ((rec.get("run") or {}).get("corroboration") or {}).get("ok"),
                                "params": {k: ((rec.get("run") or {}).get("corroboration") or {}).get(k)
                                           for k in ("params_compared", "params_mismatched", "params_unchecked")}})
    finally:
        sb.close()
        admin = target.admin()
        admin.execute("DROP SCHEMA IF EXISTS ds_run CASCADE")
        admin.close()
    return results


# ------------------------------------------------------------------------------------------------ fixtures

EXACT = "CREATE TABLE v (id bigint PRIMARY KEY, f double precision, s text, b boolean, n integer);"
LAST_WRITER = """CREATE TABLE quotes (sku int, vendor int, cents int NOT NULL, PRIMARY KEY (sku, vendor));
CREATE TABLE best_quote (sku int PRIMARY KEY, cents int NOT NULL);
CREATE FUNCTION keep() RETURNS trigger AS $$ BEGIN
    INSERT INTO best_quote VALUES (NEW.sku, NEW.cents) ON CONFLICT (sku) DO UPDATE SET cents = EXCLUDED.cents;
    RETURN NEW; END $$ LANGUAGE plpgsql;
CREATE TRIGGER t AFTER INSERT ON quotes FOR EACH ROW EXECUTE FUNCTION keep();"""
AUDIT = """CREATE TABLE acct (id int PRIMARY KEY, owner text NOT NULL);
CREATE TABLE audit (acct_id int, note text);
CREATE FUNCTION note() RETURNS trigger AS $$ BEGIN INSERT INTO audit VALUES (NEW.id, 'opened'); RETURN NEW; END $$
LANGUAGE plpgsql;
CREATE TRIGGER t AFTER INSERT ON acct FOR EACH ROW EXECUTE FUNCTION note();"""
CHECKED = "CREATE TABLE c (id int PRIMARY KEY, qty int CHECK (qty > 0));"
PARENT_CHILD = """CREATE TABLE parent (id int PRIMARY KEY);
CREATE TABLE child (id int PRIMARY KEY, parent_id int NOT NULL REFERENCES parent (id));"""
DYNAMIC = """CREATE TABLE d (id int PRIMARY KEY); CREATE TABLE e (id int);
CREATE FUNCTION dyn() RETURNS trigger AS $$ BEGIN EXECUTE 'INSERT INTO e VALUES (' || NEW.id || ')'; RETURN NEW;
END $$ LANGUAGE plpgsql;
CREATE TRIGGER t AFTER INSERT ON d FOR EACH ROW EXECUTE FUNCTION dyn();"""
JOBS = ("CREATE TABLE jobs (id uuid PRIMARY KEY, kwargs json NOT NULL, at timestamptz NOT NULL, "
        "state varchar(25) NOT NULL);")
JOBS_NO_JSON = "CREATE TABLE jobs (id uuid PRIMARY KEY, at timestamptz NOT NULL, state varchar(25) NOT NULL);"
U = "0b0a8c1e-6f43-5d2e-9a1b-3c4d5e6f7a8b"
T0 = "2026-01-01T00:00:00+00:00"


def i(v: int) -> TV:
    return TV("int", v)


FIXTURES: list[tuple[str, str, list[tuple[str, dict[str, TV]]], str, str | None]] = [
    # name, setup, rows, expected status, expected classification
    ("exact_values", EXACT, [
        ("v", {"id": i(2**63 - 1), "f": TV("float", -0.0), "s": TV("str", "NULL"), "b": TV("bool", False),
               "n": TV("null", None)}),
        ("v", {"id": i(-(2**63)), "f": TV("float", 5e-324), "s": TV("null", None), "b": TV("bool", True),
               "n": i(0)}),
        ("v", {"id": i(0), "f": TV("float", 0.0), "s": TV("str", ""), "b": TV("null", None), "n": i(2**31 - 1)})],
     "constructed", "ORIGINAL_VALID"),
    ("trigger_reordering", LAST_WRITER, [("quotes", {"sku": i(1), "vendor": i(2), "cents": i(20)}),
                                        ("quotes", {"sku": i(1), "vendor": i(1), "cents": i(10)}),
                                        ("best_quote", {"sku": i(1), "cents": i(20)})],
     "constructed", "CONSTRUCTED_EQUIVALENT"),
    ("trigger_row_reconciled", AUDIT, [("acct", {"id": i(1), "owner": TV("str", "ann")}),
                                       ("audit", {"acct_id": i(1), "note": TV("str", "opened")})],
     "constructed", None),  # recorded: whatever the constructor decides about the identical trigger row
    ("dependency_order", PARENT_CHILD, [("child", {"id": i(1), "parent_id": i(1)}), ("parent", {"id": i(1)})],
     "constructed", "CONSTRUCTED_EQUIVALENT"),
    ("check_violation_impossible", CHECKED, [("c", {"id": i(1), "qty": i(0)})], "refused_constructor",
     "IMPOSSIBLE_WITH_SUPPORTED_OPERATIONS"),
    ("dynamic_sql_trigger", DYNAMIC, [("d", {"id": i(1)})], "constructed", "ORIGINAL_VALID"),
    ("nan_refused_by_phase9b", EXACT, [("v", {"id": i(1), "f": TV("float", float("nan"))})], "refused_constructor",
     "IMPOSSIBLE_WITH_SUPPORTED_OPERATIONS"),
    ("json_refused_by_phase9b", JOBS, [("jobs", {"id": TV("uuid", U), "kwargs": TV("json", '{"n": 1}'),
                                                 "at": TV("timestamptz", T0), "state": TV("str", "pending")})],
     "refused_constructor", "IMPOSSIBLE_WITH_SUPPORTED_OPERATIONS"),
    ("jsonb_refused_by_phase9b", JOBS.replace("json", "jsonb"), [("jobs", {"id": TV("uuid", U), "kwargs": TV(
        "jsonb", '{"n": 1}'), "at": TV("timestamptz", T0), "state": TV("str", "pending")})], "refused_constructor",
     "IMPOSSIBLE_WITH_SUPPORTED_OPERATIONS"),
    ("uuid_timestamptz_without_json", JOBS_NO_JSON, [("jobs", {"id": TV("uuid", U), "at": TV("timestamptz", T0),
                                                               "state": TV("str", "pending")})],
     "constructed", "ORIGINAL_VALID"),
    ("bool_for_bigint_refused", EXACT, [("v", {"id": TV("bool", True)})], "refused_typed", None),
    ("int_out_of_int4_refused", EXACT, [("v", {"id": i(1), "n": i(2**31)})], "refused_typed", None),
    ("int_tag_holding_float_refused", EXACT, [("v", {"id": TV("int", 1.0)})], "refused_typed", None),
    ("uppercase_uuid_refused", JOBS_NO_JSON, [("jobs", {"id": TV("uuid", U.upper()), "at": TV("timestamptz", T0),
                                                        "state": TV("str", "x")})], "refused_typed", None),
    ("naive_timestamp_refused", JOBS_NO_JSON, [("jobs", {"id": TV("uuid", U), "at": TV("timestamptz",
                                                                                      "2026-01-01T00:00:00"),
                                                         "state": TV("str", "x")})], "refused_typed", None),
    ("varchar_too_long_refused", JOBS_NO_JSON, [("jobs", {"id": TV("uuid", U), "at": TV("timestamptz", T0),
                                                          "state": TV("str", "x" * 26)})], "refused_typed", None),
    ("null_in_not_null_refused", JOBS_NO_JSON, [("jobs", {"id": TV("uuid", U), "at": TV("null", None),
                                                          "state": TV("str", "x")})], "refused_typed", None),
]


def fixtures(target: hz.Target, out: Path, password: str) -> list[dict[str, Any]]:
    results = []
    sb = phase9b.open_sandbox(PORT, target.user, password)
    try:
        for name, setup, rows, want_status, want_class in FIXTURES:
            schema = f"ds_sc_fx_{name}"[:63]
            try:
                r = generic.construct_typed(target, sb, setup_sql=setup, rows=rows, schema=schema)
                rec = r.to_dict()
            except Exception as exc:  # noqa: BLE001
                rec = {"status": "crashed", "detail": f"{type(exc).__name__}: {exc}", "classification": None}
            ok = rec["status"] == want_status and (want_class is None or rec["classification"] == want_class)
            rec.update(fixture=name, expected_status=want_status, expected_classification=want_class,
                       as_expected=ok, rows=[(t, {c: v.to_dict() for c, v in vals.items()}) for t, vals in rows])
            rec["sha256"] = write(out, f"fixture_{name}.json", rec, password)
            results.append({k: rec.get(k) for k in ("fixture", "status", "classification", "order",
                                                    "expected_status", "expected_classification", "as_expected",
                                                    "sha256")} | {"detail": str(rec.get("detail"))[:240]})
            admin = target.admin()
            admin.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            admin.close()
    finally:
        sb.close()
    return results


# ------------------------------------------------------------------------------------------------ chancy

def load_isolated() -> Any:
    path = REPO / "research_tools" / "isolated" / "run_isolated.py"
    spec = importlib.util.spec_from_file_location("ds_run_isolated", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def read_job(admin: Any, schema: str, jid: str) -> dict[str, Any]:
    cols = list(chancy.PINNED_COLUMNS)
    sel = ", ".join([f'"{c}"' for c in cols] + [f'"{c}"::text' for c in cols])
    r = admin.execute(f'SELECT {sel} FROM "{schema}".chancy_jobs WHERE id = %s::uuid', (jid,)).fetchone()
    return {"values": dict(zip(cols, r[:len(cols)], strict=True)),
            "text": dict(zip(cols, r[len(cols):], strict=True))}


def chancy_run(target: hz.Target, out: Path, password: str, sdists: Path) -> list[dict[str, Any]]:
    iso = load_isolated()
    schema = iso.CAPTURE_SCHEMA
    build_dir = Path(tempfile.mkdtemp(prefix="ds_sc_build_"))
    try:
        image = iso.build_image(build_dir, chancy_sdist=sdists / "chancy-0.26.0.tar.gz")
    finally:
        shutil.rmtree(build_dir, ignore_errors=True)
    results = []

    def phase(name: str, plan: list[dict[str, Any]], *, fresh: bool, keep: bool) -> dict[str, Any]:
        d = out / name
        d.mkdir(exist_ok=True)
        r = iso.run_smoke(target, variant=name, schedule=[], out=d, image=image, mode="chancy-plan",
                          fresh_schema=fresh, keep_schema=keep, extra_env={"DS_CHANCY_PLAN": json.dumps(plan)})
        write(out, f"{name}.run.json", r, password)
        return r

    def sound(r: dict[str, Any]) -> bool:
        c = r.get("corroboration") or {}
        return bool(r.get("isolation_ok")) and c.get("ok") is True and c.get("params_mismatched", 0) == 0 and \
            all((r.get("teardown") or {}).values())

    # 0. form calibration: a job Chancy pushes itself, compared column by column with the translator's row for it
    r0 = phase("chancy_0_calibration_push", [{"actors": [{"actor": "setup", "fn": "setup", "args": {"ns": [0]}}]}],
               fresh=True, keep=True)
    admin = target.admin()
    try:
        pushed = (r0.get("raw_results") or {}).get("setup", {}).get("job_ids", [])
        ref = read_job(admin, schema, pushed[0]) if pushed else None
        admin.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
    finally:
        admin.close()
    mine = chancy.rows(chancy.goals({"jobs": [{"n": 0, "state": "pending"}]}))[0][1]
    identity = {"id", "created_at", "scheduled_at"}  # Chancy's own uuid and clock; the translator fixes these
    calib = []
    if ref is None:
        calib.append("Chancy pushed no job")
    else:
        for c, tv in mine.items():
            if c not in identity and not typed.same(tv, ref["values"][c], ref["text"][c]):
                calib.append(f"{c}: Chancy wrote {ref['text'][c]!r}, the translator {tv.to_dict()}")
    rec0 = {"attempt": "calibration", "sound": sound(r0), "differences": calib, "reference_text": ref and ref["text"],
            "as_expected": sound(r0) and not calib}
    rec0["sha256"] = write(out, "chancy_0_calibration.json", rec0, password)
    results.append({k: rec0[k] for k in ("attempt", "sound", "differences", "as_expected", "sha256")})

    workload = {"jobs": [{"n": 0, "state": "pending"}, {"n": 1, "state": "pending"}, {"n": 2, "state": "pending"},
                         {"n": 7, "state": "succeeded"}]}
    for label, corrupt in (("constructed", None), ("negative_control_corrupted_func", 1)):
        results.append(chancy_attempt(target, out, password, iso, phase, sound, schema, label, workload, corrupt))
    return results


def chancy_attempt(target: hz.Target, out: Path, password: str, iso: Any, phase: Any, sound: Any, schema: str,
                   label: str, workload: dict[str, Any], corrupt: int | None) -> dict[str, Any]:
    import psycopg
    rec: dict[str, Any] = {"attempt": label, "workload": workload}
    problems = chancy.validate(workload)
    if problems:
        rec.update(status="refused_workload", detail=problems)
        return rec
    gs = chancy.goals(workload)
    rows = chancy.rows(gs)
    if corrupt is not None:  # the negative control: one pending job's func names no function (not a goal; a test)
        t, vals = rows[corrupt]
        rows[corrupt] = (t, {**vals, "func": TV("str", chancy.FUNC.replace("ds_job", "ds_missing"))})
    rec["constructor"] = chancy.constructor_for(rows)
    rec["goals"] = gs
    r1 = phase(f"chancy_1_{label}_setup", [{"actors": [{"actor": "setup", "fn": "setup", "args": {"ns": []}}]}],
               fresh=True, keep=True)
    rec["setup_sound"] = sound(r1)
    admin = target.admin()
    try:
        if not rec["setup_sound"]:
            rec.update(status="refused_setup", as_expected=False)
            admin.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
            return rec | {"sha256": write(out, f"chancy_{label}.json", rec, password)}
        pre = chancy.preconditions(admin, schema, rows)
        rec["preconditions"] = pre
        if pre or rec["constructor"] != "direct_trigger_free":
            rec.update(status="refused_preconditions", as_expected=False)
            admin.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
            return rec | {"sha256": write(out, f"chancy_{label}.json", rec, password)}
        from psycopg import sql
        with admin.transaction():  # the construction: one transaction, exact typed parameters
            for table, vals in rows:
                cols = list(vals)
                admin.execute(sql.SQL("INSERT INTO {}.{} ({}) VALUES ({})").format(
                    sql.Identifier(schema), sql.Identifier(table), sql.SQL(", ").join(map(sql.Identifier, cols)),
                    sql.SQL(", ").join(typed.placeholder(vals[c]) for c in cols)), [vals[c].value for c in cols])
        with psycopg.connect(**target.conninfo(), autocommit=True, application_name="ds_sc_verify") as conn:
            rec["verification"] = typed.verify(conn, schema, chancy.JOBS_TABLE, [v for _, v in rows])
        expected_digest = mvp_oracle.digest({"catalog": iso.catalog(admin, schema), **iso.chancy_state(admin, schema)})
        rec["verified_digest"] = expected_digest
        if rec["verification"]:
            rec.update(status="refused_verification", as_expected=False)
            admin.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
            return rec | {"sha256": write(out, f"chancy_{label}.json", rec, password)}
        pending_ids = [chancy.job_id(j["n"]) for j in workload["jobs"] if j["state"] == "pending"]
        r2 = phase(f"chancy_2_{label}_worker", [{"actors": [{"actor": "worker", "fn": "work", "args": {
            "handle_at_least": 10**6, "wait_for": pending_ids, "timeout_s": 60}}]}], fresh=False, keep=True)
        rec["worker_sound"] = sound(r2)
        rec["bound_to_verified_state"] = r2.get("initial_state_digest") == expected_digest
        final = iso.chancy_state(admin, schema)["jobs"]
        handled = ((r2.get("raw_results") or {}).get("worker") or {}).get("handled", [])
        rec["final_jobs"], rec["handled"] = final, handled
        rec["oracle_violations"] = chancy.oracle(workload, final, handled)
        c = r2.get("corroboration") or {}
        rec["corroboration"] = {k: c.get(k) for k in ("ok", "matched", "sessions", "params_compared",
                                                       "params_mismatched", "params_unchecked", "problems")}
        admin.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
    finally:
        admin.close()
    rec["status"] = "ran"
    want_violation = corrupt is not None
    rec["as_expected"] = bool(rec["worker_sound"] and rec["bound_to_verified_state"] and
                              bool(rec["oracle_violations"]) == want_violation)
    rec["sha256"] = write(out, f"chancy_{label}.json", rec, password)
    return {k: rec.get(k) for k in ("attempt", "status", "constructor", "preconditions", "verification",
                                    "worker_sound", "bound_to_verified_state", "handled", "oracle_violations",
                                    "corroboration", "as_expected", "sha256")}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["reservations", "fixtures", "chancy"])
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--sdists", type=Path)
    a = ap.parse_args()
    password = SECRET.read_text().strip()
    target = hz.Target("127.0.0.1", PORT, "ds_mvp", password)
    if a.out.exists():
        raise SystemExit(f"refused: {a.out} exists (evidence directories are never reused)")
    a.out.mkdir(parents=True)
    try:
        if a.mode == "reservations":
            results = reservations(target, a.out, password)
        elif a.mode == "fixtures":
            results = fixtures(target, a.out, password)
        else:
            results = chancy_run(target, a.out, password, a.sdists)
    finally:
        cleanup = phase9b.teardown(PORT, target.user, password) if a.mode != "chancy" else {}
        admin = target.admin()
        cleanup["public_objects"] = admin.execute(
            "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public'").fetchone()[0]
        cleanup["leftover_schemas"] = [r[0] for r in admin.execute(
            "SELECT nspname::text FROM pg_namespace WHERE nspname LIKE 'ds_sc_%' OR nspname IN ('ds_run', "
            "'chancy_capture')").fetchall()]
        admin.close()
    ok = all(r.get("as_expected") for r in results)
    summary = {"mode": a.mode, "results": results, "cleanup": cleanup, "all_as_expected": ok}
    write(a.out, "SUMMARY.json", summary, password)
    print(json.dumps(summary, indent=1, default=str))
    return 0 if ok and not cleanup["leftover_schemas"] and cleanup["public_objects"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
