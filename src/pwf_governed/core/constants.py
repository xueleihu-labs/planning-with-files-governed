"""Gate 2 extracted module: core/constants.py.

Generated from the Gate 1 planning.py baseline.
"""
from __future__ import annotations

from pathlib import Path
import re

from pwf_governed._legacy import (
    plan_contracts,
)
from pwf_governed._legacy import plan_contracts as contracts

from pwf_governed._legacy import (
    plan_contracts,
)

SKILL_ROOT = Path(__file__).resolve().parents[1]

CURRENT_VERSION = "2.0.0"

PLAN_VERSION = "1.0.0"

GENERIC_TEMPLATE_ID = "generic-project"

PACKETS_DIR = "packets"

RECEIPTS_DIR = "receipts"

GOVERNANCE_DIR = "governance"

GOVERNANCE_REQUESTS_DIR = f"{GOVERNANCE_DIR}/requests"

GOVERNANCE_RECEIPTS_DIR = f"{GOVERNANCE_DIR}/receipts"

OWNER_GATE_RECEIPTS_DIR = f"{GOVERNANCE_DIR}/owner-gate-receipts"

CHECKPOINTS_DIR = "checkpoints"

CHECKPOINT_REFS_DIR = f"{CHECKPOINTS_DIR}/refs"

CHECKPOINT_RESUMES_DIR = f"{CHECKPOINTS_DIR}/resumes"

PACKET_ID_PREFIX = "pkt-"

GOVERNANCE_REQUEST_ID_PREFIX = "gov-"

OUTCOMES_DIR = "outcomes"

ROUTING_DECISION_RELATIVE = f"{OUTCOMES_DIR}/routing-decision.json"

ROUTING_DECISIONS_BY_CHECKPOINT_DIR = f"{OUTCOMES_DIR}/routing-decisions/by-checkpoint"

EVOLUTION_DIR = f"{OUTCOMES_DIR}/evolution"

EVOLUTION_SIGNALS_BY_CHECKPOINT_DIR = f"{EVOLUTION_DIR}/by-checkpoint"

EVOLUTION_SIGNAL_RELATIVE = f"{EVOLUTION_DIR}/evolution-signal.json"

EVOLUTION_RECEIPTS_DIR = f"{EVOLUTION_DIR}/receipts"

CONTENT_DIR = f"{OUTCOMES_DIR}/content"

KNOWLEDGE_HANDOFF_RELATIVE = f"{CONTENT_DIR}/knowledge_handoff.json"

CONTENT_RECEIPTS_DIR = f"{CONTENT_DIR}/receipts"

KNOWLEDGE_HANDOFFS_BY_CHECKPOINT_DIR = f"{CONTENT_DIR}/by-checkpoint"

OUTCOME_DECISIONS = set(contracts.OUTCOME_DECISIONS)

EVOLUTION_RESULTS = set(contracts.EVOLUTION_RESULTS)

CONTENT_RESULTS = set(contracts.CONTENT_RESULTS)

PUBLICATION_DESTINATION_SYSTEM = "external-publishing-system"

FINALIZATION_MODES = {"SIMPLE", "ADVANCED"}

FINALIZATION_RESULTS = {
    "CLOSE_READY",
    "CLOSED",
    "CLOSE_BLOCKED",
    "CLOSE_WAITING_HUMAN",
    "ALREADY_CLOSED",
}

_MISSING_ROUTE = object()

SENSITIVE_MARKERS = re.compile(
    r"(?i)(api[_ -]?key|access[_ -]?token|password|passwd|secret|private key|begin (rsa|openssh|ec) private key)"
)

CHECKPOINT_READY_ACTIONS = {"ADVANCE_PHASE", "PUBLISHED_COMMIT"}

CHECKPOINT_PAUSED_ACTIONS = {"HOLD", "BLOCKED", "FAILED"}

CHECKPOINT_HUMAN_ACTIONS = {"INCONCLUSIVE", "UNKNOWN_STATUS"}

CHECKPOINT_COMPLETION_ACTIONS = {"COMPLETION_CANDIDATE"}

CHECKPOINT_LOCAL_FIELDS = {
    "task_id",
    "checkpoint_consumer_status",
    "resume_status",
    "effective_action",
    "source_ref_digest",
    "plan_package_digest",
    "verified_evidence",
    "verified_receipt",
    "recorded_at",
    "local_consumer_version",
    "checkpoint_ref_path",
    "pause_reason",
    "required_resolution",
    "paused_at_phase",
    "completion_candidate",
}

GOVERNANCE_STAGES = set(contracts.GOVERNANCE_STAGES)

GOVERNANCE_RESULTS = set(contracts.GOVERNANCE_RESULTS)

GOVERNANCE_CANDIDATE_CLASSES = {
    "KEEP",
    "MERGE_CANDIDATE",
    "DEPRECATE_CANDIDATE",
    "ARCHIVE_CANDIDATE",
    "DELETE_REQUIRES_OWNER_APPROVAL",
}

FORMAL_PROTECTED_ASSETS = (
    "VERSION",
    "schemas/plan-contracts.schema.json",
    "templates/workflow/template_registry.json",
    "templates/workflow/00_TEMPLATE_REGISTRY.md",
    "templates/workflow/task-types/",
    "templates/workflow/modules/",
    "templates/workflow/candidates/",
)

OWNER_GATE_RECEIPT_TYPE = "OWNER_GATE_REGISTRATION"

OWNER_GATE_RECEIPT_SCHEMA_VERSION = 1

OWNER_GATE_IDENTITY_ASSURANCE = "NO_CRYPTOGRAPHIC_OWNER_IDENTITY_CLAIM"
