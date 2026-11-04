"""EXPERIMENTAL Muse adapter, offline: a fake Responses client. No network, no key."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from deployspecter.config import Settings
from deployspecter.experimental import muse
from deployspecter.experimental import workloads as wl
from deployspecter.meta_client import MetaClient, ModelBudgetExceeded


class FakeResponses:
    def __init__(self, text):
        self.text, self.calls = text, []

    def create(self, **kw):
        self.calls.append(kw)
        usage = SimpleNamespace(input_tokens=1200, output_tokens=800,
                                output_tokens_details=SimpleNamespace(reasoning_tokens=300))
        return SimpleNamespace(output_text=self.text, status="completed", usage=usage)


def client(text, **kw):
    fake = FakeResponses(text)
    settings = Settings(admin_dsn="", test_dsn="", model_api_key=None)
    return MetaClient(settings, client=SimpleNamespace(responses=fake), max_requests=kw.get("n", 2),
                      max_estimated_usd=kw.get("usd", 1.0)), fake


def test_proposals_are_validated_and_ledgered(tmp_path):
    good = wl.curated()[3][1]
    bad = json.loads(json.dumps(good))
    bad["invocations"][0]["handler"] = "drop_everything"
    c, fake = client(json.dumps({"proposals": [good, bad]}))
    out = muse.propose(c, n=2, ledger=tmp_path / "ledger.jsonl", call_label="t1")
    assert [label for label, _ in out] == ["t1#0", "t1#1"]
    entry = json.loads((tmp_path / "ledger.jsonl").read_text())
    assert [v["valid"] for v in entry["verdicts"]] == [True, False]
    assert "unknown handler v1.drop_everything" in entry["verdicts"][1]["reasons"][0]
    assert entry["input_tokens"] == 1200 and entry["estimated_usd"] == pytest.approx(0.0049)
    assert entry["prompt_sha256"] == muse.prompt_hash(2) and entry["model"] == "muse-spark-1.3"
    sent = fake.calls[0]
    assert sent["text"]["format"]["strict"] is True and sent["store"] is False
    assert sent["reasoning"] == {"effort": "medium"}


def test_the_prompt_carries_no_defect_description_and_no_secret():
    text = (muse.INSTRUCTIONS + muse.prompt(4)).lower()
    assert not any(w in text for w in ("stale", "defect", "buggy", "fixed build", "race", "password", "api_key"))
    assert "backfill_v2" in text and "reserved_v2" in text  # the capturable surface is there


def test_the_budget_stops_calls_before_sending(tmp_path):
    c, fake = client(json.dumps({"proposals": []}), n=1)
    muse.propose(c, n=1, ledger=tmp_path / "l.jsonl", call_label="a")
    with pytest.raises(ModelBudgetExceeded):
        muse.propose(c, n=1, ledger=tmp_path / "l.jsonl", call_label="b")
    assert len(fake.calls) == 1
    lines = (tmp_path / "l.jsonl").read_text().splitlines()
    assert json.loads(lines[1])["error"] == "ModelBudgetExceeded"


def test_unparsable_output_yields_no_proposals(tmp_path):
    c, _ = client("not json")
    assert muse.propose(c, n=1, ledger=tmp_path / "l.jsonl", call_label="x") == []
