"""EXPERIMENTAL, NONBLIND: the supported digital twin of procrastinate 3.3.0 → 3.4.0's documented blue-green upgrade
(``docs/howto/production/migrations.md`` at tag 3.4.0: apply ``pre``, deploy the new version, stop every old worker,
apply ``post``).

Pure functions and SQL used by ``research_tools/run_twin_procrastinate.py``:

* ``PHASES``: the seven modeled phases, their versions, permitted operations and declared entry/exit conditions;
* ``catalog(admin)`` / ``catalog_digest`` / ``catalog_diff``: schema identity of ``public``;
* ``twin_state(admin)``: jobs (with ``worker_id``), events, registered workers, locks, sessions, prepared transactions;
* ``data_only_statements``: is a migration file free of row-modifying statements (no backfill)?
* ``permitted_violations``: statements in a phase's trace that the phase doesn't permit;
* ``provenance``: per actor, version, backend pid, transactions, statements; cross-checked with the server log;
* ``quiescence``: the old version has stopped (no old container, no client session, no registered worker, no
  ``doing`` job, no foreign lock, no prepared transaction);
* ``classify``: the frozen checker's entries, split into genuine violations and the documented oracle errors E1/E2.

Nothing here edits the frozen case (``CASE.md``) or its checker (``procrastinate_properties.py``).
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

OLD, NEW = "3.3.0", "3.4.0"
PRE, POST = "03.04.00_01_pre_add_retry_failed_job_procedure.sql", "03.04.00_50_post_add_retry_failed_job_procedure.sql"
TRIGGER_FN = "procrastinate_trigger_function_status_events_update_v1"

# The modeled phases. ``runs`` names the existing rolling-upgrade phases (research_tools/isolated/
# run_rolling_upgrade.py) or the twin's own, in order. ``host_only`` phases start no container.
PHASES: list[dict[str, Any]] = [
    {"id": "T1", "name": "old application on old schema", "runs": ["0a_old_schema", "0b_old_on_old_schema"],
     "versions": [OLD], "ddl": ["0a_old_schema"],
     "entry": "public is empty", "exit": "3.3.0 schema present, no retry_job_v2; every deferred job terminal; no "
     "registered worker; catalog unchanged by 0b"},
    {"id": "T2", "name": "pre-migration (expand), then old code on the expanded schema",
     "runs": ["1_pre_migration", "2_old_on_pre_schema"], "versions": [OLD, NEW], "ddl": ["1_pre_migration"],
     "entry": "catalog equals T1's exit catalog", "exit": "the pre migration is purely additive (nothing removed or "
     "changed in the catalog); job rows unchanged by it; old code still runs on it; catalog unchanged by phase 2"},
    {"id": "T3", "name": "simultaneous old/new execution", "runs": ["3_overlap"], "versions": [OLD, NEW], "ddl": [],
     "entry": "catalog equals T2's exit catalog", "exit": "both versions executed statements, interleaved in one "
     "global grant order; catalog unchanged; no doing job; no registered worker"},
    {"id": "T4", "name": "background-job interaction: 3.4.0 admin retries of failed jobs while both versions' "
     "workers run (no backfill exists in this pair)", "runs": ["T4_background_retry"], "versions": [OLD, NEW],
     "ddl": [], "entry": "catalog equals T3's exit; at least one failed job (other than n=4, reserved for phase 5)",
     "exit": "catalog unchanged; no doing job; no registered worker"},
    {"id": "T5", "name": "safe old-version quiescence (host-verified)", "runs": [], "host_only": True,
     "versions": [], "ddl": [], "entry": "T4 sound and its containers removed",
     "exit": "no 3.3.0 container; no client session; no registered worker; no doing job; no foreign lock; no "
     "prepared transaction"},
    {"id": "T6", "name": "post-migration (contract), then the new version alone",
     "runs": ["4_post_migration", "5_new_only"], "versions": [NEW], "ddl": ["4_post_migration"],
     "entry": "T5 quiesced; catalog equals T4's exit", "exit": "post changed only the status-events trigger function; "
     "job rows unchanged by it; the trigger records 'retried'; phase 5 used only 3.4.0; every job terminal"},
    {"id": "T7", "name": "final-state verification", "runs": ["R_fresh_3.4.0_schema"], "host_only": False,
     "versions": [NEW], "ddl": ["R_fresh_3.4.0_schema"],
     "entry": "T6 sound", "exit": "frozen checker: no genuine violation (E1/E2 reported as oracle errors); contract "
     "order held; upgraded catalog compared with a fresh 3.4.0 install"},
]

CATALOG_SQL = {
    "columns": "SELECT c.relname, a.attname, format_type(a.atttypid, a.atttypmod), a.attnotnull::text, "
               "coalesce(pg_get_expr(d.adbin, d.adrelid), '') FROM pg_attribute a JOIN pg_class c ON c.oid = "
               "a.attrelid JOIN pg_namespace n ON n.oid = c.relnamespace LEFT JOIN pg_attrdef d ON d.adrelid = "
               "a.attrelid AND d.adnum = a.attnum WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p') AND "
               "a.attnum > 0 AND NOT a.attisdropped",
    "column_order": "SELECT c.relname, string_agg(a.attname, ',' ORDER BY a.attnum) FROM pg_attribute a JOIN "
                    "pg_class c ON c.oid = a.attrelid JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname "
                    "= 'public' AND c.relkind IN ('r', 'p') AND a.attnum > 0 AND NOT a.attisdropped GROUP BY "
                    "c.relname",
    "constraints": "SELECT conrelid::regclass::text, conname, contype::text, pg_get_constraintdef(oid) FROM "
                   "pg_constraint WHERE connamespace = 'public'::regnamespace",
    "indexes": "SELECT tablename, indexname, indexdef FROM pg_indexes WHERE schemaname = 'public'",
    "functions": "SELECT p.proname, pg_get_function_identity_arguments(p.oid), pg_get_function_result(p.oid), "
                 "p.prokind::text, p.prosrc FROM pg_proc p WHERE p.pronamespace = 'public'::regnamespace",
    "triggers": "SELECT c.relname, t.tgname, pg_get_triggerdef(t.oid) FROM pg_trigger t JOIN pg_class c ON c.oid = "
                "t.tgrelid WHERE c.relnamespace = 'public'::regnamespace AND NOT t.tgisinternal",
    "enums": "SELECT t.typname, string_agg(e.enumlabel, ',' ORDER BY e.enumsortorder) FROM pg_enum e JOIN pg_type t "
             "ON t.oid = e.enumtypid WHERE t.typnamespace = 'public'::regnamespace GROUP BY t.typname",
    "composites": "SELECT t.typname, a.attname, format_type(a.atttypid, a.atttypmod) FROM pg_type t JOIN pg_class c "
                  "ON c.oid = t.typrelid AND c.relkind = 'c' JOIN pg_attribute a ON a.attrelid = c.oid AND "
                  "a.attnum > 0 WHERE t.typnamespace = 'public'::regnamespace",
    "sequences": "SELECT sequencename, data_type::text FROM pg_sequences WHERE schemaname = 'public'",
    "views": "SELECT viewname, definition FROM pg_views WHERE schemaname = 'public'",
}


def _norm_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def catalog(admin: Any) -> dict[str, list[list[str]]]:
    """Schema identity of ``public``, section by section, as sorted rows of strings. Function bodies are kept as two
    hashes: exact (``prosrc``), and with whitespace collapsed (so a formatting-only difference is told apart)."""
    out: dict[str, list[list[str]]] = {}
    for key, q in CATALOG_SQL.items():
        rows = [["" if v is None else str(v) for v in r] for r in admin.execute(q).fetchall()]
        if key == "functions":
            rows = [r[:4] + [hashlib.sha256(r[4].encode()).hexdigest()[:16],
                             hashlib.sha256(_norm_ws(r[4]).encode()).hexdigest()[:16]] for r in rows]
        out[key] = sorted(rows)
    return out


def catalog_digest(cat: dict[str, list[list[str]]]) -> str:
    return hashlib.sha256(json.dumps(cat, sort_keys=True).encode()).hexdigest()


def catalog_diff(a: dict[str, list[list[str]]], b: dict[str, list[list[str]]]) -> dict[str, dict[str, list]]:
    """Per section: rows only in ``a`` (removed going a → b) and only in ``b`` (added). A changed row shows as one
    removal and one addition. Functions are keyed for ``changed`` by (name, arguments)."""
    out: dict[str, dict[str, list]] = {}
    for key in sorted(set(a) | set(b)):
        ra, rb = {json.dumps(r) for r in a.get(key, [])}, {json.dumps(r) for r in b.get(key, [])}
        removed, added = sorted(json.loads(x) for x in ra - rb), sorted(json.loads(x) for x in rb - ra)
        if removed or added:
            out[key] = {"removed": removed, "added": added}
    if "functions" in out:
        ka = {(r[0], r[1]) for r in out["functions"]["removed"]}
        kb = {(r[0], r[1]) for r in out["functions"]["added"]}
        out["functions"]["changed"] = sorted(f"{n}({args})" for n, args in ka & kb)
        out["functions"]["whitespace_only"] = sorted(
            f"{r[0]}({r[1]})" for r in out["functions"]["removed"]
            for s in out["functions"]["added"] if (s[0], s[1]) == (r[0], r[1]) and s[:4] == r[:4] and s[5] == r[5])
    return out


def is_additive(diff: dict[str, dict[str, list]]) -> bool:
    """Expand step: nothing removed or changed, only additions. An enum gaining a label shows as a changed row, so
    enums are additive when every old label list is a prefix-preserving subsequence of the new one."""
    for key, d in diff.items():
        if key == "enums":
            old = {r[0]: r[1].split(",") for r in d["removed"]}
            new = {r[0]: r[1].split(",") for r in d["added"]}
            for t, labels in old.items():
                it = iter(new.get(t, []))
                if t not in new or not all(lab in it for lab in labels):
                    return False
            continue
        if d["removed"]:
            return False
    return True


TWIN_STATE_SQL = {
    "jobs": "SELECT id, (args->>'n')::int AS n, status::text AS status, attempts, worker_id FROM "
            "public.procrastinate_jobs ORDER BY id",
    "events": "SELECT job_id, type::text AS type FROM public.procrastinate_events ORDER BY id",
    "workers": "SELECT count(*) AS n FROM public.procrastinate_workers",
}
HOST_SQL = {
    "client_sessions": "SELECT pid, application_name, state FROM pg_stat_activity WHERE datname = current_database() "
                       "AND pid <> pg_backend_pid() AND backend_type = 'client backend'",
    "foreign_locks": "SELECT locktype, mode, granted FROM pg_locks WHERE pid <> pg_backend_pid() AND database = "
                     "(SELECT oid FROM pg_database WHERE datname = current_database())",
    "prepared": "SELECT gid FROM pg_prepared_xacts",
}


def twin_state(admin: Any) -> dict[str, Any]:
    present = admin.execute("SELECT to_regclass('public.procrastinate_jobs') IS NOT NULL").fetchone()[0]
    out: dict[str, Any] = {"schema_present": bool(present)}
    if present:
        for key, q in TWIN_STATE_SQL.items():
            cur = admin.execute(q)
            names = [d.name for d in cur.description]
            out[key] = [dict(zip(names, r, strict=True)) for r in cur.fetchall()]
        out["workers"] = int(out["workers"][0]["n"])
    for key, q in HOST_SQL.items():
        out[key] = [list(r) for r in admin.execute(q).fetchall()]
    return out


def rows_digest(jobs: list[dict[str, Any]]) -> str:
    """Job rows, for 'a migration didn't modify existing rows'."""
    return hashlib.sha256(json.dumps(jobs, sort_keys=True, default=str).encode()).hexdigest()


