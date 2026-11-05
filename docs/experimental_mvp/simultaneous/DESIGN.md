# Simultaneous mixed-version execution: design and limits (EXPERIMENTAL, NONBLIND)

## What runs

Three restricted containers share one fresh `--internal` network with the disposable PostgreSQL (`127.0.0.1:55441`, never 55432). Each container uses the existing runc flags:

- uid 65534;
- a read-only root and a noexec tmpfs;
- all capabilities dropped and `no-new-privileges`;
- 64 PIDs, 512 MB memory, 1 CPU;
- a wall-clock kill.

| Container | Image | Holds | Database credentials |
|---|---|---|---|
| A | digest-pinned base + **Chancy 0.25.1** (sdist sha256-verified, source hashes checked inside) | `old_worker`, and `old_push` in some scenarios | the disposable database's only |
| B | the same base + **Chancy 0.26.0** (optionally with the declared mutant) | `new_worker`, and `new_push` in some scenarios | the disposable database's only |
| coordinator | the B image, running only `deployspecter.experimental.coordinated` | the grant decisions | **none** |

Every actor has its own database session, and each process has its own lock-wait monitor session. Chancy's own code runs through the existing stepped one-connection pool, with `notifications=False`. It computes every SQL argument itself: nothing is replayed, and no third-party source is modified, except in the declared mutation control.

## What is controlled, and what isn't

The coordinator (`ds-coord/1`) decides the order in which database statements are granted, across both processes, with one global grant numbering. It decides only when every live process has reported a *settled* snapshot for the current epoch: no actor running, granted or executing. So at each decision, every live actor is parked at its gate with its next statement, blocked on a lock, or done. That is the in-process engine's lockstep rule, extended across processes.

Not controlled:
- Python and event-loop interleavings inside a process between statements, such as which of a worker's tasks asks next, and its timers;
- the wall-clock timing of Chancy's polling.

A policy with a seed repeats its *choices* given the same enabled sets. It does not guarantee the same enabled sets. Repeatability is therefore measured (`simultaneous_repeat_*`), never assumed.

**Gates** order statements and never write anything. A gated statement inside an open transaction keeps that transaction open. So gates are used only where the awaited event follows within seconds, and the harness's `idle_in_transaction` limit (15 s) turns a misuse into a refused run. That is how the first `retry_handoff` design failed (`simultaneous_scenarios_1`).

## The mixed-version window

**Precondition.** Before the window, both pre-phases have run and the host has checked `chancy_schema_version = 8`:
- 0.25.1 migrates to its own latest schema and declares the queues;
- 0.26.0 applies v7 and v8.

**Opening.** The window opens with the first producer grant. A gate holds every producer statement until every worker has completed a claim query, so both versions' workers are started, registered and polling before any job exists.

**Closing.** The window closes at the coordinator's `stop` signal, which is sent once the expected jobs have succeeded. A stop on the coordinator's timeout makes the run INCOMPLETE, with no property verdict.

**After the stop.** Workers stop with Chancy's own `stop()`, after queued job updates have been flushed. Cancellation happens only at safe points:
- a task cancelled while its statement is on the wire waits for the statement to complete, and the cancellation is then raised at the statement boundary (`AsyncRun.defer_cancellation`, recorded in `provenance.cancellation`);
- a task cancelled at its gate never sends its statement (`cancelled_while_waiting`);
- a transaction whose COMMIT was cancelled before its grant is rolled back, ungranted and recorded;
- a grant that arrives after the framework cancelled the reported request is declined and decided again.

An earlier hold (`AsyncRun.quiesced`) is no longer used for stops: it blocked 0.26.0's own graceful drain and stalled the cross-process lockstep (`simultaneous_diagnostics_1`).

## Fail-closed rules

A run is REFUSED, with no property verdict, and is never re-run, on any of the following:
- **Coordinator:** a missing hello, a wrong nonce, a stale or missing epoch snapshot, a peer lost without `bye`, an unexpected message, a decline for a grant that was never given, or a stall. A grant whose request has gone is declined, not refused;
- **Process limits:** a lock held by a session no process reported (`external_blocker`), or a process's own limits;
- **Isolation:** any failed isolation probe in any container;
- **Server log:** any unexplained session or row, a statement or typed value that differs, a mismatched error, or a grant order that disagrees with the server's statement order;
- **Teardown:** a session that outlives the run.

## Limits

- **NONBLIND.** The scenarios, gates and properties were written by the harness's author, after reading Chancy's source, docstrings and changelog.
- **Not hostile-grade isolation.** runc shares the Docker Desktop VM's kernel, so it suits only pinned, reviewed public releases.
- **Undocumented deployment.** Simultaneous 0.25.1 and 0.26.0 workers are outside Chancy's documented upgrade procedure (the 0.26.0 changelog says "Stop your workers" before migrating).
- **Bounded exploration.** Seeded policies sample a schedule family. An empty frontier, or no violation, is not exhaustive over all executions.
- **Scope of stepping.** The workers' `AsyncExecutor` runs jobs as asyncio tasks in the worker's process. The process and thread executors are not supported.
