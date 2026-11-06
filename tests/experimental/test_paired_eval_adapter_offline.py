"""EXPERIMENTAL, offline: the muse_contrib → paired_eval adapter's mappings."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "research_tools"))
import paired_eval_from_muse_contrib as ad  # noqa: E402

BASE = {"case": "c", "arm": "B3", "replicate": 1, "run_id": "r", "schedules_used": 9, "detected": True,
        "first_detection_schedule": 1, "wall_seconds": 2.0, "invalid_proposals": 0, "refused": 8,
        "fixed_version_violation": False, "tokens_in": 10, "tokens_out": 20, "est_usd": 0.01, "known_defect": True}


def test_replicates_become_separate_arms_and_refused_counts_are_kept():
    out = ad.convert(BASE)
    assert out["arm"] == "B3.r1" and out["refused_units"] == 8
    assert out["refused"] is False  # a corroborated detection despite refused units
    assert "case_kind" not in out


def test_refused_is_true_only_without_a_detection():
    out = ad.convert(dict(BASE, detected=False, first_detection_schedule=None))
    assert out["refused"] is True
    assert ad.convert(dict(BASE, detected=False, first_detection_schedule=None, refused=0))["refused"] is False


def test_no_known_defect_and_refusals():
    assert ad.convert(dict(BASE, known_defect=False))["case_kind"] == "no_known_defect"
    with pytest.raises(SystemExit, match="lacks"):
        ad.convert({k: v for k, v in BASE.items() if k != "replicate"})
    with pytest.raises(SystemExit, match="non-negative int"):
        ad.convert(dict(BASE, refused=True))
