"""Offline tests of #27's client-side verification (research_tools/confirmatory_prep/verified_request.py). A fake
OpenAI-style client only; no real Meta request is made."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "research_tools" / "confirmatory_prep"))
sys.path.insert(0, str(ROOT / "src"))
import prep  # noqa: E402
import verified_request as vr  # noqa: E402

from deployspecter.config import Settings  # noqa: E402
from deployspecter.meta_client import MetaClient  # noqa: E402


class FakeResponses:
    def __init__(self, model="muse-spark-1.3", fail=False):
        self.model, self.fail, self.calls = model, fail, 0

    def create(self, **kw):
        self.calls += 1
        if self.fail:
            raise ConnectionError("simulated transport failure")
        return SimpleNamespace(model=self.model, output_text='{"workloads": []}', status="completed",
                               usage=SimpleNamespace(input_tokens=100, output_tokens=200, output_tokens_details=None))


def client(responses):
    s = Settings(admin_dsn="", test_dsn="", model_api_key=None, model_base_url="http://invalid",
                 model_name="muse-spark-1.3")
    return MetaClient(s, client=SimpleNamespace(responses=responses), max_requests=5, max_estimated_usd=1.0)


BASE = dict(proposer_source=b"proposer", instructions="instr", context={"registry": ["op"]},
            schema={"type": "object"}, prompt="p", schema_name="s", max_output_tokens=1000)


def frozen():
    return prep.freeze_record(proposer_source=b"proposer", instructions="instr", context={"registry": ["op"]},
                              schema={"type": "object"}, model="muse-spark-1.3", reasoning_effort="medium",
                              store=False, max_output_tokens=1000)


def test_a_matching_request_and_provider_model_is_usable():
    r = FakeResponses()
    out = vr.verified_generate(client(r), frozen(), **BASE)
    assert out["usable"] and out["provider_model"] == "muse-spark-1.3" and r.calls == 1


@pytest.mark.parametrize("change", [{"context": {"registry": ["op", "x"]}}, {"schema": {"type": "array"}},
                                    {"proposer_source": b"other"}, {"instructions": "instr2"},
                                    {"max_output_tokens": 2000}])
def test_any_changed_frozen_input_refuses_before_sending(change):
    r = FakeResponses()
    with pytest.raises(SystemExit, match="changed"):
        vr.verified_generate(client(r), frozen(), **{**BASE, **change})
    assert r.calls == 0


def test_a_changed_requested_model_refuses_before_sending():
    r = FakeResponses()
    c = client(r)
    c.model = "muse-spark-1.4"
    with pytest.raises(SystemExit, match="model"):
        vr.verified_generate(c, frozen(), **BASE)
    assert r.calls == 0


def test_a_different_provider_reported_model_is_a_provider_failure_not_used():
    out = vr.verified_generate(client(FakeResponses(model="muse-spark-1.3-preview")), frozen(), **BASE)
    assert out["status"] == "provider_model_mismatch" and not out["usable"] and out["est_usd"] > 0


def test_a_transport_failure_keeps_its_worst_case_charge_and_is_not_retried():
    r = FakeResponses(fail=True)
    c = client(r)
    out = vr.verified_generate(c, frozen(), **BASE)
    assert out["status"] == "request_failed" and not out["usable"] and out["charged_usd"] > 0 and r.calls == 1
    assert c.requests_sent == 1
