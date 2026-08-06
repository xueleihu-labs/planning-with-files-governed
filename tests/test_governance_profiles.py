#!/usr/bin/env python3
"""Focused tests for the L0-L3 governance reduction boundary."""

from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import governance_profiles as governance  # noqa: E402
import plan_contracts as contracts  # noqa: E402
import planning  # noqa: E402


FIXTURE = json.loads(
    (ROOT / "tests" / "fixtures" / "f1-01" / "valid_contracts.json").read_text(encoding="utf-8")
)["task_envelope"]


def route(level: str) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "risk_level": level,
        "route": {
            "L0": "QUICK_EXECUTION",
            "L1": "STANDARD_EXECUTION",
            "L2": "COORDINATED_EXECUTION",
            "L3": "STRICT_SEAL",
        }[level],
        "classifier": "external-risk-router",
        "reason_codes": [f"FIXTURE_{level}"],
    }


class GovernanceProfileTests(unittest.TestCase):
    def envelope(self, **updates: object) -> dict[str, object]:
        value = copy.deepcopy(FIXTURE)
        value.update(updates)
        return value

    def write_envelope(self, directory: Path, value: dict[str, object]) -> Path:
        path = directory / f"{value['task_id']}.json"
        path.write_text(contracts.stable_json(value), encoding="utf-8")
        return path

    def test_external_levels_map_to_the_frozen_profiles(self) -> None:
        expected = {
            "L0": "LIGHT_FAST",
            "L1": "LIGHT_CONTROLLED",
            "L2": "STANDARD",
            "L3": "STRICT",
        }
        for level, profile in expected.items():
            decision = governance.resolve_governance_profile(self.envelope(), risk_route=route(level))
            self.assertEqual(decision["risk_level"], level)
            self.assertEqual(decision["effective_profile"], profile)
            self.assertEqual(decision["requested_profile"], profile)

    def test_missing_route_is_safe_and_does_not_invent_a_light_classification(self) -> None:
        decision = governance.resolve_governance_profile(self.envelope(), risk_route={})
        self.assertEqual(decision["risk_level"], "L2")
        self.assertEqual(decision["effective_profile"], "STANDARD")
        self.assertIn("route_missing_safe_standard_fallback", decision["decision_reason"])

    def test_explicit_light_request_is_allowed_without_a_route(self) -> None:
        decision = governance.resolve_governance_profile(
            self.envelope(), risk_route={}, requested_profile="LIGHT_FAST"
        )
        self.assertEqual(decision["effective_profile"], "LIGHT_FAST")
        self.assertEqual(decision["risk_level"], "L0")

    def test_requested_profile_above_supported_fails_closed(self) -> None:
        decision = governance.resolve_governance_profile(
            self.envelope(), risk_route=route("L3"), supported_profile="STANDARD"
        )
        self.assertEqual(decision["error_code"], "PROFILE_NOT_SUPPORTED")
        self.assertIsNone(decision["effective_profile"])

    def test_create_plan_fails_closed_when_requested_profile_is_unsupported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / "state"
            input_path = self.write_envelope(root, self.envelope(task_id="task-unsupported"))
            result = planning.create_plan(
                input_path,
                state_root=state,
                risk_route=route("L3"),
                supported_profile="STANDARD",
                apply=True,
            )
            self.assertEqual(result["result"], "BLOCKED")
            self.assertEqual(result["error_code"], "PROFILE_NOT_SUPPORTED")
            self.assertEqual(result["top_level_status"], "BLOCKED")
            self.assertFalse(state.exists())

    def test_lower_request_cannot_downgrade_upstream_risk(self) -> None:
        decision = governance.resolve_governance_profile(
            self.envelope(), risk_route=route("L3"), requested_profile="LIGHT_FAST"
        )
        self.assertEqual(decision["effective_profile"], "STRICT")
        self.assertIn("risk_route_overrides_request", decision["decision_reason"])

    def test_l0_is_zero_write_and_has_one_top_level_status(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / "state"
            input_path = self.write_envelope(root, self.envelope(task_id="task-l0"))
            result = planning.create_plan(input_path, state_root=state, risk_route=route("L0"), apply=True)
            self.assertEqual(result["result"], "LIGHTWEIGHT_ROUTED")
            self.assertEqual(result["effective_profile"], "LIGHT_FAST")
            self.assertEqual(result["top_level_status"], "READY")
            self.assertEqual(result["created_files"], [])
            self.assertFalse(state.exists())

    def test_l1_is_light_without_a_plan_or_five_tables(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / "state"
            input_path = self.write_envelope(root, self.envelope(task_id="task-l1"))
            result = planning.create_plan(input_path, state_root=state, risk_route=route("L1"), apply=True)
            self.assertEqual(result["effective_profile"], "LIGHT_CONTROLLED")
            self.assertEqual(result["top_level_status"], "READY")
            self.assertFalse(state.exists())

    def test_l2_writes_only_the_minimal_plan_view_and_disables_pre_gates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / "state"
            input_path = self.write_envelope(root, self.envelope(task_id="task-l2"))
            result = planning.create_plan(input_path, state_root=state, risk_route=route("L2"), apply=True)
            instance = state / "task-l2"
            self.assertEqual(result["result"], "CREATED")
            self.assertEqual(result["effective_profile"], "STANDARD")
            self.assertEqual(result["top_level_status"], "READY")
            self.assertTrue((instance / "plan-package.json").is_file())
            self.assertTrue((instance / "WORKFLOW_CHECKLIST.md").is_file())
            self.assertFalse((instance / "1_master_plan.md").exists())
            self.assertFalse((instance / "governance").exists())
            self.assertFalse((instance / "checkpoints").exists())
            plan = json.loads((instance / "plan-package.json").read_text(encoding="utf-8"))
            self.assertEqual(plan["task_profile"], "STANDARD")
            self.assertEqual(plan["governance_policy"]["required_stages"], [])
            self.assertFalse(plan["governance_policy"]["require_pre_write"])
            self.assertFalse(plan["governance_policy"]["require_pre_close"])

            pre_write = planning.create_governance_request(instance, "PRE_WRITE", "P01", preview=True)
            pre_close = planning.create_governance_request(instance, "PRE_CLOSE", "P01", preview=True)
            self.assertEqual(pre_write["error_code"], "GOVERNANCE_STAGE_DISABLED")
            self.assertEqual(pre_close["error_code"], "GOVERNANCE_STAGE_DISABLED")
            self.assertFalse((instance / "governance").exists())

    def test_l3_keeps_the_full_fail_closed_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / "state"
            input_path = self.write_envelope(root, self.envelope(task_id="task-l3", risk_level="CRITICAL"))
            result = planning.create_plan(input_path, state_root=state, risk_route=route("L3"), apply=True)
            instance = state / "task-l3"
            self.assertEqual(result["effective_profile"], "STRICT")
            self.assertEqual(result["top_level_status"], "READY")
            self.assertTrue((instance / "1_master_plan.md").is_file())
            plan = json.loads((instance / "plan-package.json").read_text(encoding="utf-8"))
            self.assertEqual(plan["task_profile"], "STRICT")
            self.assertEqual(
                plan["governance_policy"]["required_stages"],
                ["PRE_WRITE", "POST_WRITE", "PRE_CLOSE"],
            )
            self.assertTrue(plan["governance_policy"]["require_read_head"])
            self.assertTrue(plan["governance_policy"]["require_root_binding"])
            self.assertEqual(plan["finalization_policy"]["mode"], "ADVANCED")

    def test_legacy_creation_remains_available_and_existing_instance_wins(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / "state"
            input_path = self.write_envelope(
                root,
                self.envelope(task_id="task-legacy", risk_level="LOW", priority="P2"),
            )
            first = planning.create_plan(input_path, state_root=state, legacy=True, apply=True)
            self.assertEqual(first["task_profile"], "LIGHTWEIGHT")
            second = planning.create_plan(input_path, state_root=state, risk_route=route("L0"), apply=True)
            self.assertEqual(second["result"], "EXISTING_PLAN")
            self.assertEqual(second["task_profile"], "LIGHTWEIGHT")
            self.assertEqual(second["effective_profile"], "LIGHTWEIGHT")

    def test_top_level_status_normalization_is_single_valued(self) -> None:
        self.assertEqual(governance.normalize_top_level_status("进行中"), "READY")
        self.assertEqual(governance.normalize_top_level_status("阻塞"), "BLOCKED")
        self.assertEqual(governance.normalize_top_level_status("待人工"), "WAITING_OWNER")
        self.assertEqual(governance.normalize_top_level_status("已完成"), "COMPLETED")
        self.assertEqual(
            governance.normalize_top_level_status("进行中", blocking_findings=["P2 warning"]),
            "READY",
        )
        self.assertEqual(
            governance.normalize_top_level_status("进行中", blocking_findings=["P0 failure"]),
            "BLOCKED",
        )


if __name__ == "__main__":
    unittest.main()
