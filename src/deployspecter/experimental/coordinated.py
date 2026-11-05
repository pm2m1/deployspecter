"""EXPERIMENTAL: one statement scheduler across several processes (``ds-coord/1``).

Each process (e.g. a container holding one pinned application release) runs a ``RemoteAsyncRun``: the ordinary async
stepping engine, with its decision loop replaced by a client of one ``Coordinator``. The coordinator is the only
place where grants are decided, so the database statements of every actor, in every process, are granted in one
global order with one global grant numbering.

The decision rule is the in-process engine's, extended across processes (lockstep). A grant is decided only when
every live process has reported, for the current epoch, a *settled* snapshot: no actor is running, granted or
executing (blocked actors are allowed). At that point every live actor is either parked at its gate with its next
statement, blocked on a lock, or done. After each grant, the epoch advances and every process must report again.

The protocol is newline-delimited JSON over TCP and fails closed:

* A missing hello, an epoch with no snapshot before its timeout, a peer that disconnects without ``bye``, a message
  that doesn't fit the protocol, a grant for an actor that isn't pending or for a statement other than the one
  reported, a stall (only gated actors left), or the overall deadline all abort every process.
* Every process halts (its own ``_halt``) on abort, on losing the coordinator, or on its own limits.
* A lock wait on a session that no process reported is an ``external_blocker``, as in-process.

What is controlled: the order in which database statements are granted. What isn't: Python and event-loop
interleavings inside a process between its statements (which task asks next, timers). Repeating a schedule repeats
the policy, not necessarily the trace; repeatability is measured, not assumed.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import random
import re
import time
from collections import deque
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

import psycopg

from .stepping import BUSY, Decision, Limits
from .stepping_async import AsyncActor, AsyncRun, AsyncSteppedConnection

PROTOCOL = "ds-coord/1"
TERMINAL = ("finished", "errored", "aborted")
MAX_LINE = 1 << 20


class ProtocolError(Exception):
    pass


async def send(writer: asyncio.StreamWriter, msg: dict[str, Any]) -> None:
    writer.write((json.dumps(msg, sort_keys=True, default=str) + "\n").encode())
    await writer.drain()


async def pump(reader: asyncio.StreamReader, inbox: asyncio.Queue[dict[str, Any]], tag: Any = None) -> None:
    """Every message from one peer into a queue; EOF and garbage become explicit messages."""
    try:
        while True:
            line = await reader.readline()
            if not line:
                await inbox.put({"t": "_eof", "_from": tag})
                return
            if len(line) > MAX_LINE:
                await inbox.put({"t": "_bad", "_from": tag, "why": "line too long"})
                return
            try:
                msg = json.loads(line)
            except ValueError:
                await inbox.put({"t": "_bad", "_from": tag, "why": "not JSON"})
                return
            if not isinstance(msg, dict) or not isinstance(msg.get("t"), str):
                await inbox.put({"t": "_bad", "_from": tag, "why": "not a message"})
                return
            msg["_from"] = tag
            await inbox.put(msg)
    except (ConnectionError, asyncio.IncompleteReadError):
        await inbox.put({"t": "_eof", "_from": tag})


def compile_markers(spec: dict[str, dict[str, Any]]) -> dict[str, tuple[re.Pattern[str], bool]]:
    """``{"claim": {"regex": "SKIP LOCKED", "rows": true}}``: a marker counts an actor's completed (ok) operations
    whose statement matches; with ``rows``, only those that returned at least one row."""
    return {name: (re.compile(m["regex"]), bool(m.get("rows"))) for name, m in spec.items()}


# ---------------------------------------------------------------------------------------------- the process side


class RemoteAsyncRun(AsyncRun):
    """An ``AsyncRun`` whose grants come from a ``Coordinator``. Grant numbers are global; ``decisions`` lists only
    this process's grants, as the coordinator decided them (the coordinator's report has the global list)."""

    def __init__(self, actors: Sequence[Any], connect: Callable[[], Awaitable[AsyncSteppedConnection]],
                 monitor: Callable[[], Awaitable[psycopg.AsyncConnection]], *, process: str, version: str,
                 endpoint: tuple[str, int], token: str, markers: dict[str, dict[str, Any]] | None = None,
                 report: Callable[[], dict[str, Any]] | None = None, limits: Limits | None = None,
                 run_id: str = "", migration_phase: str = "", connect_timeout_s: float = 30.0) -> None:
        super().__init__(actors, connect, monitor, schedule=(), limits=limits, run_id=run_id,
                         migration_phase=migration_phase)
        self.process, self.version, self.endpoint, self.token = process, version, endpoint, token
        self.markers = compile_markers(markers or {})
        self.report = report
        self.connect_timeout_s = connect_timeout_s
        self.peers: dict[int, str] = {}
        self.signals: dict[str, asyncio.Event] = {}
        self.received: list[dict[str, Any]] = []  # every coordinator message, in order (evidence)
        self._counts: dict[str, dict[str, int]] = {}
        self._scanned = 0
        self._writer: asyncio.StreamWriter | None = None

    def signal(self, name: str) -> asyncio.Event:
        return self.signals.setdefault(name, asyncio.Event())

    async def _blockers(self, actor: AsyncActor) -> list[str]:
        assert self.monitor is not None
        cur = await self.monitor.execute("SELECT pg_blocking_pids(%s)", (actor.backend_pid,))
        pids = (await cur.fetchone())[0] or []
        names = {**self.peers, **{a.backend_pid: a.name for a in self.actors}}
        return [names.get(p, f"external:{p}") for p in pids]

    def _marker_counts(self) -> dict[str, dict[str, int]]:
        for a in self.actors:
            self._counts.setdefault(a.name, dict.fromkeys(self.markers, 0))
        for op in self.records[self._scanned:]:
            if op.outcome != "ok" or op.actor not in self._counts:
                continue
            for name, (rx, rows) in self.markers.items():
                if rx.search(op.sql) and (not rows or op.rows):
                    self._counts[op.actor][name] += 1
        self._scanned = len(self.records)
        return {k: dict(v) for k, v in self._counts.items()}

    def _snapshot(self, epoch: int, blocked: dict[str, list[str]]) -> dict[str, Any]:
        counts = self._marker_counts()
        actors = {}
        for a in self.actors:
            req = a.request if a.state == "pending" else None
            actors[a.name] = {"state": a.state, "commutes": bool(req) and self._commutes(a),
                              "request": None if req is None else {"kind": req.kind, "sql": req.sql[:2000],
                                                                   "statement_id": req.statement_id},
                              "blocked_by": blocked.get(a.name, []), "markers": counts[a.name]}
        report: dict[str, Any] = {}
        if self.report is not None:
            try:
                report = self.report()
            except Exception as exc:  # a report hook must never break the protocol; its failure is evidence
                report = {"report_error": type(exc).__name__}
        return {"t": "snap", "epoch": epoch, "actors": actors, "report": report, "t_ms": self._ms()}

    async def _tell(self, msg: dict[str, Any]) -> None:
        if self._writer is not None:
            with contextlib.suppress(Exception):
                await send(self._writer, msg)

    async def _fail(self, outcome: str, detail: str) -> str:
        await self._tell({"t": "fail", "outcome": outcome, "reason": detail})
        return await self._halt(outcome, detail)

    async def _open(self) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        until = time.monotonic() + self.connect_timeout_s
        while True:
            try:
                return await asyncio.wait_for(asyncio.open_connection(*self.endpoint), timeout=5)
            except (OSError, TimeoutError):
                if time.monotonic() > until:
                    raise
                await asyncio.sleep(0.2)

    async def _control(self) -> str:
        assert self.cv is not None
        lim = self.limits
        deadline = self.t0 + lim.run_s
        try:
            reader, writer = await self._open()
        except (OSError, TimeoutError) as exc:
            return await self._halt("coordinator_unreachable", f"{type(exc).__name__}: {exc}")
        self._writer = writer
        inbox: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        pumper = asyncio.create_task(pump(reader, inbox, "coordinator"))
        try:
            await send(writer, {"t": "hello", "protocol": PROTOCOL, "token": self.token, "process": self.process,
                                "version": self.version, "run_id": self.run_id, "monitor_pid": self.monitor_pid,
                                "actors": {a.name: {"backend_pid": a.backend_pid,
                                                    "handler": f"{a.handler.app_version}.{a.handler.name}"}
                                           for a in self.actors}})
            try:
                start = await asyncio.wait_for(inbox.get(), timeout=self.connect_timeout_s + 30)
            except TimeoutError:
                return await self._fail("protocol_error", "no start from the coordinator")
            self.received.append({k: v for k, v in start.items() if k != "_from"})
            if start.get("t") == "abort":
                return await self._halt("aborted", f"coordinator: {start.get('reason')}")
            if start.get("t") == "_eof":
                return await self._halt("coordinator_lost", "the coordinator disconnected before the start")
            if start.get("t") != "start" or start.get("protocol") != PROTOCOL:
                return await self._fail("protocol_error", f"expected start, got {start.get('t')!r}")
            mine = {a.backend_pid for a in self.actors}
            self.peers = {int(pid): name for pid, name in start.get("pids", {}).items() if int(pid) not in mine}
            epoch = int(start["epoch"])
            owed: int | None = epoch
            held: deque[dict[str, Any]] = deque()
            running_since: dict[str, float] = {}
            while True:
                if time.monotonic() > deadline:
                    return await self._fail("timeout", f"run exceeded {lim.run_s} s")
                async with self.cv:
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(self.cv.wait_for(
                            lambda: all(a.state not in BUSY for a in self.actors)), timeout=lim.settle_s)
                now = time.monotonic()
                for a in self.actors:
                    if a.state == "running":
                        running_since.setdefault(a.name, now)
                        if now - running_since[a.name] > lim.python_step_s:
                            return await self._fail("timeout", f"{a.name} computed for more than {lim.python_step_s} s")
                    else:
                        running_since.pop(a.name, None)
                blocked: dict[str, list[str]] = {}
                for a in self.actors:
                    if a.state in ("executing", "blocked"):
                        who = await self._blockers(a)
                        if who:
                            blocked[a.name] = who
                            if a.state == "executing":
                                a.state = "blocked"
                if any(w.startswith("external:") for ws in blocked.values() for w in ws):
                    return await self._fail("external_blocker", f"a session outside the run holds a lock: {blocked}")
                while not inbox.empty():
                    held.append(inbox.get_nowait())
                while held:
                    msg = held.popleft()
                    self.received.append({k: v for k, v in msg.items() if k != "_from"})
                    kind = msg["t"]
                    if kind in ("grant", "sync"):
                        if int(msg.get("epoch", -1)) <= epoch:
                            return await self._fail("protocol_error", f"{kind} for a stale epoch {msg.get('epoch')}")
                        epoch, owed = int(msg["epoch"]), int(msg["epoch"])
                        if kind == "grant":
                            a = self.by_name.get(msg.get("actor", ""))
                            if a is None or a.state != "pending" or a.request is None:
                                return await self._fail("protocol_error", f"grant for {msg.get('actor')!r}, which "
                                                        "isn't pending here")
                            if a.request.statement_id != msg.get("statement_id"):
                                return await self._fail("protocol_error", f"grant for {a.name}'s statement "
                                                        f"{msg.get('statement_id')}, which isn't the one it reported")
                            self.decisions.append(Decision(int(msg["decision"]), list(msg.get("enabled", [])),
                                                           dict(msg.get("blocked", {})), list(msg.get("finished", [])),
                                                           None, a.name, str(msg.get("source", "coordinator"))))
                            async with self.cv:
                                self.grants = int(msg["grant"])
                                a.granted, a.state = True, "granted"
                                self.cv.notify_all()
                    elif kind == "signal":
                        self.signal(str(msg.get("name"))).set()
                    elif kind == "abort":
                        return await self._halt("aborted", f"coordinator: {msg.get('reason')}")
                    elif kind == "_eof":
                        return await self._halt("coordinator_lost", "the coordinator disconnected")
                    else:
                        return await self._fail("protocol_error", f"unexpected message {kind!r}")
                settled = all(a.state not in BUSY for a in self.actors)
                if owed is not None and settled:
                    await send(writer, self._snapshot(owed, blocked))
                    owed = None
                    if all(a.state in TERMINAL for a in self.actors):
                        await send(writer, {"t": "bye", "outcome": "completed"})
                        return "completed"
                if not held and inbox.empty():
                    with contextlib.suppress(TimeoutError):
                        held.append(await asyncio.wait_for(inbox.get(), timeout=lim.settle_s))
        finally:
            pumper.cancel()
            with contextlib.suppress(Exception):
                writer.close()


# ------------------------------------------------------------------------------------------- the coordinator side


class Policy:
    """How the coordinator chooses among enabled actors. Deterministic given the sequence of enabled sets.

    * ``{"kind": "prefer", "order": [...]}``: the first enabled actor in ``order`` (others by name, after).
    * ``{"kind": "alternate"}``: a different process from the previous grant's, when one is enabled.
    * ``{"kind": "seeded", "seed": n}``: a seeded pseudo-random choice.
    * ``{"kind": "schedule", "actors": [...], "then": {...}}``: an explicit prefix of choices (an actor that isn't
      enabled when its turn comes is deferred: the policy in ``then`` chooses, and the entry is kept), then ``then``.
    """

    def __init__(self, spec: dict[str, Any]) -> None:
        self.spec = spec
        self.kind = spec.get("kind", "prefer")
        if self.kind not in ("prefer", "alternate", "seeded", "schedule"):
            raise ValueError(f"unknown policy {self.kind!r}")
        self.rng = random.Random(spec.get("seed", 0))
        self.prefix = list(spec.get("actors", []))
        self.then = Policy(spec.get("then", {"kind": "prefer"})) if self.kind == "schedule" else None
        self.last_process: str | None = None

    def choose(self, enabled: list[tuple[str, str]]) -> tuple[str, str, str]:
        """``enabled`` is ``[(process, actor)]`` sorted; returns ``(process, actor, source)``."""
        if self.kind == "schedule":
            if self.prefix:
                want = self.prefix[0]
                hit = [e for e in enabled if e[1] == want]
                if hit:
                    self.prefix.pop(0)
                    return (*hit[0], "schedule")
            assert self.then is not None
            p, a, _ = self.then.choose(enabled)
            return p, a, "deferred" if self.prefix else "continuation"
        if self.kind == "prefer":
            order = {name: i for i, name in enumerate(self.spec.get("order", []))}
            pick = min(enabled, key=lambda e: (order.get(e[1], len(order)), e))
        elif self.kind == "alternate":
            other = [e for e in enabled if e[0] != self.last_process]
            pick = (other or enabled)[0]
        else:
            pick = self.rng.choice(enabled)
        self.last_process = pick[0]
        return (*pick, self.kind)


def _conds_met(conds: list[dict[str, Any]], markers: dict[str, dict[str, int]]) -> bool:
    return all(markers.get(c["actor"], {}).get(c["marker"], 0) >= int(c.get("count", 1)) for c in conds)


class Coordinator:
    """Serves one coordinated run for a fixed set of named processes, then returns its report.

    ``gates``: ``[{"actor": X, "match": regex | None, "from": [cond], "until": [cond]}]``, where a cond is
    ``{"actor": Y, "marker": M, "count": n}``. While every ``from`` cond holds and some ``until`` cond doesn't, X's
    pending statement (if it matches ``match``) isn't enabled. Gates order statements; they never write anything.

    ``stop``: ``{"key": K, "covers": [...], "after_s": T}``. The ``stop`` signal goes to every process once the
    union of the processes' ``report[K]`` lists covers ``covers``, or ``T`` seconds after the first grant.
    """

    def __init__(self, *, processes: Sequence[str], token: str, policy: dict[str, Any] | None = None,
                 gates: Sequence[dict[str, Any]] = (), stop: dict[str, Any] | None = None,
                 deadline_s: float = 150.0, epoch_timeout_s: float = 60.0, hello_timeout_s: float = 60.0,
                 stall_s: float = 20.0) -> None:
        self.expected = list(processes)
        self.token = token
        self.policy = Policy(policy or {"kind": "prefer"})
        self.policy_spec = policy or {"kind": "prefer"}
        self.gates = [dict(g, _rx=re.compile(g["match"]) if g.get("match") else None) for g in gates]
        self.stop = stop
        self.deadline_s, self.epoch_timeout_s = deadline_s, epoch_timeout_s
        self.hello_timeout_s, self.stall_s = hello_timeout_s, stall_s
        self.inbox: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.peers: dict[str, dict[str, Any]] = {}
        self.writers: dict[str, asyncio.StreamWriter] = {}
        self.decisions: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []
        self.t0 = time.monotonic()
        self._pending_conns: list[asyncio.Task[Any]] = []
        self._conn_of: dict[str, str] = {}  # connection tag -> process, after its hello

    def _ms(self) -> float:
        return round((time.monotonic() - self.t0) * 1000, 2)

    async def _accept(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        tag = f"conn{len(self._pending_conns)}"
        self._pending_conns.append(asyncio.current_task())  # type: ignore[arg-type]
        self.writers[tag] = writer
        await pump(reader, self.inbox, tag)

    async def _broadcast(self, msg: dict[str, Any], live: set[str]) -> None:
        for p in live:
            with contextlib.suppress(Exception):
                await send(self.writers[p], msg)

    async def _abort(self, outcome: str, reason: str, live: set[str]) -> dict[str, Any]:
        self.events.append({"event": "abort", "outcome": outcome, "reason": reason, "t_ms": self._ms()})
        await self._broadcast({"t": "abort", "reason": reason}, live)
        return self._report(outcome, reason)

    def _report(self, outcome: str, reason: str | None = None) -> dict[str, Any]:
        return {"protocol": PROTOCOL, "outcome": outcome, "reason": reason, "policy": self.policy_spec,
                "gates": [{k: v for k, v in g.items() if k != "_rx"} for g in self.gates], "stop": self.stop,
                "processes": self.peers, "decisions": self.decisions, "events": self.events,
                "grants": len(self.decisions), "elapsed_ms": self._ms()}

    async def serve(self, host: str = "0.0.0.0", port: int = 7777) -> dict[str, Any]:
        server = await asyncio.start_server(self._accept, host, port)
        try:
            return await self._run()
        finally:
            server.close()
            for w in self.writers.values():
                with contextlib.suppress(Exception):
                    w.close()

    async def _hellos(self) -> str | None:
        until = time.monotonic() + self.hello_timeout_s
        by_conn: dict[str, str] = {}
        while len(self.peers) < len(self.expected):
            left = until - time.monotonic()
            if left <= 0:
                return f"no hello from {sorted(set(self.expected) - set(self.peers))}"
            try:
                msg = await asyncio.wait_for(self.inbox.get(), timeout=left)
            except TimeoutError:
                continue
            conn = msg.pop("_from")
            if msg["t"] in ("_eof", "_bad"):
                if conn in by_conn:
                    return f"{by_conn[conn]} disconnected before the start"
                continue  # a connection that never said hello: dropped, and it gets nothing
            if msg["t"] != "hello" or msg.get("protocol") != PROTOCOL or msg.get("token") != self.token:
                return f"a connection sent {msg['t']!r} with a wrong protocol or token"
            p = msg.get("process")
            if p not in self.expected or p in self.peers:
                return f"unexpected or duplicate process {p!r}"
            by_conn[conn] = p
            self.peers[p] = {"version": msg.get("version"), "run_id": msg.get("run_id"),
                             "monitor_pid": msg.get("monitor_pid"), "actors": msg.get("actors", {}), "bye": None}
            self.writers[p] = self.writers.pop(conn)
            self._conn_of[conn] = p
        names = [n for p in self.peers.values() for n in p["actors"]]
        if len(set(names)) != len(names):
            return f"actor names aren't unique across processes: {names}"
        return None

    async def _run(self) -> dict[str, Any]:
        problem = await self._hellos()
        live = set(self.peers)
        if problem:
            return await self._abort("refused", problem, live)
        pids = {str(a["backend_pid"]): name for p in self.peers.values() for name, a in p["actors"].items()}
        epoch = 0
        await self._broadcast({"t": "start", "protocol": PROTOCOL, "epoch": epoch, "pids": pids}, live)
        self.events.append({"event": "start", "t_ms": self._ms(), "pids": pids})
        snaps: dict[str, dict[str, Any]] = {}
        last: dict[str, dict[str, Any]] = {}  # each actor's latest reported view, also after its process left
        reports: dict[str, dict[str, Any]] = {}
        deadline = self.t0 + self.deadline_s
        stopped = False
        first_grant_at: float | None = None
        last_grant_at = time.monotonic()
        while True:
            # 1. every live process's settled snapshot for this epoch
            epoch_until = time.monotonic() + self.epoch_timeout_s
            while any(snaps.get(p, {}).get("epoch") != epoch for p in live):
                left = min(epoch_until, deadline) - time.monotonic()
                if left <= 0:
                    late = sorted(p for p in live if snaps.get(p, {}).get("epoch") != epoch)
                    what = "the run's deadline" if time.monotonic() >= deadline else f"epoch {epoch}'s timeout"
                    return await self._abort("timeout", f"no snapshot from {late} before {what}", live)
                try:
                    msg = await asyncio.wait_for(self.inbox.get(), timeout=left)
                except TimeoutError:
                    continue
                p = self._conn_of.get(msg.pop("_from"))
                if p is None:
                    continue
                t = msg["t"]
                if t == "snap":
                    if p not in live or int(msg.get("epoch", -1)) != epoch:
                        return await self._abort("protocol_error", f"{p} sent a snapshot for epoch "
                                                 f"{msg.get('epoch')} during epoch {epoch}", live)
                    snaps[p] = msg
                    reports[p] = msg.get("report", {})
                    for name, view in msg["actors"].items():
                        last[name] = view
                elif t == "bye":
                    if any(last.get(n, {}).get("state") not in TERMINAL for n in self.peers[p]["actors"]):
                        return await self._abort("protocol_error", f"{p} said bye with an actor not done", live)
                    self.peers[p]["bye"] = msg.get("outcome")
                    live.discard(p)
                    self.events.append({"event": "bye", "process": p, "t_ms": self._ms()})
                elif t == "fail":
                    live.discard(p)
                    return await self._abort("failed", f"{p}: {msg.get('outcome')}: {msg.get('reason')}", live)
                elif t in ("_eof", "_bad"):
                    if p in live:
                        live.discard(p)
                        return await self._abort("lost_process", f"{p} disconnected ({t})", live)
                else:
                    return await self._abort("protocol_error", f"{p} sent {t!r}", live)
            if not live:
                self.events.append({"event": "end", "t_ms": self._ms()})
                return self._report("completed")
            # 2. a stop signal, once
            if self.stop and not stopped:
                covers = set(self.stop.get("covers", []))
                got = {x for r in reports.values() for x in r.get(self.stop["key"], [])}
                late = first_grant_at is not None and time.monotonic() - first_grant_at > self.stop.get("after_s", 1e9)
                if covers <= got or late:
                    stopped = True
                    why = "covered" if covers <= got else "after_s"
                    self.events.append({"event": "stop", "why": why, "at_grant": len(self.decisions),
                                        "t_ms": self._ms()})
                    await self._broadcast({"t": "signal", "name": "stop"}, live)
            # 3. the decision
            markers = {name: v.get("markers", {}) for name, v in last.items()}
            enabled: list[tuple[str, str]] = []
            gated: list[str] = []
            for p in sorted(live):
                for name, v in sorted(snaps[p]["actors"].items()):
                    if v["state"] != "pending" or v["request"] is None:
                        continue
                    if any(g["actor"] == name and (g["_rx"] is None or g["_rx"].search(v["request"]["sql"]))
                           and _conds_met(g.get("from", []), markers) and not _conds_met(g["until"], markers)
                           for g in self.gates):
                        gated.append(name)
                    else:
                        enabled.append((p, name))
            blocked = {name: v["blocked_by"] for p in live for name, v in snaps[p]["actors"].items()
                       if v["state"] == "blocked"}
            finished = sorted(n for n, v in last.items() if v.get("state") in TERMINAL)
            if not enabled:
                if not blocked and time.monotonic() - last_grant_at > self.stall_s:
                    return await self._abort("stall", f"nothing grantable for {self.stall_s} s; gated: {gated}",
                                             live)
                epoch += 1
                await self._broadcast({"t": "sync", "epoch": epoch}, live)
                await asyncio.sleep(0.01)
                continue
            setup = [e for e in enabled if snaps[e[0]]["actors"][e[1]]["commutes"]]
            if setup:
                proc, name, source = (*setup[0], "setup")
            else:
                proc, name, source = self.policy.choose(enabled)
            req = snaps[proc]["actors"][name]["request"]
            grant = len(self.decisions)
            epoch += 1
            now = time.monotonic()
            first_grant_at = first_grant_at or now
            last_grant_at = now
            self.decisions.append({"decision": grant, "grant": grant, "epoch": epoch, "process": proc,
                                   "version": self.peers[proc]["version"], "actor": name, "source": source,
                                   "enabled": [n for _p, n in enabled], "gated": gated, "blocked": blocked,
                                   "kind": req["kind"], "statement_id": req["statement_id"], "sql": req["sql"][:300],
                                   "t_ms": self._ms()})
            await send(self.writers[proc], {"t": "grant", "epoch": epoch, "actor": name, "grant": grant,
                                            "decision": grant, "statement_id": req["statement_id"],
                                            "source": source, "enabled": [n for _p, n in enabled],
                                            "blocked": blocked, "finished": finished})
            await self._broadcast({"t": "sync", "epoch": epoch}, live - {proc})


def merge_traces(traces: dict[str, Any]) -> Any:
    """One trace for the server-log check from several processes' traces (``{process: Trace or dict}``). Actor names
    are unique across processes (the coordinator refuses otherwise); grant numbers are global."""
    from types import SimpleNamespace
    actors: dict[str, Any] = {}
    records: list[Any] = []
    for t in traces.values():
        d = t if isinstance(t, dict) else t.to_dict()
        for name, a in d["actors"].items():
            if name in actors:
                raise ValueError(f"actor {name!r} appears in two processes")
            actors[name] = a
        records += [SimpleNamespace(**r) for r in d["records"]]
    return SimpleNamespace(actors=actors, records=records)
