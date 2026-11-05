"""EXPERIMENTAL trusted fixture: the Django 5.2 ledger app over stepped psycopg 3 connections.

Not part of any blind corpus. The handlers (``django_ledger/handlers.py``) are ordinary Django ORM code; the buggy
build reads the balance without a lock, the corrected build uses ``select_for_update()``. Django reaches the database
only through the trusted backend (``experimental.django_backend``), which hands over each actor thread's stepped
connection. Two aliases exercise both of Django's psycopg 3 binding modes:

* ``server``: ``OPTIONS["server_side_binding"] = True``: psycopg sends ``$n`` parameters;
* ``client``: Django's default: psycopg merges the values into the text, and the server log is compared against a
  digest of the exact text sent.

The schema comes from the app's own Django migration, applied by ``migrate`` through a stepped, corroborated run.
"""

from __future__ import annotations

import re
import zoneinfo
from typing import Any

from ..stepping import Handler, normal_sql

SCHEMA = "mvp_django"
AUTOCOMMIT = True  # Django manages autocommit itself (atomic blocks switch it off and commit or roll back)
ALIASES = {"server": True, "client": False}
SAVEPOINT = re.compile(r'^(SAVEPOINT|RELEASE SAVEPOINT|ROLLBACK TO SAVEPOINT) "s\d+_x\d+"$')
SETUP = frozenset(normal_sql(s) for s in ("SELECT set_config('TimeZone', %s, false)",))


def configure(target: Any, *, disable_server_side_cursors: bool = False) -> None:
    """Django settings for the disposable target only (``target.conninfo()`` refuses anything else)."""
    import django
    from django.conf import settings
    info = target.conninfo()
    if not settings.configured:
        databases = {}
        for alias, binding in {"default": True, **ALIASES}.items():
            databases[alias] = {"ENGINE": "deployspecter.experimental.django_backend", "NAME": info["dbname"],
                                "USER": info["user"], "PASSWORD": info["password"], "HOST": info["host"],
                                "PORT": str(info["port"]), "CONN_MAX_AGE": 0,
                                "OPTIONS": {"server_side_binding": binding},
                                "DISABLE_SERVER_SIDE_CURSORS": disable_server_side_cursors}
        settings.configure(DATABASES=databases, INSTALLED_APPS=["deployspecter.experimental.apps.django_ledger"],
                           USE_TZ=True, TIME_ZONE="UTC", DEFAULT_AUTO_FIELD="django.db.models.BigAutoField")
        django.setup()


def connection_options(*, binding: str = "server", **_: Any) -> dict[str, Any]:
    """The actor connection Django will be handed: the gated cursor for the binding mode, Django's adapters."""
    from django.db.backends.postgresql.psycopg_any import get_adapters_template

    from ..django_backend.base import cursor_class
    return {"cursor_factory": cursor_class(ALIASES[binding]),
            "context": get_adapters_template(True, zoneinfo.ZoneInfo("UTC"))}


class DjangoHandler(Handler):
    """A handler whose allowlist also admits Django's savepoint statements (their names carry the thread id)."""

    def allows(self, text: str) -> bool:
        return super().allows(text) or bool(SAVEPOINT.match(normal_sql(text)))


def _entry(fn: Any, *, alias: str, lock: bool) -> Any:
    def handler(conn: Any, **args: Any) -> Any:
        from django.db import connections

        from ..django_backend.base import handover
        handover(conn)
        try:
            return fn(alias=alias, lock=lock, **args)
        finally:
            connections[alias].close()  # Django closes its connection: the actor's session ends with the handler
    handler.__name__, handler.__qualname__, handler.__module__ = fn.__name__, fn.__qualname__, fn.__module__
    return handler


# The statements Django sends for these handlers (reviewed captures; the same templates in both binding modes).
_ACC = ('SELECT "ledger_account"."id", "ledger_account"."owner", "ledger_account"."opening", '
        '"ledger_account"."balance" FROM "ledger_account"')
GET = _ACC + ' WHERE "ledger_account"."id" = %s LIMIT 21'
GET_LOCKED = GET + " FOR UPDATE"
SAVE_BALANCE = 'UPDATE "ledger_account" SET "balance" = %s WHERE "ledger_account"."id" = %s'
CREATE_ENTRY = 'INSERT INTO "ledger_entry" ("account_id", "delta") VALUES (%s, %s) RETURNING "ledger_entry"."id"'
F_UPDATE = ('UPDATE "ledger_account" SET "balance" = ("ledger_account"."balance" + %s) '
            'WHERE "ledger_account"."id" = %s')
PAIR = _ACC + ' WHERE "ledger_account"."id" IN (%s, %s) ORDER BY "ledger_account"."id" ASC'
PAIR_LOCKED = PAIR + " FOR UPDATE"
BULK_ENTRIES = ('INSERT INTO "ledger_entry" ("account_id", "delta") SELECT * FROM UNNEST((%s)::integer[], '
                '(%s)::bigint[]) RETURNING "ledger_entry"."id"')  # Django 5.2's bulk_create form


