#!/usr/bin/env python3
"""P3-01 deterministic workflow replay tests."""
from __future__ import annotations

import copy
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import workflow_contracts as contracts
import workflow_replay as replay


class WorkflowReplayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.skill = self.root / "skill"
        shutil.copytree(ROOT / "templates", self.skill / "templates")
        self.project = self.root / "project"
        self.project.mkdir()
        self.template = self.skill / "templates/workflow/base/generic-project/1.0.0.md"
        self.digest = contracts.file_digest(self.template)
        self.selection = {
            "template_id": "generic-project", "template_version": "1.0.0",
            "template_digest": self.digest, "match_method": "explicit",
            "confidence": 1.0, "matched_signals": [], "excluded_templates": [],
            "fallback_used": False, "reason": "test",
        }

    def tearDown(self):
        self.tmp.cleanup()

    def binding(self):
        return {
            "template_id": self.selection["template_id"],
            "template_version": self.selection["template_version"],
            "template_digest": self.selection["template_digest"],
        }

    def module(self):
        path = self.skill / "templates/workflow/modules/testing/1.0.0.md"
        return {"module_id": "testing", "module_version": "1.0.0", "module_digest": contracts.file_digest(path)}

    def task_rows(self, statuses=None, evidence=None, owners=None, dependencies=None, order=None, module=False):
        statuses, evidence, owners, dependencies = statuses or {}, evidence or {}, owners or {}, dependencies or {}
        base = [
            ("P01", "需求确认", "Product Manager", "无", "P0"),
            ("P02", "执行与验证", "Executor", "P01", "P1"),
            ("P03", "审计交接", "Auditor", "P02", "P1"),
        ]
        if module:
            base += [("T01", "运行结构检查", "Executor", "P03", "P1"), ("T02", "运行回归测试", "Executor", "T01", "P1")]
        by_id = {}
        for task_id, name, owner, dependency, priority in base:
            by_id[task_id] = {
                "id": task_id, "name": name, "owner": owners.get(task_id, owner),
                "dependency": dependencies.get(task_id, dependency), "priority": priority,
                "mainline": "是", "status": statuses.get(task_id, "未开始"),
                "verification": "已核验" if statuses.get(task_id) == "已完成" else "未核验",
                "evidence": evidence.get(task_id, "—"), "note": "—", "next": "验收/封板",
            }
        for task_id in (order or []):
            if task_id not in by_id:
                by_id[task_id] = {
                    "id": task_id, "name": "新增任务", "owner": "Executor", "dependency": "P01",
                    "priority": "P1", "mainline": "是", "status": "未开始", "verification": "未核验",
                    "evidence": "—", "note": "—", "next": "验收/封板",
                }
        return [by_id[task_id] for task_id in (order or [item[0] for item in base])]

    def checklist(self, rows, modules=None, history=None, extra=None, completion_column=False):
        metadata = contracts.initial_workflow_metadata("demo-project", self.binding(), modules or [], self.selection, "Codex")
        if extra:
            metadata.update(copy.deepcopy(extra))
        lines = ["# Workflow Checklist: demo-project", "", contracts.render_machine_block("workflow", metadata), "", "## 工作流任务清单", ""]
        if completion_column:
            lines += [
                "| ID | 阶段/任务 | 主责智能体 | 前置条件 | 优先级 | 主线 | 状态 | 核验状态 | 完成条件 | 完成证据 | 阻塞/备注 | 下一步 |",
                "|---|---|---|---|---|---|---|---|---|---|---|---|",
            ]
            for row in rows:
                completion = "实际完成条件" if row["id"] == "P02" else ""
                lines.append("| {id} | {name} | {owner} | {dependency} | {priority} | {mainline} | {status} | {verification} | {completion} | {evidence} | {note} | {next} |".format(completion=completion, **row))
        else:
            lines += [
                "| ID | 阶段/任务 | 主责智能体 | 前置条件 | 优先级 | 主线 | 状态 | 核验状态 | 完成证据 | 阻塞/备注 | 下一步 |",
                "|---|---|---|---|---|---|---|---|---|---|---|",
            ]
            for row in rows:
                lines.append("| {id} | {name} | {owner} | {dependency} | {priority} | {mainline} | {status} | {verification} | {evidence} | {note} | {next} |".format(**row))
        lines += ["", "## 变更历史", "", "| 时间 | 变更类型 | 涉及ID | 变更内容 | 原因 | 影响范围 | 执行者 |", "|---|---|---|---|---|---|---|"]
        history = history or [{"时间": "—", "变更类型": "初始化", "涉及ID": "—", "变更内容": "从模板实例化", "原因": "初始基线", "影响范围": "全部任务", "执行者": "planning-with-files"}]
        for row in history:
            lines.append("| {时间} | {变更类型} | {涉及ID} | {变更内容} | {原因} | {影响范围} | {执行者} |".format(**row))
        return "\n".join(lines) + "\n"

    def write(self, text):
        (self.project / contracts.CHECKLIST_NAME).write_text(text, encoding="utf-8")

    def history(self, task_ids):
        result = []
        for task_id in task_ids:
            result += [
                {"时间": "2026-07-15T12:00:00+08:00", "变更类型": "完成", "涉及ID": task_id, "变更内容": f"{task_id} 完成", "原因": "执行", "影响范围": task_id, "执行者": "Codex"},
                {"时间": "2026-07-15T12:01:00+08:00", "变更类型": "核验", "涉及ID": task_id, "变更内容": f"{task_id} 已核验", "原因": "验证", "影响范围": task_id, "执行者": "Codex"},
            ]
        return result

    def test_template_and_actual_match(self):
        (self.project / "evidence.md").write_text("ok", encoding="utf-8")
        (self.project / "5_audit.md").write_text("结论：PASS", encoding="utf-8")
        (self.project / "4_handoff.md").write_text("交接完成", encoding="utf-8")
        rows = self.task_rows(statuses={"P01": "已完成", "P02": "已完成", "P03": "已完成"}, evidence={"P01": "evidence.md", "P02": "evidence.md", "P03": "evidence.md"})
        self.write(self.checklist(rows, history=self.history(["P01", "P02", "P03"])))
        result = replay.replay_project(self.skill, self.project)
        self.assertEqual(result["replay_status"], "COMPLETE")
        self.assertTrue(all(not value for value in result["differences"].values()))

    def test_added_task(self):
        self.write(self.checklist(self.task_rows(order=["P01", "P02", "P03", "P02A"])))
        self.assertEqual(replay.replay_project(self.skill, self.project)["differences"]["added_tasks"], ["P02A"])

    def test_missing_task(self):
        self.write(self.checklist(self.task_rows(order=["P01", "P03"])))
        self.assertEqual(replay.replay_project(self.skill, self.project)["differences"]["missing_tasks"], ["P02"])

    def test_skipped_task(self):
        self.write(self.checklist(self.task_rows(statuses={"P02": "已跳过"})))
        self.assertEqual(replay.replay_project(self.skill, self.project)["differences"]["skipped_tasks"], ["P02"])

    def test_deprecated_replaced_task(self):
        history = [{"时间": "—", "变更类型": "任务替代", "涉及ID": "P02", "变更内容": "P02 被 P02A 替代", "原因": "流程变化", "影响范围": "P02", "执行者": "Codex"}]
        self.write(self.checklist(self.task_rows(statuses={"P02": "已废弃"}, order=["P01", "P02", "P03", "P02A"]), history=history))
        result = replay.replay_project(self.skill, self.project)
        self.assertEqual(result["differences"]["deprecated_tasks"], ["P02"])
        self.assertEqual(result["differences"]["added_tasks"], ["P02A"])

    def test_reordered_tasks(self):
        self.write(self.checklist(self.task_rows(order=["P02", "P01", "P03"])))
        self.assertTrue(replay.replay_project(self.skill, self.project)["differences"]["reordered_tasks"])

    def test_changed_dependency_owner_and_completion_requirement(self):
        self.write(self.checklist(self.task_rows(dependencies={"P02": "P03"}, owners={"P02": "Auditor"}), completion_column=True))
        differences = replay.replay_project(self.skill, self.project)["differences"]
        self.assertEqual(differences["changed_dependencies"][0]["task_id"], "P02")
        self.assertEqual(differences["changed_owners"][0]["task_id"], "P02")
        self.assertEqual(differences["changed_completion_requirements"][0]["task_id"], "P02")

    def test_module_baseline(self):
        self.write(self.checklist(self.task_rows(module=True), modules=[self.module()]))
        self.assertEqual(replay.replay_project(self.skill, self.project)["expected_task_ids"], ["P01", "P02", "P03", "T01", "T02"])

    def test_missing_evidence_is_partial(self):
        self.write(self.checklist(self.task_rows(statuses={"P01": "已完成"})))
        result = replay.replay_project(self.skill, self.project)
        self.assertEqual(result["replay_status"], "PARTIAL")
        self.assertTrue(any(item["type"] == "missing_completion_evidence" for item in result["differences"]["evidence_gaps"]))

    def test_explicit_known_non_blocking_debt_is_partial_known_non_blocking(self):
        rows = self.task_rows(order=["P01", "P03", "F1-07"])
        metadata_debt = {
            "known_non_blocking_debts": [
                {
                    "debt_id": "GENERIC_BASELINE_DRIFT",
                    "status": "KNOWN_NON_BLOCKING",
                    "description": "historical generic-project baseline differs from the current task inventory",
                    "difference_keys": ["added_tasks", "missing_tasks", "reordered_tasks"],
                    "evidence_refs": ["5_audit.md"],
                }
            ]
        }
        self.write(self.checklist(rows, extra=metadata_debt))
        result = replay.replay_project(self.skill, self.project)
        self.assertEqual(result["replay_status"], "PARTIAL_KNOWN_NON_BLOCKING")
        self.assertEqual(result["known_non_blocking_debts"][0]["debt_id"], "GENERIC_BASELINE_DRIFT")
        self.assertTrue(any("GENERIC_BASELINE_DRIFT" in warning for warning in result["warnings"]))

    def test_uncovered_difference_does_not_become_known_non_blocking(self):
        rows = self.task_rows(order=["P01", "P02", "P03", "F1-07"])
        history = self.history(["P01", "P02", "P03", "F1-07"])
        history.append(
            {
                "时间": "—",
                "变更类型": "阻塞",
                "涉及ID": "P01",
                "变更内容": "历史阻塞事件",
                "原因": "兼容性夹具",
                "影响范围": "P01",
                "执行者": "Codex",
            }
        )
        metadata_debt = {
            "known_non_blocking_debts": [
                {
                    "debt_id": "ONLY_ADDED_TASKS",
                    "status": "KNOWN_NON_BLOCKING",
                    "difference_keys": ["added_tasks"],
                }
            ]
        }
        self.write(self.checklist(rows, history=history, extra=metadata_debt))
        result = replay.replay_project(self.skill, self.project)
        self.assertEqual(result["replay_status"], "COMPLETE")
        self.assertEqual(result["known_non_blocking_debts"], [])

    def test_rework_event(self):
        history = [{"时间": "—", "变更类型": "返工", "涉及ID": "P02", "变更内容": "P02 重新进入进行中", "原因": "验证失败", "影响范围": "P02", "执行者": "Codex"}]
        self.write(self.checklist(self.task_rows(), history=history))
        self.assertEqual(replay.replay_project(self.skill, self.project)["differences"]["rework_events"][0]["task_ids"], ["P02"])

    def test_block_and_release_events(self):
        history = [
            {"时间": "—", "变更类型": "阻塞", "涉及ID": "P02", "变更内容": "等待依赖", "原因": "外部条件", "影响范围": "P02", "执行者": "Codex"},
            {"时间": "—", "变更类型": "解除阻塞", "涉及ID": "P02", "变更内容": "依赖已满足", "原因": "恢复", "影响范围": "P02", "执行者": "Codex"},
        ]
        self.write(self.checklist(self.task_rows(statuses={"P02": "阻塞"}), history=history))
        self.assertEqual(len(replay.replay_project(self.skill, self.project)["differences"]["blocking_events"]), 3)

    def test_template_digest_mismatch(self):
        text = self.checklist(self.task_rows())
        self.template.write_text(self.template.read_text(encoding="utf-8") + "changed\n", encoding="utf-8")
        self.write(text)
        self.assertEqual(replay.replay_project(self.skill, self.project)["replay_status"], "DIGEST_MISMATCH")

    def test_module_digest_mismatch(self):
        binding = self.module()
        text = self.checklist(self.task_rows(module=True), modules=[binding])
        path = self.skill / "templates/workflow/modules/testing/1.0.0.md"
        path.write_text(path.read_text(encoding="utf-8") + "changed\n", encoding="utf-8")
        self.write(text)
        self.assertEqual(replay.replay_project(self.skill, self.project)["replay_status"], "DIGEST_MISMATCH")

    def test_insufficient_baseline(self):
        registry_path = self.skill / "templates/workflow/template_registry.json"
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        directory = self.skill / "templates/workflow/task-types/empty-template"
        directory.mkdir()
        path = directory / "1.0.0.md"
        fence = chr(96) * 3
        path.write_text("<!-- BEGIN TEMPLATE METADATA -->\n" + fence + 'json\n{"artifact_type":"task-template","template_id":"empty-template","version":"1.0.0","status":"FORMAL","workflow_schema_version":1}\n' + fence + "\n<!-- END TEMPLATE METADATA -->\n# Empty\n", encoding="utf-8")
        registry["templates"].append({"id": "empty-template", "current_version": "1.0.0", "lifecycle": "FORMAL", "name": "Empty", "keywords": [], "digest": contracts.file_digest(path)})
        registry_path.write_text(contracts.canonical_json(registry), encoding="utf-8")
        self.template, self.digest = path, contracts.file_digest(path)
        self.selection.update(template_id="empty-template", template_digest=self.digest)
        self.write(self.checklist(self.task_rows()))
        self.assertEqual(replay.replay_project(self.skill, self.project)["replay_status"], "INSUFFICIENT_BASELINE")

    def test_invalid_checklist(self):
        (self.project / contracts.CHECKLIST_NAME).write_text("broken", encoding="utf-8")
        self.assertEqual(replay.replay_project(self.skill, self.project)["replay_status"], "INVALID_CHECKLIST")

    def test_incomplete_execution_order_is_partial(self):
        history = [{"时间": "—", "变更类型": "完成", "涉及ID": "P01", "变更内容": "完成", "原因": "执行", "影响范围": "P01", "执行者": "Codex"}]
        self.write(self.checklist(self.task_rows(), history=history))
        result = replay.replay_project(self.skill, self.project)
        self.assertEqual(result["replay_status"], "PARTIAL")
        self.assertTrue(result["execution_sequence"])

    def test_registry_key_order_is_irrelevant(self):
        self.write(self.checklist(self.task_rows()))
        first = replay.replay_project(self.skill, self.project)
        registry_path = self.skill / "templates/workflow/template_registry.json"
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        registry_path.write_text(json.dumps(registry, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        self.assertEqual(first, replay.replay_project(self.skill, self.project))

    def test_repeated_runs_are_identical(self):
        self.write(self.checklist(self.task_rows()))
        self.assertEqual(replay.replay_project(self.skill, self.project), replay.replay_project(self.skill, self.project))

    def test_cli_is_read_only(self):
        self.write(self.checklist(self.task_rows()))
        before = {path: path.read_bytes() for path in self.project.rglob("*") if path.is_file()}
        command = [sys.executable, str(ROOT / "scripts/workflow_replay.py"), "--project-root", str(self.project), "--skill-root", str(self.skill), "--format", "summary"]
        completed = subprocess.run(command, capture_output=True, text=True, check=False)
        self.assertEqual(completed.returncode, 0)
        self.assertIn("Replay status:", completed.stdout)
        after = {path: path.read_bytes() for path in self.project.rglob("*") if path.is_file()}
        self.assertEqual(before, after)

    def test_no_external_model_or_network_references(self):
        source = (ROOT / "scripts/workflow_replay.py").read_text(encoding="utf-8").lower()
        forbidden = ("requests" + ".", "url" + "lib", "ag" + "nes", "http" + "://", "https" + "://")
        for token in forbidden:
            self.assertNotIn(token, source)


if __name__ == "__main__":
    unittest.main()