def state_digest(state: dict[str, Any], cat_digest: str) -> str:
    keep = {k: state.get(k) for k in ("jobs", "events", "workers")}
    return hashlib.sha256(json.dumps({"state": keep, "catalog": cat_digest}, sort_keys=True,
                                     default=str).encode()).hexdigest()


# ---------------------------------------------------------------------------------------------- migrations

_DOLLAR = re.compile(r"(\$[A-Za-z_]*\$).*?\1", re.S)
_COMMENT = re.compile(r"--[^\n]*")
# not a trigger or foreign-key *event* clause (AFTER UPDATE OF ..., INSERT OR UPDATE, ON DELETE ..., FOR UPDATE)
_DML = re.compile(r"(?<!FOR )(?<!AFTER )(?<!BEFORE )(?<!ON )(?<!OR )(?<!OF )"
                  r"\b(INSERT\s+INTO|UPDATE|DELETE\s+FROM|COPY|MERGE\s+INTO|TRUNCATE)\b(?=\s+[\w\"])",
                  re.I)


def data_only_statements(sql: str) -> list[str]:
    """Row-modifying statements *outside* function bodies (dollar-quoted) and comments: a backfill would show here."""
    stripped = _COMMENT.sub("", _DOLLAR.sub("$body$", sql))
    return sorted({m.group(1).upper() for m in _DML.finditer(stripped)})


