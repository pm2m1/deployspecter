"""D3 validation of the Django seam on paperless-ngx v3.4.2 (results/django_seam_validation/PLAN.md).

Runs inside the paperless-ngx image, with ``DJANGO_SETTINGS_MODULE=ds_seam_settings``:

    paperless_seam.py migrate --out DIR      # seam mode: phase 1
    paperless_seam.py seed    --out DIR      # stock mode: phase 2 (and P1's showmigrations)
    paperless_seam.py ops     --out DIR      # seam mode: phase 3, 5 workloads x 5 schedules
    paperless_seam.py stock   --out DIR      # stock mode: phase 4
    paperless_seam.py verdict --out DIR      # no Django: P1-P4 from the evidence, VERDICT.json

Every seam run is recorded whatever happens; nothing is retried.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import random
import shutil
import sys
import time
from pathlib import Path
from typing import Any

WORKLOADS = ("W1", "W2", "W3", "W4", "W5")
SCHEDULES = ("S1", "S2", "S3", "S4", "S5")
SEED_SCHEMA = "ds_seed"
VARIANT = "paperless-v3.4.2"


def schedule(name: str) -> list[str]:
    if name == "S1":
        return ["a"] * 400 + ["b"] * 400
    if name == "S2":
        return ["b"] * 400 + ["a"] * 400
    if name == "S3":
        return ["a", "b"] * 400
    rng = random.Random({"S4": 1, "S5": 2}[name])
    return [rng.choice(["a", "b"]) for _ in range(400)]


def requests_for(w: str, ids: dict[str, int]) -> dict[str, tuple[str, str, Any]]:
    d1, d2, inbox, paid = ids["doc-1"], ids["doc-2"], ids["t-inbox"], ids["t-paid"]
    return {
        "W1": {"a": ("post", "/api/tags/", {"name": "dup"}), "b": ("post", "/api/tags/", {"name": "dup"})},
        "W2": {"a": ("patch", f"/api/documents/{d1}/", {"title": "A"}),
               "b": ("patch", f"/api/documents/{d1}/", {"title": "B"})},
        "W3": {"a": ("post", "/api/documents/bulk_edit/",
                     {"documents": [d1, d2], "method": "add_tag", "parameters": {"tag": paid}}),
               "b": ("post", "/api/documents/bulk_edit/",
                     {"documents": [d1, d2], "method": "remove_tag", "parameters": {"tag": inbox}})},
        "W4": {"a": ("delete", f"/api/tags/{paid}/", None),
               "b": ("patch", f"/api/documents/{d2}/", {"tags": [paid]})},
        "W5": {"a": ("post", f"/api/documents/{d1}/notes/", {"note": "from a"}),
               "b": ("post", f"/api/documents/{d1}/notes/", {"note": "from b"})},
    }[w]


# ------------------------------------------------------------------ database helpers (admin connection, plain SQL)

def target() -> Any:
    from deployspecter.experimental import harness as hz
    return hz.Target("127.0.0.1", 5432, "ds_mvp", Path("/run/ds_secret").read_text().strip())


def app_tables(conn: Any, schema: str = "public") -> list[str]:
    return [r[0] for r in conn.execute(
        "SELECT tablename FROM pg_tables WHERE schemaname = %s AND tablename <> 'django_migrations' "
        "ORDER BY tablename", (schema,)).fetchall()]


def read_schema(conn: Any, schema: str) -> dict[str, list[dict[str, Any]]]:
    """D5's generic read: every application table, ``SELECT to_jsonb(t) FROM t ORDER BY 1``, TimeZone UTC."""
    out: dict[str, list[dict[str, Any]]] = {}
    with conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        conn.execute("SET LOCAL TimeZone = 'UTC'")
        for t in app_tables(conn, schema):
            rows = conn.execute(f'SELECT to_jsonb(t) FROM "{schema}"."{t}" t ORDER BY 1').fetchall()
            out[t] = [r[0] for r in rows]
    return out


