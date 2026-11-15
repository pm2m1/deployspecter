"""Solo r3 design: owner decision 3, a missing request excludes the case for every arm; prep_r3 is unchanged."""

import hashlib
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "research_tools" / "solo"))
import admission_solo as ad  # noqa: E402
import prep_r3  # noqa: E402  (on sys.path through admission_solo)

PREP_R3_SHA256 = "4457f97454f3c5d636bd1dbb2060376ea04df1b592800f5fcfbdd5398bc2d9c0"


def ok(p):
    return [] if isinstance(p, dict) and p.get("ok") else ["invalid"]


def reply(i, n=3, status="ok"):
    return {"index": i, "status": status, "proposals": [{"ok": True, "w": f"{i}-{j}"} for j in range(n)]}


def test_prep_r3_is_unchanged():
    p = REPO / "research_tools/confirmatory_prep/prep_r3.py"
    assert hashlib.sha256(p.read_bytes()).hexdigest() == PREP_R3_SHA256


def test_complete_replies_admit_exactly_as_prep_r3():
    replies = [reply(0), reply(1)]
    got = ad.admit_arm(replies, 2, ok)
    want = prep_r3.admit_replies(replies, 2, ok)
    assert {k: v for k, v in got.items() if k != "admission_path"} == want


def test_missing_request_excludes_the_arm_where_prep_r3_would_proceed():
    replies = [reply(0)]
    assert prep_r3.admit_replies(replies, 2, ok)["requests"]["by_status"].get("missing") == 1
    with pytest.raises(ad.CaseExcluded) as e:
        ad.admit_arm(replies, 2, ok)
    assert e.value.record["missing_indexes"] == [1] and e.value.record["excluded_for_every_arm"]


def test_one_arms_missing_request_excludes_the_case_for_every_arm():
    out = ad.admit_case({"RA-MD": [reply(0), reply(1)], "RA-MDS": [reply(1)]}, 2, ok)
    assert out["excluded_for_every_arm"] and out["missing_indexes"] == {"RA-MDS": [0]}
    assert "arms" not in out


def test_unparseable_and_failed_replies_are_failed_requests_not_exclusions():
    replies = [reply(0), {"index": 1, "status": "unparseable_reply", "proposals": None}]
    out = ad.admit_case({"RA-MD": replies}, 2, ok)
    assert not out["excluded_for_every_arm"]
    assert out["arms"]["RA-MD"]["requests"]["failed"] == 1
