"""EXPERIMENTAL: one environment snapshot for the reliability stress run (research_tools/stress_isolated.sh).

    python research_tools/stress_snapshot.py OUT.json

Records the git revision, Docker containers and networks, the disposable PostgreSQL's lifecycle and sessions, locks,
schemas, its data tmpfs usage, host disk and memory. It only ever connects to the disposable database (127.0.0.1:55441,
cluster ``ds_mvp_disposable``), never to the protected 55432 one. A probe that fails is recorded as such, not skipped.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]


def run(*cmd: str) -> dict[str, Any]:
    try:
        p = subprocess.run(list(cmd), capture_output=True, text=True, timeout=60)
        return {"exit": p.returncode, "out": p.stdout[-20000:], "err": p.stderr[-2000:]}
    except Exception as exc:  # noqa: BLE001 - a failed probe is evidence
        return {"error": f"{type(exc).__name__}: {exc}"}


def postgres() -> dict[str, Any]:
    secret = Path(os.path.expanduser("~/.ds_mvp_pg_password"))
    if not secret.exists():
        return {"available": False}
    try:
        import psycopg
        with psycopg.connect(host="127.0.0.1", port=55441, user="ds_mvp", password=secret.read_text().strip(),
                             dbname="ds_mvp", autocommit=True, connect_timeout=10,
                             application_name="ds_mvp_stress_probe") as c:
            if c.execute("SELECT current_setting('cluster_name')").fetchone()[0] != "ds_mvp_disposable":
                return {"refused": "not the disposable cluster"}

            def rows(q: str) -> list[list[Any]]:
                return [list(r) for r in c.execute(q).fetchall()]
            return {"available": True,
                    "sessions": rows("SELECT application_name, state, backend_type, count(*) FROM pg_stat_activity "
                                     "WHERE datname = current_database() AND pid <> pg_backend_pid() "
                                     "GROUP BY 1, 2, 3 ORDER BY 1, 2, 3"),
                    "locks": rows("SELECT locktype, mode, granted, count(*) FROM pg_locks WHERE pid <> "
                                  "pg_backend_pid() GROUP BY 1, 2, 3 ORDER BY 1, 2, 3"),
                    "prepared_xacts": rows("SELECT gid FROM pg_prepared_xacts"),
                    "schemas": rows("SELECT nspname FROM pg_namespace WHERE nspname NOT LIKE 'pg_%' AND nspname "
                                    "<> 'information_schema' ORDER BY 1"),
                    "public_objects": rows("SELECT count(*) FROM pg_class r JOIN pg_namespace n ON n.oid = "
                                           "r.relnamespace WHERE n.nspname = 'public'"),
                    "database_size": rows("SELECT pg_database_size(current_database())"),
                    "wal_lsn": rows("SELECT pg_current_wal_lsn()::text")}
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}


def main() -> int:
    out = Path(sys.argv[1])
    snap = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "git": run("git", "-C", str(REPO), "rev-parse", "HEAD"),
            "git_dirty": run("git", "-C", str(REPO), "status", "--porcelain", "--untracked-files=no"),
            "docker_ps": run("docker", "ps", "-a", "--format", "{{.Names}}\t{{.Status}}\t{{.Image}}"),
            "docker_networks": run("docker", "network", "ls", "--format", "{{.Name}}"),
            "mvp_pg_inspect": run("docker", "inspect", "-f", "{{.State.Status}} {{.State.StartedAt}} "
                                  "restarts={{.RestartCount}} oom={{.State.OOMKilled}}", "deployspecter-mvp-pg"),
            "mvp_pg_tmpfs": run("docker", "exec", "deployspecter-mvp-pg", "df", "-k", "/var/lib/postgresql/data"),
            "protected_inspect": run("docker", "inspect", "-f", "{{.Id}} {{.State.StartedAt}}", "deployspecter-pg"),
            "docker_df": run("docker", "system", "df"),
            "host_df": run("df", "-k", str(REPO), "/private/tmp"),
            "vm_stat": run("vm_stat"),
            "postgres": postgres()}
    out.write_text(json.dumps(snap, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
