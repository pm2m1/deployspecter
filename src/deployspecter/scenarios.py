"""Benchmark scenario loading. ``base/`` files are overlaid by the variant directory."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .models import Requirement, TestProposal
from .source_model import SourceModel, extract_source_model

VARIANTS = ("buggy", "corrected")
_NOT_IN_BUNDLE = {"hand_spec.json"}


@dataclass
class Case:
    scenario_id: str
    variant: str
    title: str
    split: str
    description: str
    files: dict[str, str]
    requirements: list[Requirement]
    hand_spec: TestProposal | None

    @property
    def case_id(self) -> str:
        return f"{self.scenario_id}/{self.variant}"

    @property
    def has_bug(self) -> bool:
        return self.variant == "buggy"

    @property
    def setup_sql(self) -> str:
        return "\n".join(self.files[n] for n in ("schema.sql", "migration.sql") if n in self.files)

    def source_model(self) -> SourceModel:
        return extract_source_model(self.files)


def _read_dir(path: Path) -> dict[str, str]:
    if not path.is_dir():
        return {}
    return {
        f.name: f.read_text()
        for f in sorted(path.iterdir())
        if f.is_file() and f.suffix in {".py", ".sql"} and f.name not in _NOT_IN_BUNDLE
    }


def load_case(scenarios_dir: Path, scenario_id: str, variant: str) -> Case:
    root = scenarios_dir / scenario_id
    meta = json.loads((root / "scenario.json").read_text())
    hand_path = root / variant / "hand_spec.json"
    return Case(
        scenario_id=scenario_id,
        variant=variant,
        title=meta["title"],
        split=meta["split"],
        description=meta["description"],
        files=_read_dir(root / "base") | _read_dir(root / variant),
        requirements=[Requirement.model_validate(r) for r in meta["requirements"]],
        hand_spec=TestProposal.model_validate_json(hand_path.read_text()) if hand_path.exists() else None,
    )


def list_scenarios(scenarios_dir: Path) -> list[str]:
    return sorted(p.parent.name for p in scenarios_dir.glob("*/scenario.json"))


def load_all_cases(scenarios_dir: Path, split: str | None = None) -> list[Case]:
    cases = [load_case(scenarios_dir, s, v) for s in list_scenarios(scenarios_dir) for v in VARIANTS]
    return [c for c in cases if split is None or c.split == split]
