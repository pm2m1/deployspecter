"""EXPERIMENTAL, optional: third-party libraries in the isolated runner. Not part of the core regression suite.

Runs only when DS_THIRDPARTY_SDIST_DIR points at a directory with the pinned sdists (research_tools/isolated/
fetch_pinned.sh) and the disposable MVP PostgreSQL is running. Each test builds the pinned image, runs one container
on an internal network and tears it down; the runner's own exit status is the verdict.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SDISTS = Path(os.environ.get("DS_THIRDPARTY_SDIST_DIR", "/nonexistent"))
SECRET = Path(os.environ.get("DS_MVP_SECRET_FILE", os.path.expanduser("~/.ds_mvp_pg_password")))
pytestmark = pytest.mark.skipif(not (SDISTS.is_dir() and SECRET.exists()),
                                reason="set DS_THIRDPARTY_SDIST_DIR and start the disposable MVP PostgreSQL")


def runner(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k not in ("MODEL_API_KEY", "DATABASE_URL")}
    return subprocess.run([sys.executable, str(REPO / "research_tools/isolated/run_isolated.py"), *args, "--out",
                           str(tmp_path / "out")], capture_output=True, text=True, timeout=900, env=env)


@pytest.mark.parametrize("args", [
    ["pgqueuer", "--pgqueuer-sdist", "{pgq}"],
    ["pgqueuer", "--public-factory", "--pgqueuer-sdist", "{pgq}"],  # must be refuted by the server log
    ["chancy-runtime", "--chancy-sdist", "{chancy}"],
    ["chancy-runtime", "--with-notifications", "--chancy-sdist", "{chancy}"],  # must be refuted
], ids=["pgqueuer_lifecycle", "pgqueuer_public_factory_refuted", "chancy_runtime", "chancy_notifications_refuted"])
def test_third_party_path_gives_its_expected_verdict(tmp_path, args):
    args = [a.format(pgq=SDISTS / "pgqueuer-1.6.0.tar.gz", chancy=SDISTS / "chancy-0.26.0.tar.gz") for a in args]
    proc = runner(tmp_path, *args)
    assert proc.returncode == 0, proc.stdout[-2000:] + proc.stderr[-2000:]


def leftovers() -> tuple[str, str]:
    ps = subprocess.run(["docker", "ps", "-a", "--filter", "name=ds-mvp-iso-run", "-q"], capture_output=True,
                        text=True).stdout.strip()
    nets = subprocess.run(["docker", "network", "ls", "--filter", "name=ds-mvp-iso-", "-q"], capture_output=True,
                          text=True).stdout.strip()
    return ps, nets


def test_a_tampered_sdist_is_refused_before_anything_starts(tmp_path):
    bad = tmp_path / "pgqueuer-1.6.0.tar.gz"
    bad.write_bytes((SDISTS / "pgqueuer-1.6.0.tar.gz").read_bytes() + b"\0")
    proc = runner(tmp_path, "pgqueuer", "--pgqueuer-sdist", str(bad))
    assert proc.returncode != 0 and "isn't the pinned sdist" in proc.stderr
    assert leftovers() == ("", "")


def test_an_interrupted_run_still_tears_down(tmp_path):
    import signal
    import time
    env = {k: v for k, v in os.environ.items() if k not in ("MODEL_API_KEY", "DATABASE_URL")}
    proc = subprocess.Popen([sys.executable, str(REPO / "research_tools/isolated/run_isolated.py"), "chancy-runtime",
                             "--chancy-sdist", str(SDISTS / "chancy-0.26.0.tar.gz"), "--out", str(tmp_path / "out")],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline and not leftovers()[0]:  # wait until its container is running
        time.sleep(0.5)
    assert leftovers()[0], "the container never started"
    proc.send_signal(signal.SIGINT)
    proc.communicate(timeout=120)
    assert proc.returncode != 0
    assert leftovers() == ("", "")  # the runner's finally removed the container and the network
