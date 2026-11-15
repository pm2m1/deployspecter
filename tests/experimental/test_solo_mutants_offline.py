"""Solo r3 design: the seeded mutant generator and its sealed store (offline; synthetic tree, throwaway seeds)."""

import hashlib
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "research_tools" / "solo"))
import mutants as mu  # noqa: E402
import sealed  # noqa: E402

QUEUE = '''import x

CLAIM = """
SELECT id FROM jobs WHERE status = 'queued' ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 1
"""
DONE = "UPDATE jobs SET status = 'done' WHERE id = %s AND status = 'running' AND claim_id = %s RETURNING id"
INS = "INSERT INTO runs (job_id) VALUES (%s) ON CONFLICT (job_id) DO NOTHING"


async def finish(conn, job):
    await conn.execute(DONE, (job,))
    await conn.execute("INSERT INTO log (job_id) VALUES (%s)", (job,))


def pick(qs):
    return qs.select_for_update(skip_locked=True).filter(state="new").first()
'''
MIGRATION = (
    "INSERT INTO b (id) SELECT a.id FROM a WHERE a.flag AND NOT EXISTS (SELECT 1 FROM b WHERE b.id = a.id)"
    " AND a.id < 100;\n"
    "CREATE FUNCTION trg() RETURNS trigger AS $$ BEGIN IF TG_OP = 'DELETE' THEN DELETE FROM b WHERE id = OLD.id;"
    " END IF; RETURN NULL; END $$ LANGUAGE plpgsql;\n"
)
SECRET_SQL = ["FOR UPDATE", "SKIP LOCKED", "ON CONFLICT", "TG_OP", "status", "queue.py", "0002_backfill"]


@pytest.fixture()
def env(tmp_path):
    tree = tmp_path / "tree"
    (tree / "pkg" / "migrations").mkdir(parents=True)
    (tree / "pkg" / "queue.py").write_text(QUEUE)
    (tree / "pkg" / "migrations" / "0002_backfill.sql").write_text(MIGRATION)
    (tree / "tests").mkdir()
    (tree / "tests" / "test_q.py").write_text("SQL = 'SELECT 1 FOR UPDATE'\n")  # tests are never mutated
    seed = "ab" * 32
    seeds = tmp_path / "seeds.json"
    seeds.write_text(json.dumps({"mutant_seed": seed, "selection_seed": "cd" * 32}))
    return {"tree": tree, "seeds": seeds, "sha": hashlib.sha256(seed.encode()).hexdigest(),
            "store": tmp_path / "store", "tmp": tmp_path}


def _args(env, *extra):
    return ["--app", "demo", "--store", str(env["store"]), "--seeds", str(env["seeds"]), "--seed-sha256", env["sha"],
            *extra]


def test_every_operator_finds_its_sites(env):
    by_op, dropped = mu.enumerate_sites(mu.tree_files(env["tree"]))
    assert {op: len(v) for op, v in by_op.items()} == {"RM1": 4, "RM2": 2, "RM3": 3, "RM4": 1, "RM5": 1, "RM6": 2}
    assert dropped == 0
    assert all(not s.path.startswith("tests/") for v in by_op.values() for s in v)


def test_generate_prints_only_counts_and_hashes_and_the_store_doesnt_leak(env, capsys):
    assert mu.main(["generate", *_args(env, "--tree", str(env["tree"]))]) == 0
    out = capsys.readouterr().out
    doc = json.loads(out)
    assert doc["mutants"] == 13 and doc["sampled"]["RM1"] == 4
    for word in SECRET_SQL:
        assert word not in out
    store = env["store"] / "demo"
    for p in store.iterdir():
        data = p.read_bytes()
        for word in SECRET_SQL:
            assert word.encode() not in data, (p.name, word)
    index = json.loads((store / "INDEX.json").read_text())
    assert set(index) == {"app", "files_scanned", "mutants"}
    assert all(set(m) == {"id", "sealed_sha256"} for m in index["mutants"])


def test_decode_refuses_until_results_exist(env, capsys):
    mu.main(["generate", *_args(env, "--tree", str(env["tree"]))])
    mid = json.loads(capsys.readouterr().out)["ids"][0]
    results = env["tmp"] / "RESULTS.json"
    with pytest.raises(SystemExit, match="no results file"):
        mu.main(["decode", *_args(env, "--id", mid, "--results", str(results))])
    results.write_text(json.dumps({mid: {"verdict": "HOLDS"}}))
    with pytest.raises(SystemExit, match="no final verdict"):
        mu.main(["decode", *_args(env, "--id", mid, "--results", str(results))])
    results.write_text(json.dumps({mid: {"verdict": "VIOLATED"}}))
    assert mu.main(["decode", *_args(env, "--id", mid, "--results", str(results))]) == 0
    rec = json.loads(capsys.readouterr().out)
    assert rec["id"] == mid and rec["op"] in mu.OPERATORS and rec["verdict"] == "VIOLATED"
    log = (env["store"] / "demo" / "ACCESS.log").read_text()
    assert '"decode"' in log


def test_materialize_applies_exactly_one_change(env, capsys):
    mu.main(["generate", *_args(env, "--tree", str(env["tree"]))])
    ids = json.loads(capsys.readouterr().out)["ids"]
    out = env["tmp"] / "mutated"
    assert mu.main(["materialize", *_args(env, "--id", ids[0], "--tree", str(env["tree"]), "--out", str(out))]) == 0
    printed = capsys.readouterr().out
    assert set(json.loads(printed)) == {"id", "tree_sha256"}
    changed = [p for p in out.rglob("*") if p.is_file() and p.read_bytes() != (env["tree"] / p.relative_to(out))
               .read_bytes()]
    assert len(changed) == 1


def test_wrong_seed_is_refused(env, capsys):
    assert mu.main(["generate", "--app", "demo", "--store", str(env["store"]), "--seeds", str(env["seeds"]),
                    "--seed-sha256", "0" * 64, "--tree", str(env["tree"])]) == 3


def test_sample_is_deterministic_and_depends_on_the_sealed_seed():
    sites = {op: [] for op in mu.OPERATORS}
    sites["RM1"] = [mu.Site("RM1", f"pkg/m{i}.py", i, 0, 0, 1, "") for i in range(40)]
    a1 = mu.sample(sites, "seed-a", "demo")
    a2 = mu.sample(sites, "seed-a", "demo")
    b = mu.sample(sites, "seed-b", "demo")
    assert a1 == a2 and len(a1) == mu.PER_OPERATOR
    assert {s.path for s in a1} != {s.path for s in b}


def test_seal_roundtrip_and_tamper():
    blob = sealed.seal("s" * 64, "id1", b"secret record")
    assert b"secret" not in blob
    assert sealed.unseal("s" * 64, "id1", blob) == b"secret record"
    with pytest.raises(sealed.SealError):
        sealed.unseal("s" * 64, "id2", blob)
    with pytest.raises(sealed.SealError):
        sealed.unseal("t" * 64, "id1", blob)
