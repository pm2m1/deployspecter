"""EXPERIMENTAL, offline adversarial regression for a demonstrated teardown defect (docs/experimental_mvp/reliability/
race_probes_1): a SIGINT that lands inside the isolated runner's ``finally`` aborted the rest of the teardown, leaving
the internal network behind with the disposable PostgreSQL container still attached to it.

No Docker or database is used: ``docker`` is replaced by a recorder that fails the body at ``network create`` (so the
``finally`` runs) and delivers a real SIGINT to this process during the first teardown command. The teardown must
still issue every command, and the interrupt must still surface afterwards as KeyboardInterrupt.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "research_tools" / "isolated"))
import run_coordinated as rc  # noqa: E402
import run_isolated as ri  # noqa: E402

TEARDOWN = ["rm", "network disconnect", "network rm"]


class Recorder:
    def __init__(self, interrupt_on: str) -> None:
        self.calls: list[str] = []
        self.interrupt_on = interrupt_on
        self.sent = False

    def __call__(self, *args: str, timeout: float = 300, check: bool = True) -> subprocess.CompletedProcess[str]:
        key = args[0] if args[0] not in ("network",) else f"network {args[1]}"
        self.calls.append(key)
        if key == "network create":
            raise RuntimeError("the body fails here, so the finally runs")
        if key == self.interrupt_on and not self.sent:
            self.sent = True
            os.kill(os.getpid(), signal.SIGINT)  # delivered now, as the test's proc.send_signal would be
        return subprocess.CompletedProcess(["docker", *args], 0, "", "")


@pytest.fixture
def default_sigint():
    old = signal.signal(signal.SIGINT, signal.default_int_handler)
    yield
    signal.signal(signal.SIGINT, old)


def teardown_part(calls: list[str]) -> list[str]:
    return [c for c in calls[calls.index("network create") + 1:] if c in TEARDOWN]


@pytest.mark.parametrize("interrupt_on", ["rm", "network disconnect"])
def test_run_smoke_teardown_completes_when_interrupted(monkeypatch, tmp_path, default_sigint, interrupt_on):
    rec = Recorder(interrupt_on)
    monkeypatch.setattr(ri, "docker", rec)
    with pytest.raises(KeyboardInterrupt):
        ri.run_smoke(object(), variant="probe", schedule=[], out=tmp_path, image=("ds-mvp-iso:x", "sha256:x"),
                     mode="chancy-runtime")
    assert rec.sent
    assert teardown_part(rec.calls) == TEARDOWN  # every teardown command was issued despite the interrupt


@pytest.mark.parametrize("interrupt_on", ["rm", "network disconnect"])
def test_coordinated_teardown_completes_when_interrupted(monkeypatch, tmp_path, default_sigint, interrupt_on):
    rec = Recorder(interrupt_on)
    monkeypatch.setattr(ri, "docker", rec)
    spec = {"processes": {"A": [], "B": []}, "gates": [], "covers": []}
    with pytest.raises(KeyboardInterrupt):
        rc.simultaneous(object(), {"A": ("a", "a"), "B": ("b", "b")}, spec, {"kind": "alternate"}, tmp_path,
                        label="probe")
    assert rec.sent
    after = teardown_part(rec.calls)
    assert after.count("rm") == 3 and after[-2:] == ["network disconnect", "network rm"]  # 3 containers, then net


def test_a_signal_outside_teardown_is_not_deferred(default_sigint):
    """The deferral is scoped to teardown: elsewhere an interrupt still interrupts at once."""
    with pytest.raises(KeyboardInterrupt):
        os.kill(os.getpid(), signal.SIGINT)
        signal.pause() if hasattr(signal, "pause") else None
