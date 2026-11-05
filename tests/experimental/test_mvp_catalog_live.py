"""EXPERIMENTAL, live: the planner's trusted context verified against the disposable database's real catalog.

The unmodified ``catalog_verification.verify_context`` checks the context against a read-only export of the live
catalog (``catalog_export``) instead of Track A's hand-written dry-run snapshot; anything but MATCH stops the chain.
Skipped unless the disposable MVP PostgreSQL is running.
"""

from __future__ import annotations

import copy
import os
from pathlib import Path

import pytest

from deployspecter.experimental import catalog_export as ce
from deployspecter.experimental import harness as hz
from deployspecter.experimental import planning_adapter as pa
from deployspecter.experimental.apps import reservations_mixed
from deployspecter.live import catalog_verification as cv
from deployspecter.research.track_a import scenario as sc

SECRET = Path(os.environ.get("DS_MVP_SECRET_FILE", os.path.expanduser("~/.ds_mvp_pg_password")))
PORT = int(os.environ.get("DS_MVP_PORT", "55441"))
pytestmark = pytest.mark.skipif(not SECRET.exists(), reason="the disposable MVP PostgreSQL isn't running")
BOTH_READ_FIRST = ["a", "b"] * 6
OTHER_SCHEMAS = ("mvp_app", "mvp_orm", "chancy_capture", "mvp_django",
                 "pgq_capture")  # other fixtures' schemas in the same disposable database


@pytest.fixture
def target():
    t = hz.Target("127.0.0.1", PORT, "ds_mvp", SECRET.read_text().strip())
    admin = t.admin()
    try:
        for s in OTHER_SCHEMAS:  # each test starts from a database holding only the planned schema
            admin.execute(f"DROP SCHEMA IF EXISTS {s} CASCADE")
    finally:
        admin.close()
    return t


def test_the_live_export_validates_and_has_the_dry_run_fixtures_structure(target):
    snap = pa.live_snapshot(target)
    cv.CatalogSnapshot.model_validate(snap)
    assert ce.diff(sc.catalog_snapshot_fixture(), snap) == {}
    assert snap["coverage"]["triggers"] == "database"
    assert all(t["writes"] is None for t in snap["triggers"])  # never claims a trigger writes nothing


def test_the_chain_bridges_on_the_live_catalog_and_both_builds_run(target):
    results = {}
    for variant in ("buggy", "fixed"):
        out = pa.run_planned_live(target, sc.DOCUMENT, variant=variant, schedule=BOTH_READ_FIRST, corroborate=True)
        p, r = out["plan"], out["result"]
        assert p.stages["verify_context"] == {"outcome": "MATCH", "findings": 0} and p.outcome == "bridged"
        assert p.digests["snapshot"] == cv.snapshot_digest(cv.CatalogSnapshot.model_validate(out["snapshot"]))
        assert r.corroboration["ok"], r.corroboration["problems"]
        results[variant] = r
    assert results["buggy"].initial_digest == results["fixed"].initial_digest
    assert results["buggy"].violations and not results["fixed"].violations


def test_a_trigger_elsewhere_with_unknown_writes_makes_the_check_indeterminate(target):
    admin = target.admin()
    try:
        reservations_mixed.reset(admin, {"events": [{"id": 1, "capacity": 5, "reserved": 0}]})  # has a user trigger
    finally:
        admin.close()
    out = pa.run_planned_live(target, sc.DOCUMENT, variant="buggy", schedule=BOTH_READ_FIRST)
    p = out["plan"]
    assert (p.outcome, p.stage) == ("indeterminate", "verify_context") and out["result"] is None
    assert {f["code"] for f in p.detail} == {"trigger_effect_unknown"}


@pytest.mark.parametrize("ddl, outcome, code", [
    ("ALTER TABLE ds_run.events ADD CONSTRAINT cap_chk CHECK (capacity >= 0)", "unsupported", "check_constraint"),
    ("ALTER TABLE ds_run.events RENAME COLUMN reserved TO booked", "invalid", "column_missing"),
    ("ALTER TABLE ds_run.events ALTER COLUMN capacity TYPE bigint", "invalid", None),
])
def test_a_live_schema_that_differs_from_the_context_is_refused(target, ddl, outcome, code):
    out = pa.run_planned_live(target, sc.DOCUMENT, variant="buggy", schedule=BOTH_READ_FIRST,
                              alter=lambda admin: admin.execute(ddl))
    p = out["plan"]
    assert (p.outcome, p.stage) == (outcome, "verify_context") and out["result"] is None
    if code:
        assert code in {f["code"] for f in p.detail}


def test_a_catalog_change_between_export_and_run_is_refused(target, monkeypatch):
    real_reset = pa.PlannedReservations.reset

    def drifting(self, conn, seed=None):
        real_reset(self, conn, seed)
        conn.execute("ALTER TABLE ds_run.events ADD COLUMN note text")

    monkeypatch.setattr(pa.PlannedReservations, "reset", drifting)
    with pytest.raises(pa.AdapterRefused, match="catalog changed"):
        pa.run_planned_live(target, copy.deepcopy(sc.DOCUMENT), variant="buggy", schedule=BOTH_READ_FIRST)
