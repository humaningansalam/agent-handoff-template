from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from fnmatch import fnmatch
from typing import Any, ClassVar, Mapping, Sequence

from tools.agent_harness import paths as harness_paths
from tools.agent_harness.pass_gate import calculate_pass_eligibility


class DecisionStatus(str, Enum):
    PASS = "pass"
    CONTINUE_TOPIC = "continue-topic"
    AWAITING_HUMAN_APPROVAL = "awaiting-human-approval"
    RETRY_PLAN = "retry-plan"
    RETRY_IMPLEMENTATION = "retry-implementation"
    RETRY_EVALUATION = "retry-evaluation"
    STOP = "stop"
    NEEDS_HUMAN_DECISION = "needs-human-decision"
    FAIL = "fail"


class ArtifactWriteAction(str, Enum):
    REFRESH = "refresh"
    RETRY_PLAN = "retry-plan"
    NEEDS_HUMAN_DECISION = "needs-human-decision"


class FindingTarget(str, Enum):
    APPROVAL = "approval"
    PLAN = "plan"
    IMPLEMENTATION = "implementation"
    EVALUATION = "evaluation"
    HUMAN_DECISION = "human-decision"
    FAIL = "fail"
    STOP = "stop"


class FailureModeSeverity(str, Enum):
    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"


TARGET_TO_DECISION: dict[FindingTarget, DecisionStatus] = {
    FindingTarget.APPROVAL: DecisionStatus.AWAITING_HUMAN_APPROVAL,
    FindingTarget.PLAN: DecisionStatus.RETRY_PLAN,
    FindingTarget.IMPLEMENTATION: DecisionStatus.RETRY_IMPLEMENTATION,
    FindingTarget.EVALUATION: DecisionStatus.RETRY_EVALUATION,
    FindingTarget.HUMAN_DECISION: DecisionStatus.NEEDS_HUMAN_DECISION,
    FindingTarget.FAIL: DecisionStatus.FAIL,
    FindingTarget.STOP: DecisionStatus.STOP,
}

DECISION_TO_TARGET: dict[DecisionStatus, FindingTarget] = {
    DecisionStatus.AWAITING_HUMAN_APPROVAL: FindingTarget.APPROVAL,
    DecisionStatus.RETRY_PLAN: FindingTarget.PLAN,
    DecisionStatus.RETRY_IMPLEMENTATION: FindingTarget.IMPLEMENTATION,
    DecisionStatus.RETRY_EVALUATION: FindingTarget.EVALUATION,
    DecisionStatus.NEEDS_HUMAN_DECISION: FindingTarget.HUMAN_DECISION,
    DecisionStatus.FAIL: FindingTarget.FAIL,
    DecisionStatus.STOP: FindingTarget.STOP,
}

DECISION_PRECEDENCE: tuple[DecisionStatus, ...] = (
    DecisionStatus.FAIL,
    DecisionStatus.NEEDS_HUMAN_DECISION,
    DecisionStatus.AWAITING_HUMAN_APPROVAL,
    DecisionStatus.STOP,
    DecisionStatus.CONTINUE_TOPIC,
    DecisionStatus.RETRY_PLAN,
    DecisionStatus.RETRY_IMPLEMENTATION,
    DecisionStatus.RETRY_EVALUATION,
)

class Phase(str, Enum):
    INTAKE = "intake"
    CARTOGRAPHED = "cartographed"
    DRAFT_PLANNED = "draft_planned"
    PLAN_REVIEWED = "plan_reviewed"
    AWAITING_HUMAN_APPROVAL = "awaiting_human_approval"
    APPROVED_FROZEN = "approved_frozen"
    IMPLEMENTED = "implemented"
    EVALUATED = "evaluated"
    SKEPTIC_REVIEWED = "skeptic_reviewed"
    DECIDED = "decided"


INTERNAL_RETRY_PHASE: dict[DecisionStatus, Phase] = {
    DecisionStatus.RETRY_PLAN: Phase.PLAN_REVIEWED,
    DecisionStatus.RETRY_IMPLEMENTATION: Phase.APPROVED_FROZEN,
    DecisionStatus.RETRY_EVALUATION: Phase.IMPLEMENTED,
}

INTERNAL_CONTINUATION_PHASE: dict[DecisionStatus, Phase] = {
    **INTERNAL_RETRY_PHASE,
    DecisionStatus.CONTINUE_TOPIC: Phase.CARTOGRAPHED,
}


class ApprovalRequired(RuntimeError):
    """Raised when implementation is attempted before explicit approval."""


class InvalidTransition(RuntimeError):
    """Raised when a harness event is recorded out of order."""


@dataclass(frozen=True)
class ArtifactWriteDecision:
    action: ArtifactWriteAction
    user_visible: bool = False
    blockers: tuple[str, ...] = ()


@dataclass(frozen=True)
class Decision:
    status: DecisionStatus
    blockers: tuple[str, ...] = ()
    user_visible: bool = True
    active_candidate_id: str = ""
    next_candidate_id: str = ""
    topic_complete: bool = True


@dataclass(frozen=True)
class Finding:
    target: FindingTarget
    message: str
    blocking: bool = True


@dataclass(frozen=True)
class PlanReviewArtifactEvidence:
    path: str

    @classmethod
    def from_path(cls, path: str) -> PlanReviewArtifactEvidence:
        expected = "ops/agent-harness/evidence/plan-review.json"
        normalized = path.strip()
        if normalized != expected:
            raise ValueError(f"plan review artifact evidence path must be {expected}")
        return cls(path=normalized)


@dataclass(frozen=True)
class ArtifactRecord:
    path: str
    workflow_id: str
    candidate_id: str
    phase: Phase
    revision: int
    content_sha256: str = ""


@dataclass(frozen=True)
class TraceEvent:
    workflow_id: str
    candidate_id: str
    phase: Phase
    event: str
    worker: str = ""
    artifact_path: str = ""
    retry_target: str = ""
    approval_gate: bool = False
    user_decision: str = ""
    result: str = ""
    final_decision: str = ""

    def to_index_row(self) -> dict[str, str | bool]:
        row: dict[str, str | bool] = {
            "workflow_id": self.workflow_id,
            "candidate_id": self.candidate_id,
            "phase": self.phase.value,
            "event": self.event,
        }
        optional = {
            "worker": self.worker,
            "artifact_path": self.artifact_path,
            "retry_target": self.retry_target,
            "approval_gate": self.approval_gate,
            "user_decision": self.user_decision,
            "result": self.result,
            "final_decision": self.final_decision,
        }
        for key, value in optional.items():
            if value:
                row[key] = value
        return row


