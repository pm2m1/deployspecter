"""EXPERIMENTAL, live: one coordinator granting the statements of two processes' stepped runs (``ds-coord/1``).

The two "processes" here are two ``RemoteAsyncRun``s in one event loop, each with its own connections and monitor,
talking to the coordinator over TCP exactly as two containers do. Skipped unless the disposable MVP PostgreSQL runs.
"""

from __future__ import annotations

import os
import socket
from pathlib import Path

import pytest

from deployspecter.experimental import coordinated as co
from deployspecter.experimental import harness as hz
from deployspecter.experimental import stepping as st
from deployspecter.experimental import stepping_async as sa
from deployspecter.experimental.apps import reservations_mixed as sync_app
from deployspecter.experimental.apps import reservations_mixed_async as A

SECRET = Path(os.environ.get("DS_MVP_SECRET_FILE", os.path.expanduser("~/.ds_mvp_pg_password")))
PORT = int(os.environ.get("DS_MVP_PORT", "55441"))
pytestmark = pytest.mark.skipif(not SECRET.exists(), reason="the disposable MVP PostgreSQL isn't running")
SEED = {"events": [{"id": 1, "capacity": 5, "reserved": 0}, {"id": 2, "capacity": 5, "reserved": 0}]}
BUMP = "UPDATE events SET reserved = reserved + 1 WHERE id = 1"


@pytest.fixture(scope="module")
def target():
    return hz.Target("127.0.0.1", PORT, "ds_mvp", SECRET.read_text().strip())


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def bump(conn):
    async with conn.transaction():
        await conn.execute(BUMP)
    return "ok"


def process(target, name, actor, port, token="t0k3n", lim=None):
    lim = lim or st.Limits(run_s=30)
    a = st.Actor(actor, st.Handler(f"proc-{name}", "bump", bump, frozenset({BUMP}), {}), {})
    connect = sa.async_actor_connection_factory(target.conninfo(), lim, search_path=A.SCHEMA)
    return co.RemoteAsyncRun([a], connect, hz.async_monitor(target), process=name, version=f"v-{name}",
                             endpoint=("127.0.0.1", port), token=token, limits=lim, run_id=f"coord_{name}",
                             connect_timeout_s=5)


def reserved(target):
    admin = target.admin()
    try:
        return admin.execute(f"SELECT reserved FROM {A.SCHEMA}.events WHERE id = 1").fetchone()[0]
    finally:
        admin.close()


def test_a_cross_process_lock_wait_is_attributed_scheduled_and_corroborated(target):
    port = free_port()
    admin = target.admin()
    try:
        sync_app.reset(admin, SEED)
        coord = co.Coordinator(processes=["A", "B"], token="t0k3n", deadline_s=30, epoch_timeout_s=10,
                               policy={"kind": "schedule", "actors": ["a", "a", "b", "b", "a", "b"]})
        runs = {"A": process(target, "A", "a", port), "B": process(target, "B", "b", port)}
        traces, report, corr = hz.corroborated_coordinated(admin, coord, runs, user=target.user, port=port)
    finally:
        admin.close()
    assert report["outcome"] == "completed", report
    assert [d["actor"] for d in report["decisions"]] == ["a", "a", "b", "b", "a", "b"]
    assert [d["kind"] for d in report["decisions"]] == ["transaction_enter", "execute"] * 2 + \
        ["transaction_exit"] * 2
    # b's UPDATE waited on a's row lock in the other process: seen by B's monitor, attributed to A's actor
    assert any(d["blocked"] == {"b": ["a"]} for d in report["decisions"]), report["decisions"]
    grants = sorted((op.grant, op.actor) for t in traces.values() for op in t.records if op.grant is not None)
    assert grants == [(0, "a"), (1, "a"), (2, "b"), (3, "b"), (4, "a"), (5, "b")]  # one global numbering
    assert corr["ok"], corr["problems"]
    assert set(corr["sessions"].values()) == {"a", "b"}
    assert reserved(target) == 2


def test_a_missing_process_aborts_every_run_before_any_statement(target):
    port = free_port()
    admin = target.admin()
    try:
        sync_app.reset(admin, SEED)
        coord = co.Coordinator(processes=["A", "B", "C"], token="t0k3n", hello_timeout_s=2, deadline_s=20)
        runs = {"A": process(target, "A", "a", port), "B": process(target, "B", "b", port)}
        traces, report, corr = hz.corroborated_coordinated(admin, coord, runs, user=target.user, port=port)
    finally:
        admin.close()
    assert report["outcome"] == "refused" and "C" in report["reason"]
    assert all(t.outcome == "aborted" for t in traces.values())
    assert not [op for t in traces.values() for op in t.records if op.grant is not None]
    assert reserved(target) == 0


def test_a_wrong_token_is_refused(target):
    port = free_port()
    admin = target.admin()
    try:
        sync_app.reset(admin, SEED)
        coord = co.Coordinator(processes=["A", "B"], token="t0k3n", hello_timeout_s=5, deadline_s=20)
        runs = {"A": process(target, "A", "a", port), "B": process(target, "B", "b", port, token="wrong")}
        _traces, report, _corr = hz.corroborated_coordinated(admin, coord, runs, user=target.user, port=port)
    finally:
        admin.close()
    assert report["outcome"] == "refused" and "token" in report["reason"]
    assert reserved(target) == 0


@pytest.mark.parametrize("seed", [1, 2])
def test_a_seeded_policy_repeats_its_grant_order_from_the_same_state(target, seed):
    orders = []
    for _ in range(2):
        port = free_port()
        admin = target.admin()
        try:
            sync_app.reset(admin, SEED)
            coord = co.Coordinator(processes=["A", "B"], token="t0k3n", deadline_s=30,
                                   policy={"kind": "seeded", "seed": seed})
            runs = {"A": process(target, "A", "a", port), "B": process(target, "B", "b", port)}
            _t, report, corr = hz.corroborated_coordinated(admin, coord, runs, user=target.user, port=port)
        finally:
            admin.close()
        assert report["outcome"] == "completed" and corr["ok"], (report["reason"], corr["problems"])
        orders.append([(d["actor"], d["kind"]) for d in report["decisions"]])
    assert orders[0] == orders[1]
