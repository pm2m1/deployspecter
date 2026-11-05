"""EXPERIMENTAL: signal-safe teardown for the isolated runners.

A SIGINT or SIGTERM that lands while a runner's ``finally`` is removing its containers and network used to abort the
rest of the teardown (demonstrated: docs/experimental_mvp/reliability/race_probes_1, where the internal network was
left with the disposable PostgreSQL container still attached). Inside ``deferred_signals()`` such a signal is
recorded instead, and re-delivered to this process, once, after the block: the teardown completes and the interrupt
still ends the run (KeyboardInterrupt for SIGINT, the default action for SIGTERM).

Limits: only the main thread can install handlers (elsewhere the block runs undeferred), and a terminal Ctrl-C sent to
the whole process group can still kill a teardown ``docker`` child mid-command; that command then fails visibly
(``check=False``) and the runner's own leftover check reports it.
"""

from __future__ import annotations

import signal
import threading
from collections.abc import Iterator
from contextlib import contextmanager

DEFERRED = (signal.SIGINT, signal.SIGTERM)


@contextmanager
def deferred_signals(signals: tuple[int, ...] = DEFERRED) -> Iterator[list[int]]:
    if threading.current_thread() is not threading.main_thread():
        yield []
        return
    received: list[int] = []

    def hold(sig: int, frame: object) -> None:
        received.append(sig)

    previous = {s: signal.signal(s, hold) for s in signals}
    try:
        yield received
    finally:
        for s, h in previous.items():
            signal.signal(s, h)
        if received:
            signal.raise_signal(received[0])
