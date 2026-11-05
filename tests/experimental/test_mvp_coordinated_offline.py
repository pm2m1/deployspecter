"""EXPERIMENTAL, offline: the coordinator's fail-closed behaviour against scripted fake processes (no database)."""

from __future__ import annotations

import asyncio
import json
import socket

from deployspecter.experimental import coordinated as co


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Fake:
    """A scripted process speaking ds-coord/1."""

    def __init__(self, port: int, process: str, actors: dict[str, int]):
        self.port, self.process, self.actors = port, process, actors

    async def open(self, token: str = "tok") -> None:
        for _ in range(50):
            try:
                self.r, self.w = await asyncio.open_connection("127.0.0.1", self.port)
                break
            except OSError:
                await asyncio.sleep(0.05)
        await self.send({"t": "hello", "protocol": co.PROTOCOL, "token": token, "process": self.process,
                         "version": "v", "run_id": "r", "monitor_pid": 1,
                         "actors": {a: {"backend_pid": pid, "handler": "h"} for a, pid in self.actors.items()}})

    async def send(self, msg: dict) -> None:
        self.w.write((json.dumps(msg) + "\n").encode())
        await self.w.drain()

    async def recv(self) -> dict:
        line = await asyncio.wait_for(self.r.readline(), timeout=10)
        return json.loads(line) if line else {"t": "_eof"}

    async def snap(self, epoch: int, states: dict[str, tuple[str, str | None]], markers=None, report=None) -> None:
        await self.send({"t": "snap", "epoch": epoch, "report": report or {}, "actors": {
            a: {"state": st, "commutes": False, "blocked_by": [], "markers": (markers or {}).get(a, {}),
                "request": None if sql is None else {"kind": "execute", "sql": sql, "statement_id": sql[:12]}}
            for a, (st, sql) in states.items()}})


def run(coord: co.Coordinator, port: int, script):
    async def main():
        serving = asyncio.create_task(coord.serve("127.0.0.1", port))
        await asyncio.sleep(0.05)
        out = await script()
        return await asyncio.wait_for(serving, timeout=20), out
    return asyncio.run(main())


def test_a_process_that_disconnects_mid_run_aborts_the_others():
    port = free_port()
    a, b = Fake(port, "A", {"x": 10}), Fake(port, "B", {"y": 11})

    async def script():
        await a.open()
        await b.open()
        start_a, start_b = await a.recv(), await b.recv()
        assert start_a["t"] == start_b["t"] == "start"
        await a.snap(0, {"x": ("pending", "SELECT 1")})
        b.w.close()  # B is lost without a bye
        return await a.recv()

    report, last = run(co.Coordinator(processes=["A", "B"], token="tok", epoch_timeout_s=5), port, script)
    assert report["outcome"] == "lost_process" and last["t"] == "abort"
    assert report["grants"] == 0


def test_a_missing_snapshot_times_out_the_epoch():
    port = free_port()
    a, b = Fake(port, "A", {"x": 10}), Fake(port, "B", {"y": 11})

    async def script():
        await a.open()
        await b.open()
        await a.recv(), await b.recv()
        await a.snap(0, {"x": ("pending", "SELECT 1")})  # B never answers
        return await a.recv()

    report, last = run(co.Coordinator(processes=["A", "B"], token="tok", epoch_timeout_s=1), port, script)
    assert report["outcome"] == "timeout" and "['B']" in report["reason"] and last["t"] == "abort"


def test_only_gated_actors_left_is_a_stall_not_a_hang():
    port = free_port()
    a = Fake(port, "A", {"x": 10})

    async def script():
        await a.open()
        msg = await a.recv()
        while msg["t"] in ("start", "sync"):
            await a.snap(msg["epoch"], {"x": ("pending", "SELECT 1")})
            msg = await a.recv()
        return msg

    gate = [{"actor": "x", "match": None, "until": [{"actor": "x", "marker": "never", "count": 1}]}]
    report, last = run(co.Coordinator(processes=["A"], token="tok", gates=gate, stall_s=0.5), port, script)
    assert report["outcome"] == "stall" and last["t"] == "abort" and report["grants"] == 0