@dataclass(frozen=True)
class WorkerEvidence:
    required: bool
    invoked: bool
    evidence: str
    worker: str = ""
    evidence_kind: str = ""
    status: str = ""
    blocking_findings: tuple[str, ...] = ()
    artifact_path: str = ""
    artifact_sha256: str = ""
    schema_version: int = 0
    structured_evidence_valid: bool = False

    def to_row(self) -> dict[str, Any]:
        return {
            "required": self.required,
            "invoked": self.invoked,
            "evidence": self.evidence,
            "worker": self.worker,
            "evidence_kind": self.evidence_kind,
            "status": self.status,
            "blocking_findings": list(self.blocking_findings),
            "artifact_path": self.artifact_path,
            "artifact_sha256": self.artifact_sha256,
            "schema_version": self.schema_version,
            "structured_evidence_valid": self.structured_evidence_valid,
        }

    @classmethod
    def from_row(cls, row: Any) -> WorkerEvidence:
        if isinstance(row, cls):
            return row
        if not isinstance(row, Mapping):
            return cls(required=True, invoked=bool(row), evidence="")
        return cls(
            required=bool(row.get("required", True)),
            invoked=bool(row.get("invoked", False)),
            evidence=str(row.get("evidence", "")).strip(),
            worker=str(row.get("worker", "")).strip(),
            evidence_kind=str(row.get("evidence_kind", "")).strip(),
            status=str(row.get("status", "")).strip(),
            blocking_findings=tuple(str(item).strip() for item in row.get("blocking_findings", []) if str(item).strip())
            if isinstance(row.get("blocking_findings", []), list)
            else (),
            artifact_path=str(row.get("artifact_path", "")).strip(),
            artifact_sha256=str(row.get("artifact_sha256", "")).strip(),
            schema_version=int(row.get("schema_version") or 0),
            structured_evidence_valid=bool(row.get("structured_evidence_valid", False)),
        )

    def satisfied(self, *, mandatory: bool = False) -> bool:
        if not self.required and not mandatory:
            return True
        return (
            self.required
            and self.invoked
            and bool(self.worker)
            and bool(self.evidence_kind)
            and self.status == "passed"
            and not self.blocking_findings
            and ((bool(self.artifact_path) and bool(self.artifact_sha256)) or self.schema_version > 0)
            and bool(self.evidence.strip())
            and self.structured_evidence_valid
        )


