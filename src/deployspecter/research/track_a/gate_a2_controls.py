"""Gate A2.1: the controls Gate A2 takes over from Gate A1, as pure checks. Research-only; opens no connection.

* The synthetic target is fixed by the manifest and decided before any connection. The protected development database
  is a forbidden target, never a fallback.
* One reviewed credential source (DATABASE_URL in the git-ignored ``.env``). Model credentials, model modules and libpq
  environment variables are refused.
* psycopg's automatic preparation is off on every connection Gate A2 opens, as in Gate A1's characterization.
* Gate 2A's per-run reset is spelled out from its source, so a later whole-batch audit can hold it to a fixed sequence.
* Each Gate 2A attempt gets a record tying its initial state to the verified one.

Nothing here is a sandbox or a privilege boundary. The parent is a superuser on a disposable cluster, and Gate 2A's own
per-run reset, initial read and role setup are still unreconciled (see GATE_A2_1_TRUST_BOUNDARY.md). Messages name
keys and variables, never their values, so a password can't reach a record.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from typing import Any
from urllib.parse import quote

from deployspecter.database import RUN_SCHEMA, RUNNER_ROLE

from . import catalog_export as ce
from . import characterization as ch
from . import evidence_binding as eb
from . import pipeline as p

PREPARE_THRESHOLD = None  # Gate A1's characterized policy: nothing is prepared, so every statement logs unnamed
HARNESS_APP = "ds_gate2a_harness"  # names Gate 2A's admin and monitor sessions in the server log
CREDENTIAL_SOURCE = "DATABASE_URL in the git-ignored .env"
INITIAL_SOURCE = "Gate 2A's admin-session snapshot after its own reset; not reconciled (blocker B1)"

TARGET_KEYS = ("host", "hostaddr", "port", "database", "initial_database", "admin_user", "container_name",
               "cluster_name", "authentication_method")
# Model credentials and endpoints, by variable name. Only names are reported.
MODEL_ENV = re.compile(r"^(MODEL_\w*|\w*API_KEY\w*|OPENAI_\w*|ANTHROPIC_\w*|META_AI_\w*)$")
# Every libpq variable fills in what a connection string leaves out: PGHOSTADDR reroutes, PGOPTIONS can change
# logging, PGSERVICE and PGPASSFILE add other sources. None may be set.
LIBPQ_ENV = re.compile(r"^PG\w*$")
MODEL_MODULES = ("deployspecter.meta_client", "deployspecter.guided", "deployspecter.analyzer", "deployspecter.api",
                 "deployspecter.cli", "deployspecter.benchmark", "deployspecter.pipeline", "openai", "anthropic",
                 "httpx")
# Column types whose value is the same through Gate 2A's parent read and a reconciled row_to_json read.
COMPARABLE_TYPES = frozenset({"int2", "int4", "int8", "text", "varchar", "bool"})

# What Gate 2A's Sandbox.reset sends after the setup transaction (src/deployspecter/database.py), for the audit model.
GATE2A_GRANTS = (f"GRANT USAGE ON SCHEMA {RUN_SCHEMA} TO {RUNNER_ROLE}",
                 f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA {RUN_SCHEMA} TO {RUNNER_ROLE}",
                 f"GRANT USAGE ON ALL SEQUENCES IN SCHEMA {RUN_SCHEMA} TO {RUNNER_ROLE}")


class ControlRefused(ValueError):
    """A Gate A2 control failed. Nothing connects, nothing is retried."""


# ------------------------------------------------------------------ the fixed target


def target(manifest: Mapping[str, Any]) -> dict[str, str]:
    """The manifest's one synthetic target. It must be loopback, complete, and none of the forbidden targets."""
    t = manifest.get("target")
    if not isinstance(t, Mapping) or any(not t.get(k) for k in TARGET_KEYS):
        raise ControlRefused("the manifest doesn't fix the synthetic target completely")
    forbidden = manifest.get("forbidden_targets")
    if not isinstance(forbidden, list) or not forbidden:
        raise ControlRefused("the manifest doesn't name the forbidden targets")
    out = {k: str(t[k]) for k in TARGET_KEYS}
    if (out["host"], out["hostaddr"]) != ("127.0.0.1", "127.0.0.1"):
        raise ControlRefused("the target must be the loopback address")
    if not out["database"].endswith("_test"):
        raise ControlRefused("the target database must be a test database")
    for f in forbidden:
        if str(f.get("port")) == out["port"] or f.get("container") == out["container_name"]:
            raise ControlRefused("the manifest's target is one of its forbidden targets")
    return out