def test_a_gate_opens_on_another_processes_marker_and_the_grant_names_the_reported_statement():
    port = free_port()
    a, b = Fake(port, "A", {"x": 10}), Fake(port, "B", {"y": 11})

    async def script():
        await a.open()
        await b.open()
        await a.recv(), await b.recv()
        await a.snap(0, {"x": ("pending", "PUSH")})
        await b.snap(0, {"y": ("pending", "FETCH")}, markers={"y": {"ready": 0}})
        first = await b.recv()  # x is gated until y is ready, so y is granted
        await a.recv()  # sync
        await a.snap(first["epoch"], {"x": ("pending", "PUSH")})
        await b.snap(first["epoch"], {"y": ("finished", None)}, markers={"y": {"ready": 1}})
        await b.send({"t": "bye", "outcome": "completed"})
        second = await a.recv()
        await a.snap(second["epoch"], {"x": ("finished", None)})
        await a.send({"t": "bye", "outcome": "completed"})
        return first, second

    gate = [{"actor": "x", "match": "PUSH", "until": [{"actor": "y", "marker": "ready", "count": 1}]}]
    report, (first, second) = run(co.Coordinator(processes=["A", "B"], token="tok", gates=gate), port, script)
    assert report["outcome"] == "completed"
    assert (first["actor"], first["statement_id"], first["grant"]) == ("y", "FETCH", 0)
    assert (second["actor"], second["statement_id"], second["grant"]) == ("x", "PUSH", 1)
    assert report["decisions"][0]["gated"] == ["x"]


def test_a_snapshot_for_the_wrong_epoch_is_a_protocol_error():
    port = free_port()
    a = Fake(port, "A", {"x": 10})

    async def script():
        await a.open()
        await a.recv()
        await a.snap(7, {"x": ("pending", "SELECT 1")})
        return await a.recv()

    report, last = run(co.Coordinator(processes=["A"], token="tok"), port, script)
    assert report["outcome"] == "protocol_error" and last["t"] == "abort"


def test_a_bye_with_an_unfinished_actor_is_refused():
    port = free_port()
    a = Fake(port, "A", {"x": 10})

    async def script():
        await a.open()
        await a.recv()
        await a.snap(0, {"x": ("blocked", None)})
        await a.send({"t": "bye", "outcome": "completed"})
        return await a.recv()

    report, _last = run(co.Coordinator(processes=["A"], token="tok", epoch_timeout_s=3), port, script)
    assert report["outcome"] in ("protocol_error", "timeout")


def test_the_seeded_policy_is_a_pure_function_of_its_seed_and_the_enabled_sets():
    enabled = [[("A", "a"), ("B", "b")], [("A", "a"), ("B", "b"), ("B", "c")], [("B", "b")]] * 5
    def picks_for(seed):
        policy = co.Policy({"kind": "seeded", "seed": seed})
        return [policy.choose(e)[1] for e in enabled]

    picks = [picks_for(s) for s in (3, 3, 4)]
    assert picks[0] == picks[1] and picks[0] != picks[2]


def test_the_schedule_policy_defers_an_unavailable_entry_and_keeps_it():
    p = co.Policy({"kind": "schedule", "actors": ["b", "a"], "then": {"kind": "prefer", "order": ["a"]}})
    assert p.choose([("A", "a")]) == ("A", "a", "deferred")
    assert p.choose([("A", "a"), ("B", "b")]) == ("B", "b", "schedule")
    assert p.choose([("A", "a"), ("B", "b")]) == ("A", "a", "schedule")
    assert p.choose([("B", "b")]) == ("B", "b", "continuation")


def test_a_declined_grant_is_recorded_and_decided_again():
    port = free_port()
    a = Fake(port, "A", {"x": 10})

    async def script():
        await a.open()
        await a.recv()
        await a.snap(0, {"x": ("pending", "FIRST")})
        g = await a.recv()
        await a.send({"t": "decline", "epoch": g["epoch"], "grant": g["grant"], "actor": "x", "now": "pending",
                      "statement_id": "SECOND"})
        await a.snap(g["epoch"], {"x": ("pending", "SECOND")})
        g2 = await a.recv()
        await a.snap(g2["epoch"], {"x": ("finished", None)})
        await a.send({"t": "bye", "outcome": "completed"})
        return g, g2

    report, (g, g2) = run(co.Coordinator(processes=["A"], token="tok"), port, script)
    assert report["outcome"] == "completed"
    assert (g["statement_id"], g2["statement_id"]) == ("FIRST", "SECOND") and g2["grant"] == g["grant"] + 1
    assert report["decisions"][0]["declined"] == {"now": "pending", "statement_id": "SECOND"}
    assert "declined" not in report["decisions"][1]


def test_a_decline_for_a_grant_never_given_is_a_protocol_error():
    port = free_port()
    a = Fake(port, "A", {"x": 10})

    async def script():
        await a.open()
        await a.recv()
        await a.send({"t": "decline", "epoch": 0, "grant": 5, "actor": "x"})
        return await a.recv()

    report, last = run(co.Coordinator(processes=["A"], token="tok", epoch_timeout_s=3), port, script)
    assert report["outcome"] == "protocol_error" and last["t"] == "abort"