def restore(conn: Any) -> None:
    tables = app_tables(conn)
    with conn.transaction():
        conn.execute("SET LOCAL session_replication_role = replica")
        conn.execute("TRUNCATE " + ", ".join(f'public."{t}"' for t in tables) + " RESTART IDENTITY")
        for t in tables:
            conn.execute(f'INSERT INTO public."{t}" SELECT * FROM {SEED_SCHEMA}."{t}"')
        seqs = conn.execute(
            "SELECT c.table_name, c.column_name, pg_get_serial_sequence(format('public.%I', c.table_name), "
            "c.column_name) FROM information_schema.columns c WHERE c.table_schema = 'public' "
            "AND (c.is_identity = 'YES' OR c.column_default LIKE 'nextval%%')").fetchall()
        for t, col, seq in seqs:
            if seq and t != "django_migrations":
                conn.execute(f'SELECT setval(%s, coalesce((SELECT max("{col}") FROM public."{t}"), 1), '
                             f'(SELECT count(*) > 0 FROM public."{t}"))', (seq,))
    shutil.rmtree("/usr/src/paperless/data/index", ignore_errors=True)


# ------------------------------------------------------------------ the harness app module (seam mode)

class App:
    """``harness.run_app``'s app protocol over paperless-ngx's REST API."""

    SCHEMA = "public"
    AUTOCOMMIT = True

    def __init__(self, ids: dict[str, int], w: str) -> None:
        self.ids, self.w = ids, w

    def registry(self, variant: str, binding: str = "client") -> dict[tuple[str, str], Any]:
        from deployspecter.experimental.apps.django_fixture import SETUP, DjangoHandler
        if variant != VARIANT or binding != "client":
            raise ValueError("one variant, the application's own (client-side) binding")

        class Any_(DjangoHandler):
            def allows(self, text: str) -> bool:  # an application's ORM SQL: recorded and corroborated, not listed
                return True
        out = {}
        for actor, (method, path, body) in requests_for(self.w, self.ids).items():
            out[(VARIANT, actor)] = Any_(VARIANT, actor, entry(method, path, body), frozenset(), {},
                                         setup_sql=SETUP)
        return out

    def connection_options(self, binding: str = "client", **_: Any) -> dict[str, Any]:
        import zoneinfo

        from django.db.backends.postgresql.psycopg_any import get_adapters_template

        from deployspecter.experimental.django_backend.base import cursor_class
        return {"cursor_factory": cursor_class(False), "context": get_adapters_template(True, zoneinfo.ZoneInfo("UTC"))}

    def reset(self, conn: Any, seed: Any) -> None:
        restore(conn)

    def read_state(self, conn: Any) -> dict[str, list[dict[str, Any]]]:
        return read_schema(conn, "public")

    def expected_initial(self, seed: Any) -> Any:
        return seed

    def check(self, state: Any) -> list[str]:
        return []  # no oracle: this validates the seam, not a defect


def request(method: str, path: str, body: Any) -> dict[str, Any]:
    from django.contrib.auth.models import User
    from rest_framework.test import APIClient
    client = APIClient(raise_request_exception=False)
    client.force_authenticate(user=User.objects.get(username="ds_admin"))
    resp = getattr(client, method)(path, body, format="json") if body is not None else getattr(client, method)(path)
    return {"status": resp.status_code}


def entry(method: str, path: str, body: Any) -> Any:
    def handler(conn: Any) -> dict[str, Any]:
        from django.db import connections

        from deployspecter.experimental.django_backend.base import handover
        handover(conn)
        try:
            return request(method, path, body)
        finally:
            connections["default"].close()
    handler.__name__ = handler.__qualname__ = f"{method}_{path.strip('/').replace('/', '_')}"
    return handler


# ------------------------------------------------------------------ phases