def connection_keywords(dsn: Mapping[str, Any], manifest: Mapping[str, Any]) -> dict[str, dict[str, str]]:
    """Keywords for the target's two databases, from the manifest. The DSN may only agree with it and add the password.

    ``dsn`` is DATABASE_URL parsed without connecting (psycopg's conninfo_to_dict). Its database is the initial one,
    which Gate 2A's Sandbox uses to create the test database and the runner role.
    """
    t = target(manifest)
    extra = sorted(set(dsn) - ch.DSN_KEYS)
    if extra:
        raise ControlRefused(f"DATABASE_URL has connection keys the manifest doesn't allow: {extra}")
    for f in manifest["forbidden_targets"]:
        if str(dsn.get("port")) == str(f.get("port")):
            raise ControlRefused("DATABASE_URL points at a forbidden target; there is no fallback")
    want = {"host": t["host"], "port": t["port"], "dbname": t["initial_database"], "user": t["admin_user"]}
    wrong = sorted(k for k, v in want.items() if str(dsn.get(k)) != v)
    if wrong:
        raise ControlRefused(f"DATABASE_URL doesn't point at the manifest's fixed target (differs on {wrong})")
    if not dsn.get("password"):
        raise ControlRefused("DATABASE_URL has no password")
    base = {"host": t["host"], "hostaddr": t["hostaddr"], "port": t["port"], "user": t["admin_user"],
            "password": str(dsn["password"])}
    return {"admin": {**base, "dbname": t["initial_database"]}, "test": {**base, "dbname": t["database"]}}


def harness_url(keywords: Mapping[str, str]) -> str:
    """A URL for Gate 2A's Sandbox that names the address itself, so PGHOSTADDR can't reroute it."""
    q = lambda s: quote(str(s), safe="")  # noqa: E731
    return (f"postgresql://{q(keywords['user'])}:{q(keywords['password'])}@{keywords['host']}:{keywords['port']}/"
            f"{q(keywords['dbname'])}?hostaddr={keywords['hostaddr']}&application_name={HARNESS_APP}"
            "&connect_timeout=5")


def harness_settings(keywords: Mapping[str, Mapping[str, str]]) -> Any:
    """Settings for Gate 2A built from the fixed target only, with no model key."""
    from deployspecter.config import Settings
    return Settings(admin_dsn=harness_url(keywords["admin"]), test_dsn=harness_url(keywords["test"]),
                    model_api_key=None)


def info_problems(name: str, info: Any, want: Mapping[str, str]) -> list[str]:
    got = (info.host, getattr(info, "hostaddr", None), str(info.port), info.dbname, info.user)
    if got != (want["host"], want["hostaddr"], want["port"], want["dbname"], want["user"]):
        return [f"{name} isn't connected to the manifest's fixed target"]
    return []


def apply_harness_policy(box: Any, want: Mapping[str, str], *, reader_pid: int | None = None) -> list[str]:
    """B5 option 1: turn off automatic preparation on Gate 2A's admin and monitor sessions, through psycopg's public
    ``prepare_threshold`` property, after ``Sandbox`` returns and before the supervisor sends anything.

    Shown from the real code, not only fakes (tests/research_tracks/test_gate_a2_3.py): ``Sandbox.__init__`` assigns
    the two public attributes once and sends three distinct statements on ``admin`` exactly once each, so psycopg's
    default threshold (5) can't have prepared any; ``SupervisedRunner.__init__`` sends nothing and reads
    ``self.box.admin`` at every call; and the property's setter is pure Python, so setting it sends nothing. Nothing
    else is touched: no global psycopg setting, no replaced object, no patched method.
    """
    problems, pids = [], []
    for name in ("admin", "monitor"):
        conn = getattr(box, name, None)
        prop = getattr(type(conn), "prepare_threshold", None)
        if conn is None or not isinstance(prop, property) or prop.fset is None:
            problems.append(f"Gate 2A's {name} session has no supported prepare_threshold property")
            continue
        conn.prepare_threshold = PREPARE_THRESHOLD
        if getattr(conn, "prepare_threshold", "unknown") is not PREPARE_THRESHOLD:
            problems.append(f"Gate 2A's {name} session would still prepare statements")
        problems += info_problems(f"Gate 2A's {name} session", conn.info, want)
        pids.append(conn.info.backend_pid)
    if len(pids) == 2 and pids[0] == pids[1]:
        problems.append("Gate 2A's admin and monitor sessions are the same backend")
    if reader_pid is not None and reader_pid in pids:
        problems.append("a Gate 2A harness session is the Gate A2 reader's backend")
    return problems


# ------------------------------------------------------------------ credentials and model refusal


def environment_problems(environ: Mapping[str, str]) -> list[str]:
    names = sorted(environ)
    problems = []
    model = [n for n in names if MODEL_ENV.match(n.upper())]
    if model:
        problems.append(f"model credentials or settings are in the environment: {model}")
    libpq = [n for n in names if LIBPQ_ENV.match(n.upper())]
    if libpq:
        problems.append(f"libpq environment variables could redirect or reconfigure a connection: {libpq}")
    if "DATABASE_URL" in environ:
        problems.append(f"DATABASE_URL is set in the environment; the only reviewed source is {CREDENTIAL_SOURCE}")
    return problems


