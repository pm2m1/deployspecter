"""PROPOSED evidence record, shared by Track A and Track B. Not adopted, and not yet depended on by either track.

Two rules are enforced by the model itself:

* Arm attribution comes from the harness that issued the unit. ``self_reported_provenance`` is carried as data and
  can never set ``arm``.
* A record's ``context`` fixes what it may be used for. Only ``heldout_evaluation`` records can enter a primary
  analysis, and ``primary_analysis_records`` refuses any other.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

RECORD_VERSION = "evidence-record/0-proposed"
HEX64 = r"^[0-9a-f]{64}$"
Arm = Literal["A", "B", "C", "C_prime", "D", "E", "F", "G", "R"]
Context = Literal["synthetic_demonstration", "development_calibration", "heldout_evaluation"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class Identities(_Strict):
    scenario: str
    application: str
    workload_schema_version: Literal[2]
    spec_digest: str = Field(pattern=HEX64)
    plan_identity: str | None = Field(default=None, pattern=r"^vplan1:[0-9a-f]{64}$")
    registry_digest: str = Field(pattern=HEX64)
    contracts_digest: str = Field(pattern=HEX64)
    context_digest: str | None = Field(default=None, pattern=HEX64)
    snapshot_digest: str | None = Field(default=None, pattern=HEX64)
    snapshot_source: Literal["supervised_export", "dry_run_fixture", "none"]


class Attribution(_Strict):
    arm: Arm | None = None
    replicate: int | None = Field(default=None, ge=1, le=3)
    attempt: int | None = Field(default=None, ge=1)
    defect_id: str | None = None
    source: Literal["harness"] = "harness"  # never the document's own provenance
    response_id: str | None = Field(default=None, pattern=HEX64)
    response_use: Literal["primary", "exploratory_reuse"] | None = None


class StageOutcome(_Strict):
    stage: Literal["validation", "planning", "literal_translation", "catalog_verification", "bridge",
                   "construction", "verification", "execution", "replay", "oracle"]
    outcome: str
    code: str | None = None
    counters: dict[str, int] = Field(default_factory=dict)  # deterministic operation counts only
    wall_seconds: float | None = None  # descriptive, never decisive


class Safety(_Strict):
    timeout: str | None = None  # which safety ceiling, if any, was reached
    infrastructure_failure: str | None = None
    retried: Literal[False] = False  # no automatic retry or replacement, as proposed


class EvidenceRecord(_Strict):
    record_version: Literal["evidence-record/0-proposed"] = RECORD_VERSION
    context: Context
    identities: Identities
    attribution: Attribution
    method: Literal["deterministic_planner", "literal_translation", "none"]
    stages: tuple[StageOutcome, ...]
    independent_verification: Literal["VERIFIED", "FAILED", "NOT_RUN"]
    oracle: Literal["violation", "no_violation", "not_run"]  # kept apart from construction verification
    requirement_ids: tuple[str, ...] = ()
    safety: Safety = Field(default_factory=Safety)
    hashes: dict[str, str]  # source and configuration files, by repository path
    self_reported_provenance: dict[str, Any] | None = None  # data only; decides nothing

    @model_validator(mode="after")
    def _rules(self) -> EvidenceRecord:
        if self.context == "heldout_evaluation":
            a = self.attribution
            if a.arm is None or a.replicate is None or a.attempt is None or a.defect_id is None:
                raise ValueError("a held-out record needs harness-recorded arm, replicate, attempt and defect")
            if self.identities.snapshot_source != "supervised_export":
                raise ValueError("a held-out record needs a supervised catalog export")
        else:
            if self.attribution.arm is not None:
                raise ValueError("only held-out evaluation records carry an experimental arm")
        if self.oracle != "not_run" and self.independent_verification != "VERIFIED":
            raise ValueError("an oracle result needs a verified starting state")
        for path, digest in self.hashes.items():
            if not path or len(digest) != 64:
                raise ValueError(f"hash for {path!r} isn't a SHA-256 hex digest")
        return self


def primary_analysis_records(records: list[EvidenceRecord]) -> list[EvidenceRecord]:
    """Refuses to mix synthetic or development records into a held-out primary analysis."""
    other = [r for r in records if r.context != "heldout_evaluation"]
    if other:
        raise ValueError(f"{len(other)} non-held-out record(s) can't enter a primary analysis")
    return records
