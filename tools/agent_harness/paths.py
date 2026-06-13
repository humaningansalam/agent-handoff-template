from __future__ import annotations

from pathlib import Path

ARTIFACT_ROOT = Path("ops/agent-harness")
EVIDENCE_ROOT = ARTIFACT_ROOT / "evidence"
VIEW_ROOT = ARTIFACT_ROOT / "views"
RUN_ARCHIVE_ROOT = ARTIFACT_ROOT / "runs"
LATEST_TRACE = VIEW_ROOT / "trace.md"
EVENTS_JSONL = ARTIFACT_ROOT / "latest-events.jsonl"
STATE_JSON = ARTIFACT_ROOT / "current-run-state.json"
CANDIDATE_STATE_JSON = ARTIFACT_ROOT / "latest-candidate-state.json"
PLAN_METADATA_JSON = ARTIFACT_ROOT / "latest-plan-metadata.json"
PLAN_REVIEW_METADATA_JSON = ARTIFACT_ROOT / "latest-plan-review-metadata.json"
EXECUTION_REVIEW_METADATA_JSON = ARTIFACT_ROOT / "latest-execution-review-metadata.json"
ARTIFACT_INDEX_JSON = ARTIFACT_ROOT / "latest-artifact-index.json"

LATEST_ARTIFACTS: dict[str, str] = {
    "cartography": "evidence/cartography.json",
    "plan": "evidence/plan.json",
    "plan-review": "evidence/plan-review.json",
    "execution": "evidence/execution.json",
    "execution-review": "evidence/execution-review.json",
    "skeptic-review": "evidence/skeptic-review.json",
}

PRE_CANDIDATE_ARTIFACT_PATHS: tuple[str, ...] = (
    str(ARTIFACT_ROOT / LATEST_ARTIFACTS["cartography"]),
)
CANDIDATE_ARTIFACT_PATHS: tuple[str, ...] = tuple(
    str(ARTIFACT_ROOT / name)
    for kind, name in LATEST_ARTIFACTS.items()
    if kind != "cartography"
)
REQUIRED_EVIDENCE_ARTIFACT_PATHS: tuple[str, ...] = tuple(
    str(ARTIFACT_ROOT / name)
    for kind, name in LATEST_ARTIFACTS.items()
    if kind != "skeptic-review"
)
EVIDENCE_ARTIFACT_PATHS: tuple[str, ...] = (*PRE_CANDIDATE_ARTIFACT_PATHS, *CANDIDATE_ARTIFACT_PATHS)
TRACE_ARTIFACT_PATHS: tuple[str, ...] = (str(STATE_JSON), str(LATEST_TRACE), *EVIDENCE_ARTIFACT_PATHS)
LATEST_METADATA_PATHS: tuple[str, ...] = (
    str(CANDIDATE_STATE_JSON),
    str(PLAN_METADATA_JSON),
    str(PLAN_REVIEW_METADATA_JSON),
    str(EXECUTION_REVIEW_METADATA_JSON),
    str(ARTIFACT_INDEX_JSON),
)
