"""Run the root suite against the disposable cluster only. Refuses port 55432 and any cluster that isn't
ds_mvp_disposable. Never prints the password."""
import os, subprocess, sys
from pathlib import Path
import psycopg
PORT = 55441
assert PORT != 55432
pw = (Path.home() / ".ds_mvp_pg_password").read_text().strip()
with psycopg.connect(host="127.0.0.1", port=PORT, user="ds_mvp", password=pw, dbname="ds_mvp", connect_timeout=5) as c:
    name = c.execute("SHOW cluster_name").fetchone()[0]
    port = int(c.execute("SHOW port").fetchone()[0])
if name != "ds_mvp_disposable":
    sys.exit(f"refused: cluster_name {name!r}")
print(f"identity ok: cluster_name={name} host_port={PORT} server_port={port}", flush=True)
env = {k: v for k, v in os.environ.items() if k not in ("MODEL_API_KEY",)}
env["DATABASE_URL"] = f"postgresql://ds_mvp:{pw}@127.0.0.1:{PORT}/ds_mvp"
out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=False)
(out / "git_revision").write_text(subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout)
cmd = [".venv/bin/python", "-m", "pytest", "tests", "-q", "-p", "no:cacheprovider", "-rfE",
       f"--junitxml={out}/root_junit.xml", "--ignore=tests/experimental", "--ignore=tests/thirdparty_isolated",
       "--ignore=tests/test_baselines.py", "--ignore=tests/test_documented_public.py", "--ignore=tests/test_meta_client.py",
       "--ignore=tests/test_scenarios.py", "--ignore=tests/test_site.py"]
with open(out / "root_stdout.txt", "w") as so, open(out / "root_stderr.txt", "w") as se:
    rc = subprocess.call(cmd, env=env, stdout=so, stderr=se)
(out / "root_exit_status").write_text(f"{rc}\n")
for f in ("root_stdout.txt", "root_stderr.txt", "root_junit.xml"):  # the password must never be kept
    p = out / f; p.write_text(p.read_text().replace(pw, "<redacted>"))
print("exit", rc)
sys.exit(rc)