def dotenv_problems(values: Mapping[str, str | None]) -> list[str]:
    keys = sorted(values)
    if keys != ["DATABASE_URL"]:
        model = [k for k in keys if MODEL_ENV.match(k.upper())]
        return ([f"the .env holds model credentials or settings: {model}"] if model else []) + [
            f".env must hold DATABASE_URL and nothing else; it holds {keys}"]
    if not values["DATABASE_URL"]:
        return ["DATABASE_URL in .env is empty"]
    return []


def module_problems(modules: Iterable[str]) -> list[str]:
    loaded = sorted(m for m in modules if any(m == x or m.startswith(x + ".") for x in MODEL_MODULES))
    return [f"model-related modules are imported: {loaded}"] if loaded else []


# ------------------------------------------------------------------ resets and statement sets


def gate2a_reset_statements(setup_sql: str, seed: list[Any]) -> list[tuple[str, tuple]]:
    """What Gate 2A's Sandbox.reset sends on its admin session: Gate A1's reconciled reset, then three GRANTs.

    A model for the whole-batch audit, checked offline against Sandbox.reset itself. Gate A2's own resets are Gate
    A1's ``reset_statements`` exactly; the catalog export reads no ACLs, so the GRANTs don't change its digest.
    """
    return [*ch.reset_statements(setup_sql, seed), *((g, ()) for g in GATE2A_GRANTS)]


def statement_sets(setup_sql: str, chain: Any, column_types: dict[tuple[str, str], str]) -> dict[str, list]:
    """Every statement list Gate A2 can send or expects, by operation. Pinned in the manifest by digest."""
    expected = p.expected_initial_state(chain)
    return {"op0p_preflight": [(t, ()) for t in ch.PREFLIGHT],
            "op0r_reset_empty": ch.reset_statements(setup_sql, []),
            "op0r_reset_seeded": ch.reset_statements(setup_sql, list(chain.seed)),
            "op2a_op2b_export": [(t, ()) for _, t in ce.EXPORT_QUERIES],
            "op2c_cross_check": [(t, ()) for _, t in ce.CROSS_CHECK_QUERIES],
            "op6i_typed_check": list(eb.independent_statements(expected, column_types)),
            "op6f_typed_read": [(t, ()) for t in eb.final_read_statements(sorted(expected))],
            "op9c_cleanup": ch.cleanup_statements(),
            "gate2a_reset_model": gate2a_reset_statements(setup_sql, list(chain.seed))}


def set_digest(statements: list) -> str:
    """The digest Gate A1's manifest uses for its statement sets."""
    return hashlib.sha256(json.dumps(statements, default=repr).encode()).hexdigest()


def snapshot_type_problems(snapshot: Mapping[str, Any]) -> list[str]:
    """The verified initial digest compares Gate 2A's parent read with a reconciled row_to_json read. They agree only
    for these types (Sandbox.snapshot masks times and turns numerics into strings)."""
    names = {t["oid"]: t["typname"] for t in snapshot["types"]}
    bad = sorted(f"{a['attrelid']}.{a['attname']}" for a in snapshot["attributes"]
                 if not a["attisdropped"] and names.get(a["atttypid"]) not in COMPARABLE_TYPES)
    return [f"columns whose two reads aren't comparable: {bad}"] if bad else []


# ------------------------------------------------------------------ per-attempt state records


def attempt_state(attempt: str, trace: Any, *, variant: str, scenario_sha: str, verified_initial_digest: str,
                  expected: dict[str, list[dict[str, tuple[str, Any]]]],
                  independent_final: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """One Gate 2A attempt's state evidence, tied to its run id, its scenario and the verified initial state."""
    problems = []
    run_id = trace.evidence.run_id
    if not run_id:
        problems.append("the attempt has no Gate 2A run id")
    initial = eb.canonical_digest(trace.initial_state)
    if initial != verified_initial_digest:
        problems.append("the attempt didn't start from the verified initial state")
    problems += [f"initial state: {x}" for x in p.check_initial_state(expected, trace.initial_state)]
    final, independent = eb.state_digest(trace.final_state), eb.state_digest(independent_final)
    if final != independent:
        problems.append("the reconciled final read disagrees with Gate 2A's final read")
    return {"attempt": attempt, "variant": variant, "run_id": run_id, "scenario_digest": scenario_sha,
            "initial_digest": initial, "verified_initial_digest": verified_initial_digest,
            "initial_source": INITIAL_SOURCE, "final_digest": final, "independent_final_digest": independent,
            "problems": problems}


class Attempts:
    """Numbers attempts in the order they ran and refuses a run id seen twice."""

    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []
        self._run_ids: set[str] = set()

    def record(self, phase: str, trace: Any, **known: Any) -> dict[str, Any]:
        rec = attempt_state(f"{phase}-{len(self.records) + 1:04d}", trace, **known)
        if rec["run_id"] in self._run_ids:
            rec["problems"].append("a Gate 2A run id was recorded twice")
        self._run_ids.add(rec["run_id"])
        self.records.append(rec)
        return rec
