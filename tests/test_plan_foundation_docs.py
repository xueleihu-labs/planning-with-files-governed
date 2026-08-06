#!/usr/bin/env python3
"""Contract checks for the PLAN foundation documentation and isolated instance."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_DRAFT = ROOT / "templates" / "workflow" / "drafts" / "plan-unified-system-foundation" / "1.0.0.md"
REGISTRY = ROOT / "templates" / "workflow" / "template_registry.json"
GENERIC_TEMPLATE = ROOT / "templates" / "workflow" / "base" / "generic-project" / "1.0.0.md"
sys.path.insert(0, str(ROOT / "scripts"))
import workflow_contracts as contracts  # noqa: E402
import workflow_template_matcher as matcher  # noqa: E402
import checkpoint_reader  # noqa: E402


def load_checkpoint_core():
    return checkpoint_reader


class PlanFoundationDocumentationTests(unittest.TestCase):
    def test_plan_standard_is_frozen_and_complete(self) -> None:
        text = (ROOT / "PLAN_STANDARD.md").read_text(encoding="utf-8")
        self.assertIn("PLAN_STANDARD_VERSION: 1.0.0", text)
        self.assertIn("OWNER: planning-with-files", text)
        self.assertIn("STATUS: FROZEN", text)
        for heading in (
            "TaskEnvelope",
            "PlanPackage",
            "ExecutionPacket",
            "能力注册",
            "标准事件",
            "执行回执",
            "冲突裁决",
            "知识交接",
            "F0–F6 实施路线",
            "最低可用验收标准",
            "明确禁止方向",
        ):
            self.assertIn(heading, text)


    def test_matcher_does_not_select_unregistered_draft(self) -> None:
        result = matcher.identify_template(
            ROOT,
            ROOT,
            project_name=ROOT.name,
            source_text="PLAN 统一规划与协同体系地基建设",
            explicit_template_id=None,
        )
        self.assertNotEqual(result["template_id"], "plan-unified-system-foundation")

    def test_checklist_contract_and_required_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            instance = Path(temporary) / "state-root" / "plan-unified-system-foundation"
            instance.mkdir(parents=True)
            checklist_path = instance / "WORKFLOW_CHECKLIST.md"
            checklist_path.write_text(self._checklist_fixture(), encoding="utf-8")
            checklist = checklist_path.read_text(encoding="utf-8")
        metadata = contracts.validate_checklist_text(checklist)
        self.assertEqual(metadata["template"]["template_id"], "generic-project")
        self.assertEqual(metadata["template"]["template_digest"], contracts.file_digest(GENERIC_TEMPLATE))
        tasks = contracts.checklist_tasks(checklist)
        ids = {task["ID"] for task in tasks}
        self.assertTrue({"F0-01", "F0-02", "F0-03", "F0-04", "F0-05"}.issubset(ids))
        self.assertTrue({"F1", "F2", "F3", "F4", "F5", "F6"}.issubset(ids))
        self.assertEqual(metadata["current_phase"], "F0")
        self.assertEqual(metadata["overall_status"], "未开始")
        self.assertEqual(metadata["recommended_next_task"], "F0-01")
        self.assertEqual(contracts.workflow_integrity_errors(checklist), [])
        self.assertIn("HUMAN_EXECUTION_GATE：WAITING_FOR_OWNER", checklist)

    def test_public_state_root_resolver_uses_temporary_override(self) -> None:
        module = load_checkpoint_core()
        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = Path(temporary).resolve()
            old = os.environ.get("PHASE_CHECKPOINT_STATE_ROOT")
            os.environ["PHASE_CHECKPOINT_STATE_ROOT"] = str(temporary_root)
            try:
                resolved = module.runtime_state_root(ROOT, None)
            finally:
                if old is None:
                    os.environ.pop("PHASE_CHECKPOINT_STATE_ROOT", None)
                else:
                    os.environ["PHASE_CHECKPOINT_STATE_ROOT"] = old
            self.assertEqual(resolved, temporary_root)
            self.assertTrue(resolved.is_absolute())
            self.assertNotEqual(resolved, ROOT)
            self.assertNotIn(".git", resolved.parts)
            instance = resolved / "plan-unified-system-foundation"
            self.assertEqual(instance.parent, resolved)

    def test_inventory_is_scaffold_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            instance = Path(temporary) / "state-root" / "plan-unified-system-foundation"
            instance.mkdir(parents=True)
            inventory_path = instance / "F0_CAPABILITY_INVENTORY.md"
            inventory_path.write_text(self._inventory_fixture(), encoding="utf-8")
            inventory = inventory_path.read_text(encoding="utf-8")
        self.assertIn("STATUS: SCAFFOLD_ONLY", inventory)
        self.assertIn("EXECUTION_STARTED: NO", inventory)
        self.assertIn("CURRENT_TASK: WAITING_FOR_F0-01", inventory)
        self.assertIn("F0: NOT STARTED", inventory)

    @staticmethod
    def _checklist_fixture() -> str:
        rows = [
            ("F0-01", "盘点现有 PLAN 相关能力", "无", "F0-02"),
            ("F0-02", "建立 Skill 能力地图", "F0-01", "F0-03"),
            ("F0-03", "识别重复与缺失能力", "F0-01、F0-02", "F0-04"),
            ("F0-04", "形成最小改动方案", "F0-03", "F0-05"),
            ("F0-05", "冻结禁止新增项", "F0-04", "F1"),
            ("F1", "PLAN 最小可用主核", "F0 通过", "F2"),
            ("F2", "Project上游接入", "F1 通过", "F3"),
            ("F3", "同级 Skill 逐项接入", "F2 通过", "F4"),
            ("F4", "发布系统最小连接", "F3 通过", "F5"),
            ("F5", "端到端真实任务验收", "F4 通过", "F6"),
            ("F6", "推广统一标准", "F5 通过", "验收/封板"),
        ]
        lines = [
            contracts.render_machine_block("workflow", {
                "checklist_version": "1.0.0",
                "current_phase": "F0",
                "last_updated_at": "2026-07-16T08:05:31+08:00",
                "modules": [],
                "overall_status": "未开始",
                "owner_agent": "0号调研官",
                "project_id": "plan-unified-system-foundation",
                "recommended_next_task": "F0-01",
                "template": {
                    "template_digest": contracts.file_digest(GENERIC_TEMPLATE),
                    "template_id": "generic-project",
                    "template_version": "1.0.0",
                },
                "workflow_schema_version": 1,
            }),
            "",
            "HUMAN_EXECUTION_GATE：WAITING_FOR_OWNER",
            "",
            "## 工作流任务清单",
            "",
            "| ID | 阶段/任务 | 主责 | 前置条件 | 必需产物 | 验收证据 | 人工闸门 | 状态 | 核验状态 | 阻塞/备注 | 下一步 |",
            "|---|---|---|---|---|---|---|---|---|---|---|",
        ]
        lines.extend(
            f"| {task_id} | {name} | 待指定 | {dependency} | 待补 | — | 等待老板开工 | 未开始 | 未核验 | — | {next_task} |"
            for task_id, name, dependency, next_task in rows
        )
        return "\n".join(lines) + "\n"

    @staticmethod
    def _inventory_fixture() -> str:
        return "\n".join([
            "STATUS: SCAFFOLD_ONLY",
            "EXECUTION_STARTED: NO",
            "CURRENT_TASK: WAITING_FOR_F0-01",
            "## 1. 盘点范围",
            "待 F0-01。",
            "## 9. F0 验收结论",
            "F0: NOT STARTED",
        ])


if __name__ == "__main__":
    unittest.main()
