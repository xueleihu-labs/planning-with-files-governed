#!/usr/bin/env python3
# VERSION source: ../VERSION
"""Single source for planning-with-files L0-L3 governance profiles.

The governance router owns risk classification.  This module consumes its structured
result, maps it to the local planning profile, and exposes only deterministic
policy decisions.  It deliberately does not inspect task prose or maintain a
second keyword classifier.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "governance_profiles.json"
PROFILE_ORDER = ("LIGHT_FAST", "LIGHT_CONTROLLED", "STANDARD", "STRICT")
PROFILE_RANK = {name: index for index, name in enumerate(PROFILE_ORDER)}
RISK_LEVELS = ("L0", "L1", "L2", "L3")
RISK_RANK = {name: index for index, name in enumerate(RISK_LEVELS)}
LEGACY_PROFILES = {"LIGHTWEIGHT", "STANDARD", "FULL", "HIGH_RISK"}
LEGACY_RISK = {
    "LIGHTWEIGHT": "L1",
    "STANDARD": "L2",
    "FULL": "L3",
    "HIGH_RISK": "L3",
}


def _load_config() -> dict[str, Any]:
    try:
        value = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot load governance profile config: {CONFIG_PATH}: {exc}") from exc
    if not isinstance(value, dict):
        raise RuntimeError("governance profile config must be an object")
    if tuple(value.get("profile_order", ())) != PROFILE_ORDER:
        raise RuntimeError("governance profile order does not match the frozen L0-L3 contract")
    profiles = value.get("profiles")
    if not isinstance(profiles, dict) or set(profiles) != set(PROFILE_ORDER):
        raise RuntimeError("governance profile config must define exactly the four supported profiles")
    return value


CONFIG = _load_config()
PROFILE_CONFIG = copy.deepcopy(CONFIG["profiles"])
RISK_TO_PROFILE = dict(CONFIG["risk_to_profile"])
DEFAULT_SUPPORTED_PROFILE = str(CONFIG["default_supported_profile"])


def profile_rank(profile: str) -> int:
    normalized = str(profile).strip().upper()
    if normalized in PROFILE_RANK:
        return PROFILE_RANK[normalized]
    if normalized in LEGACY_PROFILES:
        return PROFILE_RANK[legacy_to_profile(normalized)]
    raise ValueError(f"unsupported governance profile: {profile}")


def legacy_to_profile(profile: str) -> str:
    normalized = str(profile).strip().upper()
    mapping = {
        "LIGHTWEIGHT": "LIGHT_CONTROLLED",
        "STANDARD": "STANDARD",
        "FULL": "STRICT",
        "HIGH_RISK": "STRICT",
    }
    try:
        return mapping[normalized]
    except KeyError as exc:
        raise ValueError(f"unsupported legacy profile: {profile}") from exc


def _profile_for_risk(risk_level: str) -> str:
    try:
        return str(RISK_TO_PROFILE[risk_level])
    except KeyError as exc:
        raise ValueError(f"unsupported governance risk level: {risk_level}") from exc


def _risk_for_profile(profile: str) -> str:
    normalized = str(profile).strip().upper()
    if normalized in LEGACY_PROFILES:
        normalized = legacy_to_profile(normalized)
    for risk_level, mapped in RISK_TO_PROFILE.items():
        if mapped == normalized:
            return risk_level
    raise ValueError(f"unsupported governance profile: {profile}")


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _route_object(route: Any) -> dict[str, Any]:
    if not isinstance(route, dict):
        return {}
    nested = route.get("risk_route")
    if isinstance(nested, dict):
        return nested
    return route


def _route_risk(route: Any) -> str | None:
    value = _route_object(route)
    candidate = _text(value.get("risk_level") or value.get("level")).upper()
    return candidate if candidate in RISK_LEVELS else None


def _embedded_route(envelope: dict[str, Any]) -> Any:
    for key in ("risk_route", "governance_route", "governance_route"):
        if key in envelope:
            return envelope[key]
    return None


def _legacy_decision(envelope: dict[str, Any], profile: str | None = None) -> dict[str, Any]:
    legacy_profile = str(profile or "").strip().upper()
    if legacy_profile not in LEGACY_PROFILES:
        risk = _text(envelope.get("risk_level")).upper()
        priority = _text(envelope.get("priority")).upper()
        if risk in {"HIGH", "CRITICAL"}:
            legacy_profile = "HIGH_RISK"
        elif priority == "P0":
            legacy_profile = "FULL"
        elif risk == "MEDIUM" or priority == "P1":
            legacy_profile = "STANDARD"
        else:
            legacy_profile = "LIGHTWEIGHT"
    stages = {
        "LIGHTWEIGHT": ["POST_WRITE"],
        "STANDARD": ["PRE_WRITE", "POST_WRITE"],
        "FULL": ["PRE_WRITE", "POST_WRITE", "PRE_CLOSE"],
        "HIGH_RISK": ["PRE_WRITE", "POST_WRITE", "PRE_CLOSE"],
    }[legacy_profile]
    return {
        "requested_profile": legacy_profile,
        "supported_profile": legacy_profile,
        "effective_profile": legacy_profile,
        "risk_level": LEGACY_RISK[legacy_profile],
        "enabled_gates": ["LEGACY_GOVERNANCE"],
        "disabled_gates": [],
        "decision_reason": ["legacy_compatibility_mode"],
        "required_stages": list(stages),
        "create_formal_plan": True,
        "generate_five_tables": True,
        "create_checkpoint": legacy_profile in {"FULL", "HIGH_RISK"},
        "require_pre_write": "PRE_WRITE" in stages,
        "require_pre_close": "PRE_CLOSE" in stages,
        "require_read_head": legacy_profile in {"FULL", "HIGH_RISK"},
        "require_root_binding": legacy_profile in {"FULL", "HIGH_RISK"},
        "require_independent_audit": legacy_profile in {"FULL", "HIGH_RISK"},
        "require_owner_acceptance": legacy_profile in {"FULL", "HIGH_RISK"},
        "allow_automatic_recovery": legacy_profile in {"LIGHTWEIGHT", "STANDARD"},
        "finalization_mode": "ADVANCED" if legacy_profile in {"FULL", "HIGH_RISK"} else "SIMPLE",
        "legacy_mode": True,
        "error_code": None,
    }


def _decision_for_profile(
    *,
    requested_profile: str,
    supported_profile: str,
    risk_level: str,
    effective_profile: str | None,
    reasons: Iterable[str],
    error_code: str | None = None,
) -> dict[str, Any]:
    profile = PROFILE_CONFIG[effective_profile] if effective_profile else None
    result: dict[str, Any] = {
        "requested_profile": requested_profile,
        "supported_profile": supported_profile,
        "effective_profile": effective_profile,
        "risk_level": risk_level,
        "enabled_gates": copy.deepcopy(profile["enabled_gates"] if profile else []),
        "disabled_gates": copy.deepcopy(profile["disabled_gates"] if profile else []),
        "decision_reason": list(dict.fromkeys(str(item) for item in reasons)),
        "required_stages": copy.deepcopy(profile["required_stages"] if profile else []),
        "create_formal_plan": bool(profile and profile["create_formal_plan"]),
        "generate_five_tables": bool(profile and profile["generate_five_tables"]),
        "create_checkpoint": bool(profile and profile["create_checkpoint"]),
        "require_pre_write": bool(profile and profile["require_pre_write"]),
        "require_pre_close": bool(profile and profile["require_pre_close"]),
        "require_read_head": bool(profile and profile["require_read_head"]),
        "require_root_binding": bool(profile and profile["require_root_binding"]),
        "require_independent_audit": bool(profile and profile["require_independent_audit"]),
        "require_owner_acceptance": bool(profile and profile["require_owner_acceptance"]),
        "allow_automatic_recovery": bool(profile and profile["allow_automatic_recovery"]),
        "finalization_mode": profile["finalization_mode"] if profile else "NONE",
        "legacy_mode": False,
        "error_code": error_code,
    }
    if error_code:
        result["result"] = error_code
    return result


def resolve_governance_profile(
    envelope: dict[str, Any],
    *,
    risk_route: Any = None,
    requested_profile: str | None = None,
    supported_profile: str | None = None,
    legacy: bool = False,
) -> dict[str, Any]:
    """Resolve one immutable policy decision from an upstream route result.

    ``risk_route`` is intentionally data-only.  Passing an empty object means
    that the caller attempted the new route but the upstream result was
    unavailable; that case safely selects STANDARD instead of LIGHT.
    """
    if not isinstance(envelope, dict):
        raise ValueError("envelope must be an object")
    embedded = _embedded_route(envelope)
    route = risk_route if risk_route is not None else embedded
    explicit = _text(requested_profile or envelope.get("requested_profile")).upper()
    if legacy:
        return _legacy_decision(envelope, explicit or None)

    supported = _text(supported_profile or envelope.get("supported_profile") or DEFAULT_SUPPORTED_PROFILE).upper()
    if supported not in PROFILE_RANK:
        raise ValueError(f"unsupported supported_profile: {supported}")
    upstream_risk = _route_risk(route)
    reasons: list[str] = []
    if upstream_risk is None:
        if explicit:
            if explicit not in PROFILE_RANK:
                raise ValueError(f"unsupported requested_profile: {explicit}")
            upstream_risk = _risk_for_profile(explicit)
            reasons.append("explicit_profile_without_route")
        elif _text(envelope.get("risk_level")).upper() in {"HIGH", "CRITICAL"}:
            upstream_risk = "L3"
            reasons.append("envelope_high_risk_fallback")
        else:
            upstream_risk = "L2"
            reasons.append("route_missing_safe_standard_fallback")
    else:
        reasons.append("consumed_governance_route")

    routed_profile = _profile_for_risk(upstream_risk)
    requested = explicit or routed_profile
    if requested not in PROFILE_RANK:
        raise ValueError(f"unsupported requested_profile: {requested}")
    if PROFILE_RANK[requested] < PROFILE_RANK[routed_profile]:
        reasons.append("risk_route_overrides_request")
    required_rank = max(PROFILE_RANK[requested], PROFILE_RANK[routed_profile])
    if PROFILE_RANK[requested] > PROFILE_RANK[supported] or required_rank > PROFILE_RANK[supported]:
        reasons.append("requested_or_risk_profile_exceeds_supported_capability")
        return _decision_for_profile(
            requested_profile=requested,
            supported_profile=supported,
            risk_level=upstream_risk,
            effective_profile=None,
            reasons=reasons,
            error_code="PROFILE_NOT_SUPPORTED",
        )
    effective = PROFILE_ORDER[required_rank]
    return _decision_for_profile(
        requested_profile=requested,
        supported_profile=supported,
        risk_level=upstream_risk,
        effective_profile=effective,
        reasons=reasons,
    )


resolve_profile = resolve_governance_profile


def normalize_top_level_status(
    raw_status: Any,
    *,
    blocking_findings: Iterable[Any] | None = None,
    waiting_owner: bool = False,
    completed: bool = False,
) -> str:
    """Collapse detailed runtime states into one executor-facing status."""
    if completed:
        return "COMPLETED"
    raw = _text(raw_status).upper()
    if waiting_owner or raw in {"WAITING_OWNER", "WAITING_FOR_OWNER", "WAITING_FOR_HUMAN", "待人工", "暂停"}:
        return "WAITING_OWNER"
    if raw in {"BLOCKED", "FAILED", "阻塞", "失败", "CLOSE_BLOCKED"}:
        return "BLOCKED"
    for finding in blocking_findings or ():
        value = _text(finding).upper()
        if "P2" in value or "WARNING" in value or "WARN" in value:
            continue
        if any(marker in value for marker in ("P0", "P1", "BLOCKED", "FAILED", "ERROR", "MISSING_")):
            return "BLOCKED"
    if raw in {"COMPLETED", "CLOSED", "已完成"}:
        return "COMPLETED"
    return "READY"


def plan_governance_policy(decision: dict[str, Any], *, legacy_stages: list[str] | None = None) -> dict[str, Any]:
    """Return the policy fragment persisted in a new PlanPackage."""
    effective = decision.get("effective_profile")
    if not effective:
        raise ValueError("cannot build a plan policy without an effective profile")
    stages = list(legacy_stages if legacy_stages is not None else decision.get("required_stages", []))
    policy = {
        "profile": effective,
        "requested_profile": decision.get("requested_profile"),
        "supported_profile": decision.get("supported_profile"),
        "risk_level": decision.get("risk_level"),
        "enabled_gates": copy.deepcopy(decision.get("enabled_gates", [])),
        "disabled_gates": copy.deepcopy(decision.get("disabled_gates", [])),
        "decision_reason": copy.deepcopy(decision.get("decision_reason", [])),
        "required_stages": stages,
        "blocking_results": ["BLOCKED", "INCONCLUSIVE"],
        "receipt_required": bool(decision.get("require_pre_write") or decision.get("require_pre_close")),
        "integration_status": "RESERVED_ONLY",
        "create_formal_plan": bool(decision.get("create_formal_plan")),
        "generate_five_tables": bool(decision.get("generate_five_tables")),
        "create_checkpoint": bool(decision.get("create_checkpoint")),
        "require_pre_write": bool(decision.get("require_pre_write")),
        "require_pre_close": bool(decision.get("require_pre_close")),
        "require_read_head": bool(decision.get("require_read_head")),
        "require_root_binding": bool(decision.get("require_root_binding")),
        "require_independent_audit": bool(decision.get("require_independent_audit")),
        "require_owner_acceptance": bool(decision.get("require_owner_acceptance")),
        "allow_automatic_recovery": bool(decision.get("allow_automatic_recovery")),
    }
    return policy
