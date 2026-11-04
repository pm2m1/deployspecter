"""Gate A2.3, B2 option A: a disclosed trusted-control-plane exception for Gate 2A's unlogged role creation.

Research-only and pure. The owner chose option A for characterization only:

* The interval in which the supervisor's admin session runs with ``log_statement='none'`` stays UNOBSERVABLE. Nothing
  here marks what ran inside it as verified, and a clean catalog afterwards is not proof of correct intermediate
  execution.
* Outside the protected window only, in the reader segments before and after a Gate 2A call, a reconciled read-only
  parent session reads the role catalog (``ROLE_CATALOG``). The one permitted claim is that those observed states meet
  the stated before/after conditions, subject to what the catalog can show.

Gate A1's reconciler accepts only its ten operation tags, so the observation runs under the ``op0p`` tag with its
own pinned statement set; records name the set, so it isn't mistaken for Gate A1's preflight.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from deployspecter.database import RUNNER_ROLE

from . import gate2a_audit as ga

OP = "op0p"
STATEMENT_SET = "gate2a_role_catalog"
RUN_ROLE = re.compile(r"^ds_run_[0-9a-f]{12}$")

ROLE_CATALOG: tuple[tuple[str, str], ...] = (
    ("begin", "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY"),
    ("roles", "SELECT rolname::text, rolsuper, rolinherit, rolcreaterole, rolcreatedb, rolcanlogin, rolreplication, "
              "rolbypassrls, rolconnlimit FROM pg_roles WHERE rolname !~ '^pg_' ORDER BY 1"),
    ("memberships", "SELECT r.rolname::text, m.rolname::text, a.admin_option FROM pg_auth_members a "
                    "JOIN pg_roles r ON r.oid = a.roleid JOIN pg_roles m ON m.oid = a.member "
                    "WHERE m.rolname !~ '^pg_' ORDER BY 1, 2"),
    ("role_settings", "SELECT coalesce(d.datname, '')::text, coalesce(r.rolname, '')::text, s.setconfig::text "
                      "FROM pg_db_role_setting s LEFT JOIN pg_database d ON d.oid = s.setdatabase "
                      "LEFT JOIN pg_roles r ON r.oid = s.setrole ORDER BY 1, 2"),
    ("database_acls", "SELECT datname::text, coalesce(datacl::text, '') FROM pg_database ORDER BY 1"),
    ("commit", "COMMIT"),
)
ROLE_COLUMNS = ("rolsuper", "rolinherit", "rolcreaterole", "rolcreatedb", "rolcanlogin", "rolreplication",
                "rolbypassrls", "rolconnlimit")
# What the runner role's DO block declares (database.py): NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE, and the defaults.
RUNNER_ATTRIBUTES = {"rolsuper": False, "rolinherit": True, "rolcreaterole": False, "rolcreatedb": False,
                     "rolcanlogin": False, "rolreplication": False, "rolbypassrls": False, "rolconnlimit": -1}


def statements() -> list[tuple[str, tuple]]:
    return [(text, ()) for _, text in ROLE_CATALOG]


@dataclass(frozen=True)
class Observation:
    """One reconciled read of the role catalog, taken in a reader segment (never inside Gate 2A's window)."""
    label: str  # "before bootstrap", "before run <n>", "after run <n>"
    roles: dict[str, dict[str, Any]]
    memberships: tuple[tuple[str, str, bool], ...]
    role_settings: tuple[tuple[str, str, str], ...]
    database_acls: dict[str, str]


def read(conn: Any, label: str) -> Observation:
    """Run ROLE_CATALOG on a tagged parent session and parse it. A malformed answer refuses."""
    out: dict[str, list[tuple]] = {}
    for name, text in ROLE_CATALOG:
        cursor = conn.execute(text)
        if text.startswith("SELECT"):
            out[name] = [tuple(r) for r in cursor.fetchall()]
    try:
        roles = {r[0]: dict(zip(ROLE_COLUMNS, r[1:], strict=True)) for r in out["roles"]}
        memberships = tuple((str(a), str(b), bool(c)) for a, b, c in out["memberships"])
        settings = tuple((str(a), str(b), str(c)) for a, b, c in out["role_settings"])
        acls = {str(a): str(b) for a, b in out["database_acls"]}
    except (ValueError, TypeError) as exc:
        raise ga.AuditError(f"the role catalog answer is malformed: {exc}") from None
    if len(roles) != len(out["roles"]) or len(acls) != len(out["database_acls"]):
        raise ga.AuditError("the role catalog answer repeats a name")
    return Observation(label, roles, memberships, settings, acls)


def _run_role_traces(o: Observation) -> list[str]:
    """Any trace of a Gate 2A run role: a role, a membership, a setting or a database privilege."""
    found = [f"role {r}" for r in o.roles if RUN_ROLE.match(r) or r.startswith("ds_run_")]
    found += [f"membership of {b} in {a}" for a, b, _ in o.memberships if "ds_run_" in a or "ds_run_" in b]
    found += [f"setting for {r}" for _, r, _ in o.role_settings if r.startswith("ds_run_")]
    found += [f"privilege on {d}" for d, acl in o.database_acls.items() if "ds_run_" in acl]
    return found


def _public_connect(acl: str) -> bool:
    """A PUBLIC entry (an aclitem with an empty grantee) carrying CONNECT ('c'). An empty ACL means the default,
    which grants PUBLIC CONNECT and TEMP."""
    if acl == "":
        return True
    items = acl.strip("{}").split(",") if acl.strip("{}") else []
    return any(item.startswith("=") and "c" in item.split("/")[0][1:] for item in items)


@dataclass
class Conditions:
    """The before/after conditions for one Gate 2A call, and nothing more."""
    admin_user: str
    connectable_databases: Sequence[str]
    hardened_after: bool  # the call was the runner's first, so Gate 2A's hardening ran in it
    problems: list[str] = field(default_factory=list)


def judge(before: Observation | None, after: Observation | None, conditions: Conditions, scope: str,
          cov: ga.Coverage) -> None:
    """The claims option A permits around one Gate 2A call. A missing observation is INDETERMINATE."""
    claim = "role catalog meets the before/after conditions"
    if before is None or after is None:
        cov.add(scope, claim, ga.INDETERMINATE,
                f"no {'before' if before is None else 'after'} observation; nothing is concluded")
    else:
        problems = []
        for o in (before, after):
            problems += [f"{o.label}: {t}" for t in _run_role_traces(o)]
            admin = o.roles.get(conditions.admin_user)
            if admin is None or admin["rolsuper"] is not True:
                problems.append(f"{o.label}: the admin role {conditions.admin_user} isn't the superuser it should be")
            runner = o.roles.get(RUNNER_ROLE)
            if runner != RUNNER_ATTRIBUTES:
                problems.append(f"{o.label}: {RUNNER_ROLE} doesn't have the attributes its DO block declares")
        if before.roles != after.roles:
            problems.append("roles or role attributes changed across the call")
        if before.memberships != after.memberships:
            problems.append("role memberships changed across the call")
        if before.role_settings != after.role_settings:
            problems.append("role settings changed across the call")
        if set(before.database_acls) != set(after.database_acls):
            problems.append("the set of databases changed across the call")
        for db in conditions.connectable_databases:
            if db not in after.database_acls:
                problems.append(f"{after.label}: database {db} isn't in the catalog")
            elif conditions.hardened_after and _public_connect(after.database_acls[db]):
                problems.append(f"{after.label}: PUBLIC can still connect to {db} after Gate 2A's hardening")
            elif not conditions.hardened_after and before.database_acls.get(db) != after.database_acls[db]:
                problems.append(f"database privileges on {db} changed across a call that doesn't harden")
        cov.add(scope, claim, ga.REFUTED if problems else ga.VERIFIED,
                "; ".join(problems[:4]) if problems else
                "before and after: no run role, membership, setting or database privilege left by any run; roles, "
                "memberships and settings identical; the admin role superuser; the runner role exactly as its DO "
                "block declares; PUBLIC CONNECT revoked once hardening has run")
    cov.add(scope, "the run role's attributes and privileges inside the unlogged interval", ga.UNOBSERVABLE,
            "option A exception: CREATE ROLE runs with log_statement='none', and the catalog is read only before and "
            "after the call; a change made and undone in between can't be seen")
    cov.add(scope, "the role-creation SQL itself", ga.UNOBSERVABLE,
            "unlogged by design; a clean catalog afterwards is not proof of correct intermediate execution")


def judge_bootstrap(before: Observation | None, after: Observation | None, conditions: Conditions, scope: str,
                    cov: ga.Coverage) -> None:
    """Around Sandbox construction: the only change allowed is the runner role appearing (or staying) exactly as its
    DO block declares. That shows the role's state afterwards, not what the DO block executed."""
    claim = "role catalog around the sandbox bootstrap"
    if before is None or after is None:
        cov.add(scope, claim, ga.INDETERMINATE, "a before or after observation is missing")
        return
    problems = [f"{o.label}: {t}" for o in (before, after) for t in _run_role_traces(o)]
    if after.roles.get(RUNNER_ROLE) != RUNNER_ATTRIBUTES:
        problems.append(f"{RUNNER_ROLE} doesn't have the attributes its DO block declares")
    if before.roles.get(RUNNER_ROLE) not in (None, RUNNER_ATTRIBUTES):
        problems.append(f"{RUNNER_ROLE} existed before the bootstrap with other attributes")
    others = lambda o: {k: v for k, v in o.roles.items() if k != RUNNER_ROLE}  # noqa: E731
    if others(before) != others(after):
        problems.append("roles other than the runner role changed")
    if (before.memberships, before.role_settings, before.database_acls) != (
            after.memberships, after.role_settings, after.database_acls):
        problems.append("memberships, role settings or database privileges changed")
    cov.add(scope, claim, ga.REFUTED if problems else ga.VERIFIED, "; ".join(problems[:4]) if problems else
            f"afterwards {RUNNER_ROLE} has exactly its declared attributes and nothing else changed")


def interval_contract(run: ga.AdminRun, cov: ga.Coverage) -> dict[str, Any]:
    """The disclosed interval, as far as the log can bound it: the admin session's logged SET, then the first logged
    admin row after it, which must be this run's GRANT CONNECT."""
    ctx, m = run.ctx, run.marks
    contract = {"session": "Gate 2A's Sandbox admin session", "admin_pid": None, "role": ctx.role,
                "role_derivation": "ds_run_ + the run id Gate 2A chose (trace.evidence.run_id)",
                "starts_after": None, "ends_before": None,
                "unlogged_by_source": ["CREATE ROLE <role> LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION "
                                       "NOBYPASSRLS PASSWORD '<verifier>' CONNECTION LIMIT <actors + 1> IN ROLE "
                                       f"{RUNNER_ROLE}", "RESET log_statement"],
                "log_cannot_show": ["the statements actually run in the interval", "the role's attributes while it "
                                    "existed", "whether the verifier or password changed", "results of any query"]}
    start, end = m.get("log_off"), m.get("log_on")
    if start is None or end is None:
        cov.add(run.scope, "unlogged interval bounded by the log", ga.INDETERMINATE,
                "the log doesn't show both the SET log_statement and the next logged admin statement")
        return contract
    contract.update(admin_pid=start[ga.pr.PID], starts_after=f"line {start[ga.pr.LINE]}: {start.message}",
                    ends_before=f"line {end[ga.pr.LINE]}: {end.message}")
    consecutive = int(end[ga.pr.LINE]) == int(start[ga.pr.LINE]) + 1
    cov.add(run.scope, "unlogged interval bounded by the log", ga.VERIFIED if consecutive else ga.REFUTED,
            "the admin session's SET log_statement and its next logged row (this run's GRANT CONNECT) are on "
            "consecutive log lines" if consecutive else "admin log lines inside the interval are missing")
    return contract


def observations_from(records: Mapping[str, Observation]) -> dict[str, dict[str, Any]]:
    return {k: {"roles": v.roles, "memberships": v.memberships, "role_settings": v.role_settings,
                "database_acls": v.database_acls} for k, v in records.items()}
