"""EXPERIMENTAL: serialize every use of the one disposable PostgreSQL (127.0.0.1:55441) across parallel worktrees.

    python research_tools/pg_lock.py [--log FILE] -- COMMAND ARGS...

Takes an exclusive ``fcntl.flock`` on a fixed lock file (``$DS_MVP_PG_LOCK``, default under /private/tmp), runs
COMMAND, releases the lock, and exits with COMMAND's own exit status (never masked). Each acquisition and release is
appended, with the waiting time, to ``--log`` (default: the lock file + ``.log``). Any live database test, isolated
run or experiment must go through this wrapper; offline tests need not.
"""

from __future__ import annotations

import fcntl
import os
import subprocess
import sys
import time

DEFAULT = "/private/tmp/ds_mvp_pg.lock"


def main() -> int:
    args = sys.argv[1:]
    log = None
    if args[:1] == ["--log"]:
        log, args = args[1], args[2:]
    if args[:1] != ["--"] or len(args) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    cmd = args[1:]
    path = os.environ.get("DS_MVP_PG_LOCK", DEFAULT)
    log = log or path + ".log"
    t0 = time.time()
    with open(path, "a+") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        waited = time.time() - t0
        with open(log, "a") as lf:
            lf.write(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} pid={os.getpid()} cwd={os.getcwd()} "
                     f"acquired waited={waited:.1f}s cmd={' '.join(cmd)[:300]}\n")
        try:
            rc = subprocess.call(cmd)
        finally:
            with open(log, "a") as lf:
                lf.write(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} pid={os.getpid()} released "
                         f"exit={rc if 'rc' in dir() else 'exception'}\n")
    return rc


if __name__ == "__main__":
    sys.exit(main())
