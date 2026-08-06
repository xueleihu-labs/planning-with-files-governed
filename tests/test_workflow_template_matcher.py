#!/usr/bin/env python3
"""P2-01 deterministic local template matcher tests."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import workflow_contracts as contracts  # noqa: E402
import workflow_template_matcher as matcher  # noqa: E402


class WorkflowTemplateMatcherTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.skill_root = self.root / "skill"
        shutil.copytree(ROOT / "templates", self.skill_root / "templates")
        self.project = self.root / "project"
        self.project.mkdir()

    def tearDown(self) -> None:
        self.directory.cleanup()

    def match(self, text: str = "", explicit: str | None = None) -> dict[str, object]:
        return matcher.identify_template(self.skill_root, self.project, project_name=self.project.name, source_text=text, explicit_template_id=explicit)

    def test_explicit_template_wins(self) -> None:
        result = self.match("优化 Skill", "skill-create")
        self.assertEqual(result["template_id"], "skill-create")
        self.assertEqual(result["match_method"], "explicit")
        self.assertEqual(result["confidence"], 1.0)

    def test_existing_binding_wins_over_rules(self) -> None:
        metadata = {
            "workflow_schema_version": 1,
            "project_id": "project",
            "checklist_version": "1.0.0",
            "template": {
                "template_id": "generic-project",
                "template_version": "1.0.0",
                "template_digest": "89407394a0412da37d6c96d198b75372190498ae0a51af37494961e781b3082b",
            },
            "modules": [],
            "current_phase": "P01",
            "overall_status": "未开始",
            "owner_agent": "Codex",
            "last_updated_at": "2026-07-15T12:00:00+08:00",
            "recommended_next_task": "P01",
        }
        (self.project / "WORKFLOW_CHECKLIST.md").write_text(contracts.render_machine_block("workflow", metadata), encoding="utf-8")
        result = self.match("创建 Skill 新建 SKILL.md")
        self.assertEqual(result["template_id"], "generic-project")
        self.assertEqual(result["match_method"], "binding")

    def test_explicit_template_wins_over_existing_binding(self) -> None:
        metadata = {
            "workflow_schema_version": 1,
            "project_id": "project",
            "checklist_version": "1.0.0",
            "template": {
                "template_id": "generic-project",
                "template_version": "1.0.0",
                "template_digest": "89407394a0412da37d6c96d198b75372190498ae0a51af37494961e781b3082b",
            },
            "modules": [],
            "current_phase": "P01",
            "overall_status": "未开始",
            "owner_agent": "Codex",
            "last_updated_at": "2026-07-15T12:00:00+08:00",
            "recommended_next_task": "P01",
        }
        (self.project / "WORKFLOW_CHECKLIST.md").write_text(contracts.render_machine_block("workflow", metadata), encoding="utf-8")
        result = self.match("完全不同的目标", "skill-create")
        self.assertEqual(result["template_id"], "skill-create")
        self.assertEqual(result["match_method"], "explicit")

    def test_skill_create_rule_match(self) -> None:
        (self.project / "SKILL.md").write_text("placeholder", encoding="utf-8")
        result = self.match("创建 Skill，新建 SKILL.md")
        self.assertEqual(result["template_id"], "skill-create")
        self.assertEqual(result["match_method"], "rule")
        self.assertGreater(result["confidence"], 0.0)

    def test_no_signal_falls_back_to_generic(self) -> None:
        result = self.match("完全没有匹配信号")
        self.assertEqual(result["template_id"], "generic-project")
        self.assertEqual(result["match_method"], "fallback")
        self.assertTrue(result["fallback_used"])

    def test_exclusion_blocks_skill_create(self) -> None:
        result = self.match("优化 Skill，新建 SKILL.md")
        self.assertEqual(result["template_id"], "generic-project")
        self.assertIn("skill-create", result["excluded_templates"])

    def test_deprecated_template_is_not_newly_selected(self) -> None:
        template_path = self.skill_root / "templates" / "workflow" / "task-types" / "old-special" / "0.9.0.md"
        template_path.parent.mkdir(parents=True)
        current = (self.skill_root / "templates" / "workflow" / "task-types" / "skill-create" / "1.0.0.md").read_text(encoding="utf-8")
        template_path.write_text(current.replace('"version": "1.0.0"', '"version": "0.9.0"').replace('"status": "EXPERIMENTAL"', '"status": "DEPRECATED"'), encoding="utf-8")
        registry_path = self.skill_root / "templates" / "workflow" / "template_registry.json"
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        registry["templates"].append({
            "id": "old-special",
            "current_version": "0.9.0",
            "lifecycle": "DEPRECATED",
            "lifecycle_status": "DEPRECATED",
            "name": "旧模板",
            "keywords": ["特殊旧任务"],
            "exclude_keywords": [],
            "digest": contracts.file_digest(template_path),
        })
        registry_path.write_text(contracts.canonical_json(registry), encoding="utf-8")
        result = self.match("特殊旧任务")
        self.assertEqual(result["template_id"], "generic-project")
        self.assertIn(result["match_method"], {"rule", "fallback"})

    def test_bound_old_template_version_is_preserved(self) -> None:
        old_path = self.skill_root / "templates" / "workflow" / "task-types" / "skill-create" / "0.9.0.md"
        current = (self.skill_root / "templates" / "workflow" / "task-types" / "skill-create" / "1.0.0.md").read_text(encoding="utf-8")
        old_path.write_text(current.replace('"version": "1.0.0"', '"version": "0.9.0"'), encoding="utf-8")
        metadata = {
            "workflow_schema_version": 1,
            "project_id": "project",
            "checklist_version": "1.0.0",
            "template": {"template_id": "skill-create", "template_version": "0.9.0", "template_digest": contracts.file_digest(old_path)},
            "modules": [],
            "current_phase": "P01",
            "overall_status": "未开始",
            "owner_agent": "Codex",
            "last_updated_at": "2026-07-15T12:00:00+08:00",
            "recommended_next_task": "P01",
        }
        (self.project / "WORKFLOW_CHECKLIST.md").write_text(contracts.render_machine_block("workflow", metadata), encoding="utf-8")
        result = self.match("完全不同的目标")
        self.assertEqual(result["template_id"], "skill-create")
        self.assertEqual(result["template_version"], "0.9.0")
        self.assertEqual(result["match_method"], "binding")

    def test_same_input_and_registry_key_order_are_stable(self) -> None:
        first = self.match("创建 Skill，新建 SKILL.md")
        registry_path = self.skill_root / "templates" / "workflow" / "template_registry.json"
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        registry_path.write_text(json.dumps(registry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        second = self.match("创建 Skill，新建 SKILL.md")
        self.assertEqual(first, second)

    def test_digest_mismatch_is_an_explicit_error(self) -> None:
        template_path = self.skill_root / "templates" / "workflow" / "task-types" / "skill-create" / "1.0.0.md"
        template_path.write_text(template_path.read_text(encoding="utf-8") + "\nchanged\n", encoding="utf-8")
        with self.assertRaises(matcher.TemplateMatchError):
            self.match("创建 Skill", "skill-create")

    def test_match_result_has_only_local_methods(self) -> None:
        result = self.match("创建 Skill")
        self.assertIn(result["match_method"], {"explicit", "binding", "rule", "fallback"})
        self.assertNotIn("semantic", result)
        self.assertNotIn("model", result)
        self.assertNotIn("llm", result)


if __name__ == "__main__":
    unittest.main()