def registry(variant: str, *, binding: str = "server") -> dict[tuple[str, str], Handler]:
    from .django_ledger import handlers as h
    if variant not in ("buggy", "fixed"):
        raise ValueError("variant must be buggy or fixed")
    lock = variant == "fixed"
    get, pair = (GET_LOCKED, PAIR_LOCKED) if lock else (GET, PAIR)
    money = {"account_id": int, "amount": int}

    def allow(*texts: str) -> frozenset[str]:
        return frozenset(normal_sql(t) for t in texts)
    out = [DjangoHandler("v1", "withdraw", _entry(h.withdraw, alias=binding, lock=lock),
                         allow(get, SAVE_BALANCE, CREATE_ENTRY), money, setup_sql=SETUP),
           DjangoHandler("v1", "deposit", _entry(h.deposit, alias=binding, lock=lock),
                         allow(F_UPDATE, CREATE_ENTRY), money, setup_sql=SETUP),
           DjangoHandler("v1", "transfer", _entry(h.transfer, alias=binding, lock=lock),
                         allow(pair, SAVE_BALANCE, BULK_ENTRIES), {"src": int, "dst": int, "amount": int},
                         setup_sql=SETUP)]
    return {(x.app_version, x.name): x for x in out}


# ------------------------------------------------------------------ state, independent of Django

STATE_SQL = {"accounts": f"SELECT id, owner, opening, balance FROM {SCHEMA}.ledger_account ORDER BY id",
             "entries": f"SELECT id, account_id, delta FROM {SCHEMA}.ledger_entry ORDER BY id"}


def reset(conn: Any, seed: dict[str, list[dict[str, Any]]]) -> None:
    """Empty the migrated tables and insert the seed, on the admin connection (the schema is Django's migration's)."""
    with conn.transaction():
        conn.execute(f"TRUNCATE {SCHEMA}.ledger_entry, {SCHEMA}.ledger_account RESTART IDENTITY")
        for a in seed.get("accounts", []):
            conn.execute(f"INSERT INTO {SCHEMA}.ledger_account (id, owner, opening, balance) VALUES (%s, %s, %s, %s)",
                         (a["id"], a["owner"], a["opening"], a["balance"]))


def read_state(conn: Any) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    with conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        for table, text in STATE_SQL.items():
            cur = conn.execute(text)
            names = [d.name for d in cur.description]
            out[table] = [dict(zip(names, row, strict=True)) for row in cur.fetchall()]
    return out


def expected_initial(seed: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    return {"accounts": [{"id": a["id"], "owner": a["owner"], "opening": a["opening"], "balance": a["balance"]}
                         for a in sorted(seed.get("accounts", []), key=lambda a: a["id"])], "entries": []}


def check(state: dict[str, list[dict[str, Any]]]) -> list[str]:
    """From the specification: L1 a balance is its opening plus its entries; L2 no balance is negative."""
    sums: dict[int, int] = {}
    for e in state["entries"]:
        sums[e["account_id"]] = sums.get(e["account_id"], 0) + e["delta"]
    out = []
    for a in state["accounts"]:
        if a["balance"] != a["opening"] + sums.get(a["id"], 0):
            out.append(f"L1 account {a['id']}: balance {a['balance']} but opening {a['opening']} plus entries "
                       f"{sums.get(a['id'], 0)}")
        if a["balance"] < 0:
            out.append(f"L2 account {a['id']}: balance {a['balance']} is negative")
    return out


# ------------------------------------------------------------------ the schema, from the app's own migration


class CaptureHandler(Handler):
    def allows(self, text: str) -> bool:  # Django's migration and introspection SQL: recorded, not reviewed
        return True


def migrate_through_stepping(target: Any) -> tuple[Any, dict[str, Any]]:
    """Drop and recreate the schema, then run ``manage.py migrate django_ledger`` as one stepped actor, so the
    migration's DDL and Django's introspection go through the gate and the server log.

    Returns (trace, corroboration)."""
    from django.core.management import call_command

    from .. import harness as hz
    from ..stepping import Actor, Limits, Run, actor_connection_factory
    admin = target.admin()
    try:
        admin.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        admin.execute(f"CREATE SCHEMA {SCHEMA}")

        def migrate(conn: Any) -> str:
            from django.db import connections

            from ..django_backend.base import handover
            handover(conn)
            try:
                call_command("migrate", "django_ledger", database="server", verbosity=0, interactive=False)
            finally:
                connections["server"].close()
            return "migrated"

        lim = Limits(statement_ms=20000, run_s=60)
        actor = Actor("migrate", CaptureHandler("django", "migrate", migrate, frozenset(), {}), {})
        connect = actor_connection_factory(target.conninfo(), lim, search_path=SCHEMA, autocommit=True,
                                           **connection_options(binding="server"))
        run = Run([actor], connect, admin, limits=lim, run_id="django_migrate")
        return hz.corroborated(admin, run, user=target.user)
    finally:
        admin.close()