@dataclass(frozen=True)
class ApprovalFreeze:
    candidate_id: str
    plan_path: str
    plan_revision: int
    plan_sha256: str = ""
    affected_surfaces: tuple[str, ...] = ()
    acceptance_criteria_ids: tuple[str, ...] = ()

    def to_checkpoint(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "plan_path": self.plan_path,
            "plan_revision": self.plan_revision,
            "plan_sha256": self.plan_sha256,
            "affected_surfaces_sha256": self.affected_surfaces_sha256(),
            "acceptance_criteria_sha256": self.acceptance_criteria_sha256(),
            "approval_hash": self.approval_hash(),
            "affected_surfaces": list(self.affected_surfaces),
            "acceptance_criteria_ids": list(self.acceptance_criteria_ids),
        }

    def affected_surfaces_sha256(self) -> str:
        return self._sequence_hash(self.affected_surfaces)

    def acceptance_criteria_sha256(self) -> str:
        return self._sequence_hash(self.acceptance_criteria_ids)

    def approval_hash(self) -> str:
        payload = {
            "candidate_id": self.candidate_id,
            "plan_path": self.plan_path,
            "plan_revision": self.plan_revision,
            "plan_sha256": self.plan_sha256,
            "affected_surfaces_sha256": self.affected_surfaces_sha256(),
            "acceptance_criteria_sha256": self.acceptance_criteria_sha256(),
            "affected_surfaces": list(self.affected_surfaces),
            "acceptance_criteria_ids": list(self.acceptance_criteria_ids),
        }
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _sequence_hash(values: Sequence[str]) -> str:
        encoded = json.dumps(list(values), ensure_ascii=False, sort_keys=False, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


def finding_from_decision(status: DecisionStatus, message: str) -> Finding:
    if status == DecisionStatus.PASS:
        raise ValueError("pass is not a blocking finding")

    return Finding(
        target=DECISION_TO_TARGET[status],
        message=message,
        blocking=True,
    )


@dataclass
class MaintenanceHarness:
    phase: Phase = Phase.INTAKE
    workflow_id: str = ""
    active_candidate_id: str = ""
    queued_candidate_ids: tuple[str, ...] = ()
    terminal_candidate: bool = True
    queue_policy: str = "human-decision"
    approval_evidence: str = ""
    approval_freeze: ApprovalFreeze | None = None
    changed_files: tuple[str, ...] = ()
    pass_candidate: bool = False
    active_candidate: str | None = None
    candidate_records: set[str] = field(default_factory=set)
    worker_matrix: dict[str, WorkerEvidence] = field(default_factory=dict)
    findings: tuple[Finding, ...] = ()
    artifacts: dict[str, ArtifactRecord] = field(default_factory=dict)
    trace_events: tuple[TraceEvent, ...] = ()

    STATE_ARTIFACT_PATH = str(harness_paths.STATE_JSON)
    TRACE_INDEX_PATH = str(harness_paths.LATEST_TRACE)
    RUN_ARCHIVE_ROOT = str(harness_paths.RUN_ARCHIVE_ROOT)
    PRE_CANDIDATE_ARTIFACT_PATHS: tuple[str, ...] = harness_paths.PRE_CANDIDATE_ARTIFACT_PATHS
    CANDIDATE_ARTIFACT_PATHS: tuple[str, ...] = harness_paths.CANDIDATE_ARTIFACT_PATHS
    REQUIRED_EVIDENCE_ARTIFACT_PATHS: tuple[str, ...] = harness_paths.REQUIRED_EVIDENCE_ARTIFACT_PATHS
    EVIDENCE_ARTIFACT_PATHS: tuple[str, ...] = harness_paths.EVIDENCE_ARTIFACT_PATHS
    TRACE_ARTIFACT_PATHS: tuple[str, ...] = harness_paths.TRACE_ARTIFACT_PATHS
    STATE_FORBIDDEN_BODY_FIELDS: tuple[str, ...] = (
        "plan_body",
        "critic_findings",
        "implementation_summary_body",
        "evaluation_matrix",
        "failure_mode_replay_matrix",
        "skeptic_review_body",
        "transcript_body",
        "tool_event_body",
        "worker_output_body",
        "temp_path",
        "sidecar_path",
        "credential",
    )
    STATE_FORBIDDEN_TOP_LEVEL_ALIASES: tuple[str, ...] = (
        "approval",
        "workers",
        "retry_target",
    )
    STATE_REQUIRED_TOP_LEVEL_FIELDS: tuple[str, ...] = (
        "schema_version",
        "workflow_id",
        "phase",
        "active_candidate_id",
        "queued_candidate_ids",
        "terminal_candidate",
        "approval_gate",
        "retry",
        "artifacts",
        "latest_event",
        "failure_mode_ledger",
        "pass_eligibility",
        "worker_status",
    )

    @classmethod
    def validate_state_checkpoint(cls, checkpoint: Mapping[str, Any]) -> None:
        aliases = sorted(set(checkpoint).intersection(cls.STATE_FORBIDDEN_TOP_LEVEL_ALIASES))
        if aliases:
            raise ValueError(f"state checkpoint uses invalid top-level alias fields: {', '.join(aliases)}")
        if isinstance(checkpoint.get("artifacts"), Mapping):
            raise ValueError("state checkpoint artifacts must be a compact list, not a dict")
        if cls._contains_forbidden_state_key(checkpoint):
            raise ValueError("state checkpoint must reference rendered artifacts, not embed evidence bodies")
        missing = [field for field in cls.STATE_REQUIRED_TOP_LEVEL_FIELDS if field not in checkpoint]
        if missing:
            raise ValueError(f"state checkpoint missing required top-level fields: {', '.join(missing)}")
        cls._validate_checkpoint_artifact_lineage(checkpoint)

    @classmethod
    def _validate_checkpoint_artifact_lineage(cls, checkpoint: Mapping[str, Any]) -> None:
        workflow_id = str(checkpoint.get("workflow_id") or "").strip()
        artifacts = checkpoint.get("artifacts")
        if not isinstance(artifacts, list):
            raise ValueError("state checkpoint artifacts must be a compact list")
        for artifact in artifacts:
            if not isinstance(artifact, Mapping):
                raise ValueError("state checkpoint artifact entries must be objects")
            path = str(artifact.get("path") or "").strip()
            if path not in cls.TRACE_ARTIFACT_PATHS:
                raise ValueError(f"state checkpoint artifact has unknown path: {path or '<empty>'}")
            canonical_path = str(artifact.get("canonical_path") or "").strip()
            if not canonical_path:
                raise ValueError(f"state checkpoint artifact missing canonical_path: {path}")
            if path in cls.EVIDENCE_ARTIFACT_PATHS:
                cls._validate_run_scoped_canonical_path(path, canonical_path, workflow_id)

    @classmethod
    def _validate_run_scoped_canonical_path(cls, path: str, canonical_path: str, workflow_id: str) -> None:
        if not workflow_id:
            raise ValueError("state checkpoint workflow_id is required for artifact lineage")
        prefix = f"{cls.RUN_ARCHIVE_ROOT}/{workflow_id}/candidates/"
        if not canonical_path.startswith(prefix):
            raise ValueError(f"state checkpoint artifact canonical_path is not run-scoped: {path}")
        artifact_name = path.rsplit("/", 1)[-1]
        if not canonical_path.rsplit("/", 1)[-1].endswith(f"-{artifact_name}"):
            raise ValueError(f"state checkpoint artifact canonical_path does not match artifact name: {path}")

    @classmethod
    def _contains_forbidden_state_key(cls, value: Any) -> bool:
        if isinstance(value, Mapping):
            return any(
                key in cls.STATE_FORBIDDEN_BODY_FIELDS or cls._contains_forbidden_state_key(nested)
                for key, nested in value.items()
            )
        if isinstance(value, list):
            return any(cls._contains_forbidden_state_key(item) for item in value)
        return False

    MANDATORY_WORKERS: tuple[str, ...] = (
        "maintenance-cartographer",
        "maintenance-planner",
        "maintenance-plan-critic",
        "maintenance-implementer",
        "maintenance-evaluator",
        "maintenance-skeptic",
    )
    REQUIRED_APPROVAL_ARTIFACT_PATHS: tuple[str, ...] = (
        STATE_ARTIFACT_PATH,
        "ops/agent-harness/evidence/plan.json",
        "ops/agent-harness/evidence/plan-review.json",
    )
    REQUIRED_PASS_ARTIFACT_PATHS: tuple[str, ...] = (STATE_ARTIFACT_PATH, *REQUIRED_EVIDENCE_ARTIFACT_PATHS)
    FULL_GATED_SURFACE_PATTERNS: ClassVar[tuple[str, ...]] = (
        ".claude/hooks/**",
        ".claude/settings.maintenance.json",
        "tools/hooks/**",
        "tools/agent_harness/**",
        "tests/maintenance/**",
        "tests/research_ops/test_hook_permission_contracts.py",
        "tests/maintenance/test_runner_contract.py",
    )

    @classmethod
    def mandatory_workers_for_profile(cls, profile_path: str) -> tuple[str, ...]:
        if profile_path == "TINY_DOC":
            return ("maintenance-planner", "maintenance-implementer")
        if profile_path == "STANDARD":
            return (
                "maintenance-cartographer",
                "maintenance-planner",
                "maintenance-plan-critic",
                "maintenance-implementer",
                "maintenance-evaluator",
            )
        return cls.MANDATORY_WORKERS

    @classmethod
    def required_pass_artifact_paths_for_profile(cls, profile_path: str) -> tuple[str, ...]:
        artifact = harness_paths.ARTIFACT_ROOT / "evidence"
        if profile_path == "TINY_DOC":
            return (cls.STATE_ARTIFACT_PATH, str(artifact / "plan.json"), str(artifact / "execution.json"), str(artifact / "execution-review.json"))
        if profile_path == "STANDARD":
            return (
                cls.STATE_ARTIFACT_PATH,
                str(artifact / "cartography.json"),
                str(artifact / "plan.json"),
                str(artifact / "plan-review.json"),
                str(artifact / "execution.json"),
                str(artifact / "execution-review.json"),
            )
        return (cls.STATE_ARTIFACT_PATH, *cls.EVIDENCE_ARTIFACT_PATHS)
    LIGHTWEIGHT_SURFACE_PATTERNS: ClassVar[tuple[str, ...]] = (
        "docs/**",
        ".claude/agents/**",
        ".claude/skills/**",
        "templates/**",
        "README.md",
        "CLAUDE.md",
    )

    failure_mode_ledger_required: bool = False
    failure_mode_ledger_severity: FailureModeSeverity = FailureModeSeverity.P1
    failure_mode_ledger_mapped: bool = True
    failure_mode_direct_evidence: bool = True
    pass_eligibility_confirmed: bool = False
    pass_eligibility_blocked_by: tuple[str, ...] = ("mandatory worker evidence pending",)

    def record_workflow(self, workflow_id: str) -> None:
        normalized = workflow_id.strip()
        if not normalized:
            raise ValueError("workflow id is required")
        self.workflow_id = normalized
        self.record_trace_event("workflow-start")

    def canonical_artifact_path(self, latest_path: str, *, candidate_id: str | None = None, revision: int | None = None) -> str:
        normalized = self._normalize_trace_artifact_path(latest_path)
        workflow = self.workflow_id.strip()
        if not workflow:
            raise ValueError("workflow id is required before canonical artifact path")
        candidate = (candidate_id or self.active_candidate_id or "run").strip()
        if not candidate:
            raise ValueError("candidate id is required before canonical artifact path")
        artifact_name = normalized.rsplit("/", 1)[-1]
        revision_value = revision or (self.artifacts.get(normalized).revision if normalized in self.artifacts else 1)
        return f"{self.RUN_ARCHIVE_ROOT}/{workflow}/candidates/{candidate}/r{revision_value:03d}-{artifact_name}"

    def record_trace_event(
        self,
        event: str,
        *,
        worker: str = "",
        artifact_path: str = "",
        retry_target: FindingTarget | DecisionStatus | str = "",
        approval_gate: bool = False,
        user_decision: str = "",
        result: str | DecisionStatus = "",
        final_decision: str | DecisionStatus = "",
    ) -> TraceEvent:
        normalized_event = event.strip()
        if not normalized_event:
            raise ValueError("trace event is required")
        trace_event = TraceEvent(
            workflow_id=self.workflow_id,
            candidate_id=self.active_candidate_id,
            phase=self.phase,
            event=normalized_event,
            worker=worker.strip(),
            artifact_path=artifact_path.strip(),
            retry_target=self._enum_value(retry_target),
            approval_gate=approval_gate,
            user_decision=user_decision.strip(),
            result=self._enum_value(result),
            final_decision=self._enum_value(final_decision),
        )
        self.trace_events = (*self.trace_events, trace_event)
        return trace_event

    def trace_index(self) -> dict[str, Any]:
        return {
            "workflow_id": self.workflow_id,
            "topic_focus": self.active_candidate or "",
            "active_candidate_id": self.active_candidate_id,
            "queued_candidate_ids": list(self.queued_candidate_ids),
            "queue_policy": self.queue_policy,
            "phase": self.phase.value,
            "phase_sequence": [event.to_index_row() for event in self.trace_events],
            "artifacts": [self._artifact_index_row(record) for record in self.artifacts.values()],
        }

    def _artifact_index_row(self, record: ArtifactRecord) -> dict[str, str | int]:
        canonical_path = self.canonical_artifact_path(record.path, candidate_id=record.candidate_id or "run", revision=record.revision)
        row: dict[str, str | int] = {
            "path": record.path,
            "canonical_path": canonical_path,
            "workflow_id": record.workflow_id,
            "candidate_id": record.candidate_id,
            "phase": record.phase.value,
            "revision": record.revision,
        }
        if record.content_sha256:
            row["content_sha256"] = record.content_sha256
        return row

    def state_checkpoint(self) -> dict[str, Any]:
        """Return the compact JSON shape expected in current-run-state.json."""
        latest_event = self.trace_events[-1].to_index_row() if self.trace_events else {}
        checkpoint: dict[str, Any] = {
            "schema_version": 1,
            "workflow_id": self.workflow_id,
            "phase": self.phase.value,
            "active_candidate_id": self.active_candidate_id,
            "queued_candidate_ids": list(self.queued_candidate_ids),
            "terminal_candidate": self.terminal_candidate,
            "queue_policy": self.queue_policy,
            "approval_gate": {
                "status": self._approval_gate_status(),
                "requires_human_approval": self.phase == Phase.AWAITING_HUMAN_APPROVAL,
                "approval_evidence_recorded": bool(self.approval_evidence.strip()),
                "freeze": self.approval_freeze.to_checkpoint() if self.approval_freeze else {},
            },
            "retry": self._retry_checkpoint(),
            "artifacts": self.trace_index()["artifacts"],
            "latest_event": latest_event,
            "failure_mode_ledger": {
                "required": self.failure_mode_ledger_required,
                "severity": self.failure_mode_ledger_severity.value,
                "full_replay_required": self._failure_mode_full_replay_required(),
                "mapped": self.failure_mode_ledger_mapped,
                "direct_evidence": self.failure_mode_direct_evidence,
            },
            "pass_eligibility": {
                "eligible": self.pass_eligibility_confirmed,
                "blocked_by": list(self.pass_eligibility_blocked_by),
                "evaluation_pass_candidate": self.pass_candidate,
                "tests_passed": self._tests_passed(),
                "calculated": self.calculated_pass_eligibility(),
                "workflow_profile": self.workflow_profile(),
            },
            "worker_status": {
                worker: {
                    "required": evidence.required,
                    "invoked": evidence.invoked,
                    "worker": evidence.worker,
                    "evidence_kind": evidence.evidence_kind,
                    "status": evidence.status,
                    "blocking_findings": list(evidence.blocking_findings),
                    "artifact_path": evidence.artifact_path,
                    "artifact_sha256": evidence.artifact_sha256,
                    "schema_version": evidence.schema_version,
                    "structured_evidence_valid": evidence.structured_evidence_valid,
                }
                for worker, evidence in self.worker_matrix.items()
            },
        }
        self.validate_state_checkpoint(checkpoint)
        return checkpoint

    def _approval_gate_status(self) -> str:
        if self.phase == Phase.AWAITING_HUMAN_APPROVAL:
            return DecisionStatus.AWAITING_HUMAN_APPROVAL.value
        if self.approval_freeze is not None:
            return "approved-frozen"
        if self.approval_evidence.strip():
            return "approved"
        return "not-ready"

    def _retry_checkpoint(self) -> dict[str, Any]:
        blockers = tuple(
            finding.message
            for finding in self.findings
            if finding.blocking and finding.message.strip()
        )
        if not blockers:
            return {"target": "", "blockers": []}
        targets = {
            TARGET_TO_DECISION[finding.target]
            for finding in self.findings
            if finding.blocking and finding.message.strip()
        }
        target = ""
        for status in DECISION_PRECEDENCE:
            if status in targets:
                target = status.value
                break
        return {"target": target, "blockers": list(blockers)}

    @staticmethod
    def _enum_value(value: FindingTarget | DecisionStatus | str) -> str:
        if isinstance(value, Enum):
            return str(value.value)
        return str(value).strip()

    def record_artifact_write(
        self,
        path: str,
        *,
        workflow_id: str | None = None,
        candidate_id: str | None = None,
        content_sha256: str = "",
    ) -> ArtifactRecord:
        normalized = self._normalize_trace_artifact_path(path)
        workflow = (workflow_id or self.workflow_id).strip()
        if not workflow:
            raise ValueError("workflow id is required before artifact write")
        candidate = ""
        if normalized in self.CANDIDATE_ARTIFACT_PATHS:
            active_candidate = self.active_candidate_id.strip()
            candidate = (candidate_id or active_candidate).strip()
            if not active_candidate:
                raise ValueError("candidate id is required before candidate artifact write")
            if candidate != active_candidate:
                raise ValueError("candidate artifact must belong to active candidate")
        previous = self.artifacts.get(normalized)
        revision = 1 if previous is None else previous.revision + 1
        record = ArtifactRecord(
            path=normalized,
            workflow_id=workflow,
            candidate_id=candidate,
            phase=self.phase,
            revision=revision,
            content_sha256=content_sha256.strip(),
        )
        self.artifacts[normalized] = record
        self.record_trace_event("artifact-write", artifact_path=normalized)
        return record

    def record_artifact_write_failure(
        self,
        path: str,
        *,
        reason: str = "artifact write failed before persistence",
    ) -> ArtifactWriteDecision:
        normalized = self._normalize_trace_artifact_path(path)
        if not self.workflow_id.strip():
            raise ValueError("workflow id is required before artifact write failure")
        self.record_trace_event(
            "artifact-write-failed",
            artifact_path=normalized,
            retry_target=DecisionStatus.RETRY_PLAN.value,
            result=reason,
        )
        self._append_findings([Finding(FindingTarget.PLAN, reason)])
        return ArtifactWriteDecision(
            action=ArtifactWriteAction.RETRY_PLAN,
            user_visible=False,
            blockers=(reason,),
        )

    def reconcile_artifact_write(
        self,
        path: str,
        *,
        observed_workflow_id: str | None = None,
        observed_candidate_id: str | None = None,
        approval_sensitive_changed: bool = False,
    ) -> ArtifactWriteDecision:
        normalized = self._normalize_trace_artifact_path(path)
        observed_workflow = (observed_workflow_id or self.workflow_id).strip()
        if not observed_workflow:
            raise ValueError("workflow id is required before artifact reconciliation")
        observed_candidate = (observed_candidate_id or self.active_candidate_id).strip()
        current = self.artifacts.get(normalized)
        if normalized in self.CANDIDATE_ARTIFACT_PATHS:
            active_candidate = self.active_candidate_id.strip()
            if not active_candidate:
                raise ValueError("candidate id is required before candidate artifact reconciliation")
            if observed_candidate != active_candidate:
                if current and current.candidate_id:
                    return self._record_artifact_write_decision(
                        ArtifactWriteAction.RETRY_PLAN,
                        (f"artifact belongs to {current.candidate_id}, not active candidate {active_candidate}",),
                    )
                raise ValueError("candidate artifact must belong to active candidate")

        if current and current.workflow_id and observed_workflow and current.workflow_id != observed_workflow:
            return self._record_artifact_write_decision(
                ArtifactWriteAction.RETRY_PLAN,
                (f"artifact belongs to workflow {current.workflow_id}, not {observed_workflow}",),
            )
        if current and current.candidate_id and observed_candidate and current.candidate_id != observed_candidate:
            return self._record_artifact_write_decision(
                ArtifactWriteAction.RETRY_PLAN,
                (f"artifact belongs to {current.candidate_id}, not {observed_candidate}",),
            )
        if approval_sensitive_changed:
            return self._record_artifact_write_decision(
                ArtifactWriteAction.NEEDS_HUMAN_DECISION,
                ("approval-sensitive artifact content changed",),
            )
        return ArtifactWriteDecision(action=ArtifactWriteAction.REFRESH, user_visible=False)

    def record_cartography(self) -> None:
        if self.phase not in {Phase.INTAKE, Phase.CARTOGRAPHED, Phase.DRAFT_PLANNED, Phase.PLAN_REVIEWED}:
            raise InvalidTransition(f"cannot record cartography from {self.phase.value}")
        self.phase = Phase.CARTOGRAPHED

    def record_candidate_queue(
        self,
        active_candidate_id: str,
        queued_candidate_ids: Sequence[str] = (),
        *,
        terminal_candidate: bool | None = None,
        queue_policy: str = "human-decision",
    ) -> None:
        if self.phase not in {Phase.INTAKE, Phase.CARTOGRAPHED, Phase.DRAFT_PLANNED, Phase.PLAN_REVIEWED}:
            raise InvalidTransition(f"cannot record candidate queue from {self.phase.value}")
        active = active_candidate_id.strip()
        if not active:
            raise ValueError("active candidate id is required")
        queued = tuple(candidate.strip() for candidate in queued_candidate_ids if candidate.strip())
        if active in queued:
            raise ValueError("active candidate cannot also be queued")
        normalized_policy = queue_policy.strip() or "human-decision"
        if normalized_policy not in {"human-decision", "auto-continuation"}:
            raise ValueError("queue_policy must be human-decision or auto-continuation")
        self.active_candidate_id = active
        self.queued_candidate_ids = queued
        self.terminal_candidate = len(queued) == 0 if terminal_candidate is None else terminal_candidate
        self.queue_policy = "human-decision" if not queued else normalized_policy

    def record_draft_plan(self) -> None:
        if self.phase not in {Phase.CARTOGRAPHED, Phase.PLAN_REVIEWED}:
            raise InvalidTransition(f"cannot record draft plan from {self.phase.value}")
        if self.phase == Phase.PLAN_REVIEWED:
            self.findings = ()
        self.phase = Phase.DRAFT_PLANNED

    def record_plan_review(
        self,
        ready: bool,
        scope_changed: bool = False,
        blocking_findings: Sequence[str | Finding] = (),
        review_artifact: PlanReviewArtifactEvidence | None = None,
        approval_state_checkpoint: Mapping[str, Any] | None = None,
    ) -> None:
        self._require_phase(Phase.DRAFT_PLANNED, "plan review")
        self._append_findings(blocking_findings, default_target=FindingTarget.PLAN)

        if scope_changed:
            self._append_findings(
                [Finding(FindingTarget.PLAN, "plan scope changed; return to cartography")],
                default_target=FindingTarget.PLAN,
            )
            self.phase = Phase.CARTOGRAPHED
            return

        has_blockers = any(
            finding.blocking and finding.message.strip()
            for finding in self.findings
        )
        if ready and not has_blockers:
            if isinstance(review_artifact, PlanReviewArtifactEvidence):
                state_findings = self._approval_state_checkpoint_findings(approval_state_checkpoint)
                if state_findings:
                    self._append_findings(state_findings, default_target=FindingTarget.PLAN)
                    self.phase = Phase.PLAN_REVIEWED
                    return
                missing_artifacts = self._missing_required_artifacts(self.REQUIRED_APPROVAL_ARTIFACT_PATHS)
                if not missing_artifacts:
                    self.phase = Phase.AWAITING_HUMAN_APPROVAL
                    return
                self._append_findings(
                    [Finding(FindingTarget.PLAN, f"missing approval gate trace artifact: {path}") for path in missing_artifacts],
                    default_target=FindingTarget.PLAN,
                )
                self.phase = Phase.PLAN_REVIEWED
                return
            if review_artifact is not None:
                raise TypeError("review_artifact must be PlanReviewArtifactEvidence")
            self._append_findings(
                [Finding(FindingTarget.PLAN, "plan review artifact evidence is required before human approval")],
                default_target=FindingTarget.PLAN,
            )

        if not ready:
            self._append_findings(
                [Finding(FindingTarget.PLAN, "plan review is not ready to freeze")],
                default_target=FindingTarget.PLAN,
            )
        self.phase = Phase.PLAN_REVIEWED

    def _approval_state_checkpoint_findings(self, checkpoint: Mapping[str, Any] | None) -> tuple[Finding, ...]:
        if checkpoint is None:
            return ()
        try:
            self.validate_state_checkpoint(checkpoint)
        except ValueError as exc:
            return (Finding(FindingTarget.PLAN, f"approval state checkpoint is invalid: {exc}"),)
        state_candidate = str(checkpoint.get("active_candidate_id") or "").strip()
        if state_candidate != self.active_candidate_id:
            return (
                Finding(
                    FindingTarget.PLAN,
                    f"approval state active candidate {state_candidate or '<empty>'} does not match {self.active_candidate_id}",
                ),
            )
        stale_artifacts = tuple(
            artifact
            for artifact in checkpoint.get("artifacts", [])
            if isinstance(artifact, Mapping)
            and str(artifact.get("path") or "") in self.CANDIDATE_ARTIFACT_PATHS
            and str(artifact.get("candidate_id") or "") != self.active_candidate_id
        )
        if stale_artifacts:
            paths = ", ".join(str(artifact.get("path") or "") for artifact in stale_artifacts)
            return (Finding(FindingTarget.PLAN, f"approval state candidate artifact mismatch: {paths}"),)
        return ()

    def record_human_approval(
        self,
        evidence: str,
        *,
        approved_plan_sha256: str = "",
        approved_affected_surfaces: Sequence[str] = (),
        approved_acceptance_criteria_ids: Sequence[str] = (),
    ) -> None:
        self._require_phase(Phase.AWAITING_HUMAN_APPROVAL, "human approval")
        approval_evidence = evidence.strip()
        if not approval_evidence:
            raise ApprovalRequired("human approval requires explicit evidence")
        plan_record = self.artifacts.get("ops/agent-harness/evidence/plan.json")
        if plan_record is None:
            raise ApprovalRequired("human approval requires persisted latest-plan artifact")
        if plan_record.candidate_id != self.active_candidate_id:
            raise ApprovalRequired("human approval requires latest-plan artifact for active candidate")
        affected_surfaces = tuple(surface.strip() for surface in approved_affected_surfaces if surface.strip())
        acceptance_criteria_ids = tuple(criteria.strip() for criteria in approved_acceptance_criteria_ids if criteria.strip())
        if not affected_surfaces:
            raise ApprovalRequired("human approval requires approved affected surfaces")
        if not acceptance_criteria_ids:
            raise ApprovalRequired("human approval requires acceptance criteria ids")
        self.approval_evidence = approval_evidence
        self.approval_freeze = ApprovalFreeze(
            candidate_id=self.active_candidate_id,
            plan_path=plan_record.path,
            plan_revision=plan_record.revision,
            plan_sha256=approved_plan_sha256.strip() or plan_record.content_sha256,
            affected_surfaces=affected_surfaces,
            acceptance_criteria_ids=acceptance_criteria_ids,
        )
        self.phase = Phase.APPROVED_FROZEN

    def record_implementation(self, changed_files: Sequence[str]) -> None:
        if self.phase != Phase.APPROVED_FROZEN:
            if self.phase in {
                Phase.INTAKE,
                Phase.CARTOGRAPHED,
                Phase.DRAFT_PLANNED,
                Phase.PLAN_REVIEWED,
                Phase.AWAITING_HUMAN_APPROVAL,
            }:
                raise ApprovalRequired("implementation requires approved frozen plan")
            raise InvalidTransition(f"cannot record implementation from {self.phase.value}")
        self._require_approval_freeze("implementation")
        normalized_changed_files = tuple(path.strip() for path in changed_files if path.strip())
        self._require_changed_files_within_approved_surfaces(normalized_changed_files)
        self.findings = self._findings_without_target(FindingTarget.IMPLEMENTATION)
        self.pass_candidate = False
        self.worker_matrix = {}
        self.changed_files = normalized_changed_files
        self.phase = Phase.IMPLEMENTED

    def record_evaluation(
        self,
        pass_candidate: bool,
        blocking_findings: Sequence[str | Finding] = (),
    ) -> None:
        self._require_phase(Phase.IMPLEMENTED, "evaluation")
        self._require_approval_freeze("evaluation")
        self.findings = self._findings_without_target(FindingTarget.EVALUATION)
        self.worker_matrix = {}
        self.pass_candidate = pass_candidate
        self._append_findings(blocking_findings, default_target=FindingTarget.EVALUATION)
        self.phase = Phase.EVALUATED

    def record_worker_matrix(self, matrix: Mapping[str, Any]) -> None:
        if self.phase not in {Phase.EVALUATED, Phase.SKEPTIC_REVIEWED}:
            raise InvalidTransition(f"cannot record worker matrix from {self.phase.value}")
        self._require_approval_freeze("worker matrix")
        self.worker_matrix = {worker: WorkerEvidence.from_row(row) for worker, row in matrix.items()}
        self.phase = Phase.SKEPTIC_REVIEWED

    def record_skeptic_review(
        self,
        blocking_findings: Sequence[str | Finding] = (),
        suggested_decision: DecisionStatus | None = None,
    ) -> None:
        if self.phase not in {Phase.EVALUATED, Phase.SKEPTIC_REVIEWED}:
            raise InvalidTransition(f"cannot record skeptic review from {self.phase.value}")
        self._require_approval_freeze("skeptic review")

        default_target = (
            DECISION_TO_TARGET.get(suggested_decision, FindingTarget.EVALUATION)
            if suggested_decision
            else FindingTarget.EVALUATION
        )
        self._append_findings(blocking_findings, default_target=default_target)

        if suggested_decision and suggested_decision != DecisionStatus.PASS:
            self._append_findings(
                [finding_from_decision(suggested_decision, f"skeptic suggested {suggested_decision.value}")]
            )

        self.phase = Phase.SKEPTIC_REVIEWED

    def record_worker_failure(self, worker: str, message: str | None = None) -> None:
        target = {
            "maintenance-cartographer": FindingTarget.PLAN,
            "maintenance-planner": FindingTarget.PLAN,
            "maintenance-plan-critic": FindingTarget.PLAN,
            "maintenance-implementer": FindingTarget.IMPLEMENTATION,
            "maintenance-evaluator": FindingTarget.EVALUATION,
            "maintenance-skeptic": FindingTarget.EVALUATION,
        }.get(worker, FindingTarget.EVALUATION)

        if worker == "maintenance-cartographer":
            allowed_phases = {Phase.INTAKE, Phase.CARTOGRAPHED}
        elif worker == "maintenance-planner":
            allowed_phases = {Phase.CARTOGRAPHED, Phase.DRAFT_PLANNED}
        elif worker == "maintenance-plan-critic":
            allowed_phases = {Phase.DRAFT_PLANNED, Phase.PLAN_REVIEWED}
        elif worker == "maintenance-implementer":
            allowed_phases = {Phase.APPROVED_FROZEN, Phase.IMPLEMENTED, Phase.EVALUATED, Phase.SKEPTIC_REVIEWED}
        else:
            allowed_phases = {Phase.IMPLEMENTED, Phase.EVALUATED, Phase.SKEPTIC_REVIEWED}

        if target in {FindingTarget.IMPLEMENTATION, FindingTarget.EVALUATION} and self.phase == Phase.AWAITING_HUMAN_APPROVAL:
            self._require_approval_freeze(f"{worker} failure")
        if self.phase not in allowed_phases:
            raise InvalidTransition(f"cannot record {worker} failure from {self.phase.value}")
        if target in {FindingTarget.IMPLEMENTATION, FindingTarget.EVALUATION}:
            self._require_approval_freeze(f"{worker} failure")

        self._append_findings(
            [Finding(target, message or f"{worker} output was missing or not reviewable")],
            default_target=target,
        )

        if target == FindingTarget.PLAN:
            self.phase = Phase.PLAN_REVIEWED
        elif target == FindingTarget.IMPLEMENTATION:
            self.phase = Phase.SKEPTIC_REVIEWED
        else:
            self.pass_candidate = False
            self.phase = Phase.SKEPTIC_REVIEWED

    def decide(self) -> Decision:
        if self.phase == Phase.AWAITING_HUMAN_APPROVAL:
            return Decision(status=DecisionStatus.AWAITING_HUMAN_APPROVAL)

        if self.phase == Phase.PLAN_REVIEWED:
            decision = self._decision_from_findings(
                self.findings,
                fallback_status=DecisionStatus.RETRY_PLAN,
                fallback_blocker="plan review is not ready for human approval",
            )
            if decision.status in INTERNAL_CONTINUATION_PHASE:
                self.phase = INTERNAL_CONTINUATION_PHASE[decision.status]
            else:
                self.phase = Phase.DECIDED
            return decision

        self._require_phase(Phase.SKEPTIC_REVIEWED, "decision")

        decision = self._decision_from_findings(
            self._decision_findings(),
            fallback_status=DecisionStatus.RETRY_EVALUATION,
        )
        if decision.status == DecisionStatus.PASS:
            decision = self._pass_or_continue_topic()
        if decision.status in INTERNAL_CONTINUATION_PHASE:
            self.phase = INTERNAL_CONTINUATION_PHASE[decision.status]
        elif decision.status == DecisionStatus.AWAITING_HUMAN_APPROVAL:
            self.phase = Phase.AWAITING_HUMAN_APPROVAL
        else:
            self.phase = Phase.DECIDED
        return decision

    def _pass_or_continue_topic(self) -> Decision:
        if self.queued_candidate_ids and self.queue_policy == "auto-continuation":
            completed_candidate = self.active_candidate_id
            next_candidate, *remaining = self.queued_candidate_ids
            self.active_candidate_id = next_candidate
            self.queued_candidate_ids = tuple(remaining)
            self.terminal_candidate = len(remaining) == 0
            self.queue_policy = "human-decision" if not remaining else self.queue_policy
            self.approval_evidence = ""
            self.approval_freeze = None
            self.changed_files = ()
            self.pass_candidate = False
            self.worker_matrix = {}
            self.findings = ()
            self.artifacts = {
                path: record
                for path, record in self.artifacts.items()
                if path == self.STATE_ARTIFACT_PATH
            }
            return Decision(
                status=DecisionStatus.CONTINUE_TOPIC,
                blockers=(f"candidate passed; continue maintenance topic from {completed_candidate} to {next_candidate}",),
                user_visible=False,
                active_candidate_id=completed_candidate,
                next_candidate_id=next_candidate,
                topic_complete=False,
            )
        self.phase = Phase.DECIDED
        return Decision(
            status=DecisionStatus.PASS,
            active_candidate_id=self.active_candidate_id,
            next_candidate_id=self.queued_candidate_ids[0] if self.queued_candidate_ids else "",
            topic_complete=not self.queued_candidate_ids,
        )

    def _decision_from_findings(
        self,
        findings: Sequence[Finding],
        *,
        fallback_status: DecisionStatus,
        fallback_blocker: str | None = None,
    ) -> Decision:
        blockers = tuple(
            finding.message
            for finding in findings
            if finding.blocking and finding.message.strip()
        )
        if not blockers:
            if fallback_blocker:
                blockers = (fallback_blocker,)
            else:
                return Decision(status=DecisionStatus.PASS, active_candidate_id=self.active_candidate_id)

        routed = {
            TARGET_TO_DECISION[finding.target]
            for finding in findings
            if finding.blocking and finding.message.strip()
        }
        if not routed:
            routed = {fallback_status}
        for status in DECISION_PRECEDENCE:
            if status in routed:
                return Decision(
                    status=status,
                    blockers=blockers,
                    user_visible=status not in INTERNAL_CONTINUATION_PHASE,
                    active_candidate_id=self.active_candidate_id,
                )
        return Decision(
            status=fallback_status,
            blockers=blockers,
            user_visible=fallback_status not in INTERNAL_CONTINUATION_PHASE,
            active_candidate_id=self.active_candidate_id,
        )

    def record_failure_mode_ledger(
        self,
        *,
        required: bool,
        mapped: bool,
        direct_evidence: bool,
        severity: FailureModeSeverity | str = FailureModeSeverity.P1,
    ) -> None:
        self.failure_mode_ledger_required = required
        self.failure_mode_ledger_severity = self._coerce_failure_mode_severity(severity)
        self.failure_mode_ledger_mapped = mapped
        self.failure_mode_direct_evidence = direct_evidence

    def record_pass_eligibility(
        self,
        *,
        eligible: bool,
        blocked_by: Sequence[str] = (),
    ) -> None:
        self.pass_eligibility_confirmed = eligible
        self.pass_eligibility_blocked_by = tuple(blocker.strip() for blocker in blocked_by if blocker.strip())

    def calculated_pass_eligibility(self) -> dict[str, Any]:
        profile = self.workflow_profile()
        profile_path = str(profile["path"])
        return calculate_pass_eligibility(
            required_artifact_paths=self.required_pass_artifact_paths_for_profile(profile_path),
            available_artifact_paths=set(self.artifacts),
            mandatory_workers=self.mandatory_workers_for_profile(profile_path),
            worker_status={worker: WorkerEvidence.from_row(evidence).to_row() for worker, evidence in self.worker_matrix.items()},
            approval_hash_ok=self._approval_hash_ok(),
            tests_passed=self._tests_passed(),
            scope_ok=self._scope_ok(),
            pass_candidate=self.pass_candidate,
            state_confirmed=self.pass_eligibility_confirmed,
            state_blockers=list(self.pass_eligibility_blocked_by),
            workflow_path=profile_path,
        )

    def workflow_profile(self) -> dict[str, Any]:
        surfaces = self._approval_surfaces_or_changed_files()
        critical = (
            self.failure_mode_ledger_severity in {FailureModeSeverity.P0, FailureModeSeverity.P1}
            or any(self._path_matches_any(surface, self.FULL_GATED_SURFACE_PATTERNS) for surface in surfaces)
        )
        tiny_doc = (
            not critical
            and self.failure_mode_ledger_severity == FailureModeSeverity.P3
            and bool(surfaces)
            and all(surface.endswith((".md", ".txt")) for surface in surfaces)
        )
        if critical:
            path = "CRITICAL_HARNESS"
            verification = "full-replay"
        elif tiny_doc:
            path = "TINY_DOC"
            verification = "checklist"
        else:
            path = "STANDARD"
            verification = "targeted"
        return {
            "path": path,
            "verification": verification,
            "plan_review_required": path != "TINY_DOC",
            "evaluator_required": path != "TINY_DOC",
            "skeptic_required": path == "CRITICAL_HARNESS",
            "surfaces": list(surfaces),
        }

    def _approval_surfaces_or_changed_files(self) -> tuple[str, ...]:
        if self.approval_freeze and self.approval_freeze.affected_surfaces:
            return tuple(self.approval_freeze.affected_surfaces)
        return tuple(self.changed_files)

    @classmethod
    def _path_matches_any(cls, path: str, patterns: Sequence[str]) -> bool:
        normalized = path.strip().lstrip("./")
        return any(fnmatch(normalized, pattern.strip().lstrip("./")) for pattern in patterns)

    def _decision_findings(self) -> tuple[Finding, ...]:
        missing_worker_findings = tuple(
            Finding(FindingTarget.EVALUATION, f"missing mandatory worker evidence: {worker}")
            for worker in self.MANDATORY_WORKERS
            if not WorkerEvidence.from_row(self.worker_matrix.get(worker, WorkerEvidence(required=True, invoked=False, evidence=""))).satisfied(mandatory=True)
        )
        missing_artifact_findings = tuple(
            Finding(FindingTarget.EVALUATION, f"missing trace artifact: {path}")
            for path in self.REQUIRED_PASS_ARTIFACT_PATHS
            if path not in self.artifacts
        )
        stale_artifact_findings = tuple(
            Finding(
                FindingTarget.EVALUATION,
                f"stale trace artifact: {path} belongs to {record.candidate_id}, not active candidate {self.active_candidate_id}",
            )
            for path, record in self.artifacts.items()
            if path in self.CANDIDATE_ARTIFACT_PATHS
            and record.candidate_id != self.active_candidate_id
        )
        fml_findings = self._failure_mode_ledger_findings()
        pass_eligibility_findings = self._pass_eligibility_findings()
        candidate_findings = () if self.pass_candidate else (Finding(FindingTarget.EVALUATION, "evaluation did not produce pass candidate"),)
        return (
            *self.findings,
            *missing_worker_findings,
            *missing_artifact_findings,
            *stale_artifact_findings,
            *fml_findings,
            *pass_eligibility_findings,
            *candidate_findings,
        )

    def _missing_required_artifacts(self, paths: Sequence[str]) -> tuple[str, ...]:
        return tuple(path for path in paths if path not in self.artifacts)

    def _failure_mode_ledger_findings(self) -> tuple[Finding, ...]:
        if not self._failure_mode_full_replay_required():
            return ()
        findings: list[Finding] = []
        if not self.failure_mode_ledger_mapped:
            findings.append(Finding(FindingTarget.PLAN, "mandatory Failure Mode Ledger mapping is incomplete"))
        if not self.failure_mode_direct_evidence:
            findings.append(Finding(FindingTarget.EVALUATION, "mandatory Failure Mode Ledger direct evidence is incomplete"))
        return tuple(findings)

    def _failure_mode_full_replay_required(self) -> bool:
        return self.failure_mode_ledger_required and self.failure_mode_ledger_severity in {
            FailureModeSeverity.P0,
            FailureModeSeverity.P1,
        }

    @staticmethod
    def _coerce_failure_mode_severity(severity: FailureModeSeverity | str) -> FailureModeSeverity:
        if isinstance(severity, FailureModeSeverity):
            return severity
        normalized = str(severity).strip().upper()
        try:
            return FailureModeSeverity(normalized)
        except ValueError as exc:
            raise ValueError(f"unknown Failure Mode Ledger severity: {severity}") from exc

    def _pass_eligibility_findings(self) -> tuple[Finding, ...]:
        calculated = self.calculated_pass_eligibility()
        findings = [Finding(FindingTarget.EVALUATION, blocker) for blocker in self.pass_eligibility_blocked_by]
        if not calculated["approval_hash_ok"]:
            findings.append(Finding(FindingTarget.APPROVAL, "approval hash does not match frozen plan"))
        if not calculated["tests_passed"]:
            findings.append(Finding(FindingTarget.EVALUATION, "tests have not passed"))
        if not calculated["scope_ok"]:
            findings.append(Finding(FindingTarget.IMPLEMENTATION, "changed files are outside approved surfaces"))
        if not self.pass_eligibility_confirmed:
            findings.append(Finding(FindingTarget.EVALUATION, "pass eligibility was not confirmed by current-run-state"))
        return tuple(findings)

    def _approval_hash_ok(self) -> bool:
        if self.approval_freeze is None:
            return False
        current_plan = self.artifacts.get(self.approval_freeze.plan_path)
        if current_plan is None:
            return False
        if self.approval_freeze.candidate_id != self.active_candidate_id:
            return False
        if current_plan.candidate_id != self.approval_freeze.candidate_id:
            return False
        if current_plan.revision != self.approval_freeze.plan_revision:
            return False
        return not (self.approval_freeze.plan_sha256 and current_plan.content_sha256 and current_plan.content_sha256 != self.approval_freeze.plan_sha256)

    def _tests_passed(self) -> bool:
        return self.pass_candidate and not any(finding.target == FindingTarget.EVALUATION and finding.blocking for finding in self.findings)

    def _scope_ok(self) -> bool:
        if self.approval_freeze is None or not self.approval_freeze.affected_surfaces:
            return False
        if not self.changed_files:
            return False
        return all(self._path_in_approved_surfaces(path, self.approval_freeze.affected_surfaces) for path in self.changed_files)

    def _findings_without_target(self, target: FindingTarget) -> tuple[Finding, ...]:
        return tuple(finding for finding in self.findings if finding.target != target)

    def _append_findings(
        self,
        findings: Sequence[str | Finding],
        *,
        default_target: FindingTarget = FindingTarget.EVALUATION,
    ) -> None:
        coerced = tuple(
            self._coerce_finding(finding, default_target=default_target)
            for finding in findings
            if self._finding_present(finding)
        )
        self.findings = (*self.findings, *coerced)

    def _coerce_finding(
        self,
        finding: str | Finding,
        *,
        default_target: FindingTarget = FindingTarget.EVALUATION,
    ) -> Finding:
        if isinstance(finding, Finding):
            return finding
        return Finding(default_target, finding.strip())

    def _finding_present(self, finding: str | Finding) -> bool:
        if isinstance(finding, Finding):
            return finding.blocking and bool(finding.message.strip())
        return bool(finding.strip())

    def _record_artifact_write_decision(
        self,
        action: ArtifactWriteAction,
        blockers: tuple[str, ...],
    ) -> ArtifactWriteDecision:
        if action == ArtifactWriteAction.RETRY_PLAN:
            self._append_findings(
                [Finding(FindingTarget.PLAN, blocker) for blocker in blockers],
                default_target=FindingTarget.PLAN,
            )
            self.phase = Phase.PLAN_REVIEWED
            return ArtifactWriteDecision(action=action, user_visible=False, blockers=blockers)
        if action == ArtifactWriteAction.NEEDS_HUMAN_DECISION:
            self._append_findings(
                [Finding(FindingTarget.HUMAN_DECISION, blocker) for blocker in blockers],
                default_target=FindingTarget.HUMAN_DECISION,
            )
            self.phase = Phase.DECIDED
            return ArtifactWriteDecision(action=action, user_visible=True, blockers=blockers)
        return ArtifactWriteDecision(action=action, user_visible=False, blockers=blockers)

    def _normalize_trace_artifact_path(self, path: str) -> str:
        normalized = path.strip()
        if normalized not in self.TRACE_ARTIFACT_PATHS:
            raise ValueError(f"unknown maintenance trace artifact: {normalized}")
        return normalized

    def _require_phase(self, expected: Phase, action: str) -> None:
        if self.phase != expected:
            raise InvalidTransition(f"cannot record {action} from {self.phase.value}; expected {expected.value}")

    def _require_approval_evidence(self, action: str) -> None:
        if not self.approval_evidence.strip():
            raise ApprovalRequired(f"{action} requires stored approval evidence")

    def _require_approval_freeze(self, action: str) -> None:
        self._require_approval_evidence(action)
        if self.approval_freeze is None:
            raise ApprovalRequired(f"{action} requires approved plan freeze")
        if self.approval_freeze.candidate_id != self.active_candidate_id:
            raise ApprovalRequired(f"{action} approval freeze candidate does not match active candidate")
        current_plan = self.artifacts.get(self.approval_freeze.plan_path)
        if current_plan is None:
            raise ApprovalRequired(f"{action} requires frozen plan artifact")
        if current_plan.candidate_id != self.approval_freeze.candidate_id:
            raise ApprovalRequired(f"{action} frozen plan candidate changed after approval")
        if self.approval_freeze.plan_sha256 and current_plan.content_sha256 and current_plan.content_sha256 != self.approval_freeze.plan_sha256:
            raise ApprovalRequired(f"{action} frozen plan content hash changed after approval")
        if current_plan.revision != self.approval_freeze.plan_revision:
            raise ApprovalRequired(f"{action} frozen plan artifact changed after approval")

    def _require_changed_files_within_approved_surfaces(self, changed_files: Sequence[str]) -> None:
        if self.approval_freeze is None or not self.approval_freeze.affected_surfaces:
            raise ApprovalRequired("implementation requires approved affected surfaces")
        outside = tuple(
            path for path in changed_files if not self._path_in_approved_surfaces(path, self.approval_freeze.affected_surfaces)
        )
        if outside:
            raise ApprovalRequired(f"implementation changed files outside approved surfaces: {', '.join(outside)}")

    @staticmethod
    def _path_in_approved_surfaces(path: str, surfaces: Sequence[str]) -> bool:
        normalized_path = path.strip().lstrip("./")
        for surface in surfaces:
            normalized_surface = surface.strip().lstrip("./")
            if not normalized_surface:
                continue
            if normalized_surface.endswith("/**"):
                prefix = normalized_surface[:-3].rstrip("/")
                if normalized_path == prefix or normalized_path.startswith(f"{prefix}/"):
                    return True
            if fnmatch(normalized_path, normalized_surface):
                return True
        return False