# ---------------------------------------------------------------------------------------------- traces

_DDL = re.compile(r"^\s*(?:--[^\n]*\n\s*)*(CREATE|ALTER|DROP|TRUNCATE|GRANT|REVOKE|COMMENT)\b", re.I)


def records(traces: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Every statement record of every process, in global grant order."""
    out = [{**r, "process": p} for p, t in traces.items() for r in t.get("records", [])]
    return sorted(out, key=lambda r: (r.get("grant") if r.get("grant") is not None else 10**9, r["process"],
                                      r["seq"]))


def permitted_violations(phase: str, recs: list[dict[str, Any]], *, ddl_allowed: bool,
                         versions: list[str]) -> list[str]:
    """Statements the phase doesn't permit: DDL outside a schema or migration step, a version the phase doesn't run,
    and 3.4.0's ``retry_job_v2`` from a 3.3.0 actor."""
    out = []
    allowed = {f"procrastinate-{v}" for v in versions}
    for r in recs:
        prov = r.get("provenance") or {}
        ver = prov.get("app_version", "")
        sql = r.get("sql") or ""
        if ver not in allowed:
            out.append(f"{phase}: {r['actor']} ran {ver or 'an unknown version'}, not permitted here")
        if _DDL.match(sql) and not ddl_allowed:
            out.append(f"{phase}: DDL by {r['actor']} outside a schema or migration step: {sql[:60]!r}")
        if "procrastinate_retry_job_v2" in sql and ver == f"procrastinate-{OLD}":
            out.append(f"{phase}: 3.3.0 actor {r['actor']} called retry_job_v2")
    return sorted(set(out))


def provenance(traces: dict[str, dict[str, Any]], log_sessions: dict[str, str] | None) -> dict[str, Any]:
    """Per actor: process, version, backend pid, transactions, statements, errors. ``consistent``: each actor's
    backend pid in the trace is the session the server log attributes to it."""
    actors: dict[str, dict[str, Any]] = {}
    for p, t in traces.items():
        for name, a in t.get("actors", {}).items():
            actors[name] = {"process": p, "backend_pid": a.get("backend_pid"), "statements": 0, "errors": 0,
                            "transactions": set(), "versions": set()}
    for r in records(traces):
        a = actors.setdefault(r["actor"], {"process": r["process"], "backend_pid": None, "statements": 0,
                                           "errors": 0, "transactions": set(), "versions": set()})
        prov = r.get("provenance") or {}
        a["statements"] += 1
        a["errors"] += r.get("outcome") != "ok"
        if prov.get("txn"):
            a["transactions"].add(prov["txn"])
        a["versions"].add(prov.get("app_version", ""))
    by_log = {v: int(k) for k, v in (log_sessions or {}).items()}
    out = {}
    for name, a in sorted(actors.items()):
        out[name] = {"process": a["process"], "versions": sorted(a["versions"]), "backend_pid": a["backend_pid"],
                     "transactions": len(a["transactions"]), "statements": a["statements"], "errors": a["errors"],
                     "log_pid": by_log.get(name)}
    consistent = all(v["log_pid"] == v["backend_pid"] for v in out.values() if v["statements"])
    return {"actors": out, "consistent_with_server_log": consistent}


def interleaving(traces: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """In global grant order: statements per version and the number of version switches."""
    seq = [(r.get("provenance") or {}).get("app_version", "") for r in records(traces) if r.get("grant") is not None]
    switches = sum(1 for x, y in zip(seq, seq[1:], strict=False) if x != y)
    return {"by_version": {v: seq.count(v) for v in sorted(set(seq))}, "switches": switches}


# ---------------------------------------------------------------------------------------------- quiescence

def quiescence(state: dict[str, Any], old_containers: list[str]) -> dict[str, Any]:
    checks = {"no_old_container": not old_containers,
              "no_client_session": not state.get("client_sessions"),
              "no_registered_worker": state.get("workers", 0) == 0,
              "no_doing_job": not [j for j in state.get("jobs", []) if j["status"] == "doing"],
              "no_foreign_lock": not state.get("foreign_locks"),
              "no_prepared_transaction": not state.get("prepared")}
    return {"quiesced": all(checks.values()), "checks": checks,
            "failed": sorted(k for k, ok in checks.items() if not ok),
            "todo_jobs_left_for_new_version": sorted(int(j["n"]) for j in state.get("jobs", [])
                                                     if j["status"] == "todo")}


def background_targets(state: dict[str, Any], reserved: set[int], limit: int = 3) -> list[int]:
    """Deterministic: the failed jobs (by n) not reserved for a later phase, at most ``limit``."""
    return sorted(int(j["n"]) for j in state.get("jobs", [])
                  if j["status"] == "failed" and int(j["n"]) not in reserved)[:limit]


# ---------------------------------------------------------------------------------------------- oracle

def classify(checked: dict[str, Any]) -> dict[str, Any]:
    """The frozen checker's entries, split by ERRATA.md. E1: the R2 *equality* (``... but attempts ...``). E2: an R5
    'after' read that isn't ``todo`` where the retry demonstrably did its documented effect: before ``failed``,
    attempts exactly one higher, and the job executed at least twice. Everything else is a genuine violation."""
    viol = checked.get("violations", {})
    admin = {(int(a["n"])): a for a in checked.get("admin_retries", []) if a.get("ok")}
    execs = checked.get("executions", {})
    e1, e2, genuine = [], [], []
    for rule, entries in sorted(viol.items()):
        for e in entries:
            if rule == "R2" and " but attempts " in e:
                e1.append(e)
                continue
            if rule == "R5" and e.startswith("admin retry of n="):
                m = re.match(r"admin retry of n=(\d+):", e)
                a = admin.get(int(m.group(1))) if m else None
                if a and (a.get("before") or {}).get("status") == "failed" and \
                        (a.get("after") or {}).get("attempts") == (a.get("before") or {}).get("attempts", -9) + 1 and \
                        (a.get("after") or {}).get("status") != "todo" and len(execs.get(m.group(1), [])) >= 2:
                    e2.append(e)
                    continue
            genuine.append(f"{rule}: {e}")
    return {"genuine_violations": genuine, "oracle_error_E1": e1, "oracle_error_E2": e2}