def dump(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(obj, indent=1, sort_keys=True, default=str).encode() + b"\n"
    if path.suffix == ".gz":
        path.write_bytes(gzip.compress(data, mtime=0))
    else:
        path.write_bytes(data)


def django_setup() -> None:
    import django
    django.setup()


def phase_migrate(out: Path) -> int:
    django_setup()
    from django.core.management import call_command

    from deployspecter.experimental import harness as hz
    from deployspecter.experimental.apps.django_fixture import SETUP, DjangoHandler
    from deployspecter.experimental.stepping import Actor, Limits, Run, actor_connection_factory
    t = target()
    admin = t.admin()
    try:
        admin.execute("DROP SCHEMA IF EXISTS ds_seed CASCADE")
        admin.execute("DROP SCHEMA IF EXISTS public CASCADE")
        admin.execute("CREATE SCHEMA public")

        def migrate(conn: Any) -> str:
            from django.db import connections

            from deployspecter.experimental.django_backend.base import handover
            handover(conn)
            try:
                call_command("migrate", verbosity=1, interactive=False)
            finally:
                connections["default"].close()
            return "migrated"

        class Capture(DjangoHandler):
            def allows(self, text: str) -> bool:
                return True
        lim = Limits(statement_ms=60000, python_step_s=120, run_s=3600)
        actor = Actor("migrate", Capture("django", "migrate", migrate, frozenset(), {}, setup_sql=SETUP), {})
        connect = actor_connection_factory(t.conninfo(), lim, search_path="public", autocommit=True,
                                           **App({}, "W1").connection_options())
        run = Run([actor], connect, admin, limits=lim, run_id="paperless_migrate")
        started = time.monotonic()
        trace, corr = hz.corroborated(admin, run, user=t.user)
        secs = round(time.monotonic() - started, 1)
    finally:
        admin.close()
    td = trace.to_dict()
    dump(out / "evidence" / "P1_migrate_trace.json.gz", td)
    summary = {"outcome": trace.outcome, "actor": trace.actors["migrate"], "diagnostics": trace.diagnostics,
               "statements": len(trace.records), "seconds": secs,
               "corroboration": {k: v for k, v in corr.items() if k != "details"}}
    dump(out / "evidence" / "P1_migrate.json", summary)
    print(json.dumps({k: summary[k] for k in ("outcome", "statements", "seconds")}
                     | {"exception": trace.actors["migrate"]["exception"], "corroboration_ok": corr.get("ok"),
                        "params_mismatched": corr.get("params_mismatched"), "problems": corr.get("problems", [])[:5]}))
    return 0


def phase_seed(out: Path) -> int:
    import io
    django_setup()
    from django.contrib.auth.models import User
    from django.core.management import call_command
    from django.db import connection

    from documents.models import Correspondent, Document, Tag
    buf = io.StringIO()
    call_command("showmigrations", "--list", stdout=buf, no_color=True)
    text = buf.getvalue()
    unapplied = [ln.strip() for ln in text.splitlines() if ln.strip().startswith("[ ]")]
    applied = sum(1 for ln in text.splitlines() if ln.strip().startswith("[X]"))
    dump(out / "evidence" / "P1_showmigrations.json", {"applied": applied, "unapplied": unapplied, "text": text})
    u = User.objects.create_superuser("ds_admin", "ds_admin@example.invalid", "ds-validation-only")
    inbox = Tag.objects.create(name="t-inbox")
    paid = Tag.objects.create(name="t-paid")
    acme = Correspondent.objects.create(name="ACME")
    ids = {"t-inbox": inbox.pk, "t-paid": paid.pk, "user": u.pk}
    for i in (1, 2, 3):
        d = Document.objects.create(title=f"doc-{i}", checksum=f"{i:032x}", mime_type="application/pdf",
                                    content=f"content {i}", correspondent=acme, owner=u)
        d.tags.add(inbox)
        ids[f"doc-{i}"] = d.pk
    connection.close()
    admin = target().admin()
    try:
        admin.execute(f"DROP SCHEMA IF EXISTS {SEED_SCHEMA} CASCADE")
        admin.execute(f"CREATE SCHEMA {SEED_SCHEMA}")
        for t in app_tables(admin):
            admin.execute(f'CREATE TABLE {SEED_SCHEMA}."{t}" AS SELECT * FROM public."{t}"')
        cols = admin.execute("SELECT table_name, column_name, data_type FROM information_schema.columns "
                             "WHERE table_schema = 'public' ORDER BY 1, 2").fetchall()
        seed = read_schema(admin, SEED_SCHEMA)
    finally:
        admin.close()
    from deployspecter.experimental import oracle
    dump(out / "evidence" / "seed_ids.json", ids)
    dump(out / "evidence" / "column_types.json", [list(c) for c in cols])
    dump(out / "evidence" / "seed_state.json.gz", seed)
    print(json.dumps({"applied": applied, "unapplied": len(unapplied), "ids": ids,
                      "seed_digest": oracle.digest(seed), "tables": len(seed)}))
    return 0


def phase_ops(out: Path) -> int:
    django_setup()
    from django.urls import resolve

    from deployspecter.experimental import harness as hz
    from deployspecter.experimental import oracle
    from deployspecter.experimental.stepping import Limits
    ids = json.loads((out / "evidence" / "seed_ids.json").read_text())
    for p in ("/api/tags/", f"/api/documents/{ids['doc-1']}/", "/api/documents/bulk_edit/"):
        resolve(p)  # import the URLconf and views before the first stepped run (not a database operation)
    import rest_framework.test  # noqa: F401
    admin = target().admin()
    try:
        seed = read_schema(admin, SEED_SCHEMA)
    finally:
        admin.close()
    lim = Limits(statement_ms=5000, lock_ms=3000, python_step_s=30, run_s=300)
    rows = []
    for w in WORKLOADS:
        app = App(ids, w)
        inv = [{"actor": a, "app_version": VARIANT, "handler": a, "args": {}} for a in ("a", "b")]
        for s in SCHEDULES:
            started = time.monotonic()
            rec: dict[str, Any] = {"workload": w, "schedule": s}
            try:
                r = hz.run_app(target(), app, inv, seed, variant=VARIANT, schedule=schedule(s), label=w,
                               limits=lim, corroborate=True, registry_kw={"binding": "client"})
                td = r.trace.to_dict()
                dump(out / "evidence" / "ops" / f"{w}_{s}_trace.json.gz", td)
                dump(out / "evidence" / "ops" / f"{w}_{s}_final.json.gz", r.final_state)
                grants = [op["actor"] for op in sorted((o for o in td["records"] if o.get("grant") is not None),
                                                         key=lambda o: o["grant"])]
                rec |= {"outcome": r.trace.outcome, "actors": r.trace.actors, "diagnostics": r.trace.diagnostics,
                        "statements": len(r.trace.records), "granted_order": grants,
                        "final_digest": oracle.digest(r.final_state),
                        "corroboration": {k: v for k, v in (r.corroboration or {}).items() if k != "details"}}
            except Exception as exc:  # recorded, never retried
                rec |= {"outcome": "harness_exception", "error": f"{type(exc).__name__}: {str(exc)[:500]}"}
            rec["seconds"] = round(time.monotonic() - started, 2)
            dump(out / "evidence" / "ops" / f"{w}_{s}.json", rec)
            c = rec.get("corroboration", {})
            rows.append({"run": f"{w}_{s}", "outcome": rec["outcome"],
                         "status": {n: (a.get("result") or {}).get("status") if isinstance(a.get("result"), dict)
                                    else a.get("exception") for n, a in rec.get("actors", {}).items()},
                         "corr_ok": c.get("ok"), "mismatched": c.get("params_mismatched"),
                         "error": rec.get("error")})
            print(json.dumps(rows[-1]), flush=True)
    return 0


def phase_stock(out: Path) -> int:
    django_setup()
    from django.db import connection
    ids = json.loads((out / "evidence" / "seed_ids.json").read_text())
    for w in WORKLOADS:
        for rep in (1, 2):
            admin = target().admin()
            try:
                restore(admin)
                statuses = {}
                for actor, (method, path, body) in requests_for(w, ids).items():
                    statuses[actor] = request(method, path, body)["status"]
                connection.close()
                state = read_schema(admin, "public")
            finally:
                admin.close()
            dump(out / "evidence" / "stock" / f"{w}_rep{rep}_final.json.gz", state)
            dump(out / "evidence" / "stock" / f"{w}_rep{rep}.json", {"statuses": statuses})
            print(json.dumps({"workload": w, "rep": rep, "statuses": statuses}), flush=True)
    return 0


# ------------------------------------------------------------------ verdict (no Django)

def load(p: Path) -> Any:
    data = p.read_bytes()
    return json.loads(gzip.decompress(data) if p.suffix == ".gz" else data)


def masked(state: dict[str, list[dict[str, Any]]], mask: dict[str, set[str]]) -> dict[str, list[str]]:
    out = {}
    for t, rows in state.items():
        m = mask.get(t, set())
        out[t] = sorted(json.dumps({k: ("<masked>" if k in m else v) for k, v in r.items()}, sort_keys=True)
                        for r in rows)
    return out


def diff_columns(s1: dict[str, list[dict[str, Any]]], s2: dict[str, list[dict[str, Any]]]) -> dict[str, set[str]]:
    """Columns whose values differ between two states of the same table (rows paired in order)."""
    out: dict[str, set[str]] = {}
    for t in set(s1) | set(s2):
        a, b = s1.get(t, []), s2.get(t, [])
        if len(a) != len(b):
            continue  # a row-count difference isn't masked; it shows in the comparison
        for ra, rb in zip(a, b):
            for k in set(ra) | set(rb):
                if ra.get(k) != rb.get(k):
                    out.setdefault(t, set()).add(k)
    return out


def phase_verdict(out: Path) -> int:
    ev = out / "evidence"
    v: dict[str, Any] = {"label": "preregistered, automation-blinded, single-investigator. Not independent"}
    m = load(ev / "P1_migrate.json")
    sm = load(ev / "P1_showmigrations.json")
    c = m["corroboration"]
    p1 = {"outcome": m["outcome"], "exception": m["actor"]["exception"], "corroboration_ok": c.get("ok"),
          "params_mismatched": c.get("params_mismatched"), "problems": c.get("problems"),
          "statements": m["statements"], "applied": sm["applied"], "unapplied": sm["unapplied"]}
    p1["pass"] = (m["outcome"] == "completed" and m["actor"]["exception"] is None and c.get("ok") is True
                  and c.get("params_mismatched") == 0 and not sm["unapplied"] and sm["applied"] > 0)
    v["P1"] = p1
    p2, p3 = {}, {}
    for w in WORKLOADS:
        for s in SCHEDULES:
            r = load(ev / "ops" / f"{w}_{s}.json")
            c = r.get("corroboration", {})
            acts = r.get("actors", {})
            ds_exc = [n for n, a in acts.items() if (a.get("exception_class") or "").split(".")[-1]
                      in ("Refused", "RunAborted") or (a.get("exception") or "").startswith(("Refused", "RunAborted"))]
            statuses = {n: (a.get("result") or {}).get("status") if isinstance(a.get("result"), dict) else None
                        for n, a in acts.items()}
            ok = (r["outcome"] == "completed" and not ds_exc and len(statuses) == 2
                  and all(isinstance(x, int) for x in statuses.values()) and c.get("ok") is True
                  and c.get("params_mismatched") == 0)
            p2[f"{w}_{s}"] = {"pass": ok, "outcome": r["outcome"], "statuses": statuses,
                              "deployspecter_exceptions": ds_exc, "corroboration_ok": c.get("ok"),
                              "params_mismatched": c.get("params_mismatched"),
                              "problems": c.get("problems"), "error": r.get("error")}
            if s == "S3":
                g = r.get("granted_order", [])
                ia = [i for i, x in enumerate(g) if x == "a"]
                inter = bool(ia) and any(x == "b" and ia[0] < i < ia[-1] for i, x in enumerate(g))
                p3[w] = {"pass": inter, "granted": len(g)}
    v["P2"] = {"pass": all(x["pass"] for x in p2.values()), "runs": p2}
    v["P3"] = {"pass": all(x["pass"] for x in p3.values()), "workloads": p3}
    types = load(ev / "column_types.json")
    tmask: dict[str, set[str]] = {}
    for t, col, ty in types:
        if ty.startswith("timestamp") or ty == "date":
            tmask.setdefault(t, set()).add(col)
    p4 = {}
    for w in WORKLOADS:
        st1, st2 = load(ev / "stock" / f"{w}_rep1_final.json.gz"), load(ev / "stock" / f"{w}_rep2_final.json.gz")
        nondet = diff_columns(st1, st2)
        mask = {t: tmask.get(t, set()) | nondet.get(t, set()) for t in set(tmask) | set(nondet)}
        seam = load(ev / "ops" / f"{w}_S1_final.json.gz")
        a, b = masked(seam, mask), masked(st1, mask)
        differ = sorted(t for t in set(a) | set(b) if a.get(t) != b.get(t))
        p4[w] = {"pass": not differ, "tables_differing": differ,
                 "masked_nondeterministic": {t: sorted(cs) for t, cs in sorted(nondet.items())}}
    v["P4"] = {"pass": all(x["pass"] for x in p4.values()),
               "masked_by_type": {t: sorted(cs) for t, cs in sorted(tmask.items())}, "workloads": p4}
    v["verdict"] = "PASS" if all(v[k]["pass"] for k in ("P1", "P2", "P3", "P4")) else "FAIL"
    dump(out / "VERDICT.json", v)
    print(json.dumps({k: v[k]["pass"] for k in ("P1", "P2", "P3", "P4")} | {"verdict": v["verdict"]}))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("phase", choices=["migrate", "seed", "ops", "stock", "verdict"])
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    return {"migrate": phase_migrate, "seed": phase_seed, "ops": phase_ops, "stock": phase_stock,
            "verdict": phase_verdict}[a.phase](a.out)


if __name__ == "__main__":
    sys.exit(main())
