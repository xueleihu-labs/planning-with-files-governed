#!/usr/bin/env python3
"""P2-02 deterministic workflow-module composition tests."""

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
import workflow_module_composer as composer  # noqa: E402


class WorkflowModuleComposerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.skill_root = self.root / "skill"
        shutil.copytree(ROOT / "templates", self.skill_root / "templates")
        self.project = self.root / "project"
        self.project.mkdir()
        self.template = {"template_id": "skill-create", "template_version": "1.0.0"}

    def tearDown(self) -> None:
        self.directory.cleanup()

    def registry_path(self) -> Path:
        return self.skill_root / "templates" / "workflow" / "template_registry.json"

    def registry(self) -> dict[str, object]:
        return json.loads(self.registry_path().read_text(encoding="utf-8"))

    def save_registry(self, registry: dict[str, object]) -> None:
        self.registry_path().write_text(contracts.canonical_json(registry), encoding="utf-8")

    def add_module(self, module_id: str, *, keywords: list[str] | None = None, lifecycle: str = "FORMAL", requires: list[str] | None = None, conflicts: list[str] | None = None, order: int = 100) -> None:
        module_dir = self.skill_root / "templates" / "workflow" / "modules" / module_id
        module_dir.mkdir(parents=True)
        content = f'''<!-- BEGIN TEMPLATE METADATA -->
```json
{{
  "artifact_type": "workflow-module",
  "module_id": "{module_id}",
  "status": "{lifecycle}",
  "version": "1.0.0",
  "workflow_schema_version": 1
}}
```
<!-- END TEMPLATE METADATA -->

# {module_id}
'''
        path = module_dir / "1.0.0.md"
        path.write_text(content, encoding="utf-8")
        registry = self.registry()
        modules = registry["modules"]
        modules.append({
            "id": module_id,
            "current_version": "1.0.0",
            "lifecycle": lifecycle,
            "lifecycle_status": lifecycle,
            "name": module_id,
            "keywords": keywords or [],
            "exclude_keywords": [],
            "requires_modules": requires or [],
            "conflicts_with": conflicts or [],
            "order": order,
            "digest": contracts.file_digest(path),
        })
        self.save_registry(registry)

    def compose(self, **kwargs: object) -> dict[str, object]:
        return composer.compose_modules(self.skill_root, self.project, self.template, **kwargs)

    def test_template_default_selects_testing(self) -> None:
        result = self.compose()
        self.assertEqual([item["module_id"] for item in result["modules"]], ["testing"])
        self.assertEqual(result["modules"][0]["selection_method"], "template-default")
        self.assertTrue(result["modules"][0]["required"])

    def test_explicit_module_precedes_template_default(self) -> None:
        self.add_module("audit")
        result = self.compose(explicit_module_ids=["audit"])
        self.assertEqual([item["module_id"] for item in result["modules"]], ["audit", "testing"])
        self.assertEqual(result["modules"][0]["selection_method"], "explicit")

    def test_existing_binding_is_preserved_and_precedes_default(self) -> None:
        self.add_module("audit")
        audit_path = self.skill_root / "templates" / "workflow" / "modules" / "audit" / "1.0.0.md"
        existing = [{"module_id": "audit", "module_version": "1.0.0", "module_digest": contracts.file_digest(audit_path), "selection_method": "binding", "required": False}]
        result = self.compose(existing_bindings=existing)
        self.assertEqual([item["module_id"] for item in result["modules"]], ["audit", "testing"])
        self.assertEqual(result["modules"][0]["selection_method"], "binding")

    def test_duplicate_sources_keep_one_module_and_highest_priority(self) -> None:
        result = self.compose(explicit_module_ids=["testing"])
        self.assertEqual(len([item for item in result["modules"] if item["module_id"] == "testing"]), 1)
        self.assertEqual(result["modules"][0]["selection_method"], "explicit")

    def test_deterministic_output_and_registry_key_order(self) -> None:
        self.add_module("audit", keywords=["审计"], order=10)
        first = self.compose(source_text="审计")
        registry = self.registry()
        self.save_registry(json.loads(json.dumps(registry, sort_keys=True)))
        second = self.compose(source_text="审计")
        self.assertEqual(first, second)

    def test_deprecated_module_is_not_automatically_selected(self) -> None:
        self.add_module("legacy", keywords=["旧模块"], lifecycle="DEPRECATED")
        result = self.compose(source_text="旧模块")
        self.assertNotIn("legacy", [item["module_id"] for item in result["modules"]])

    def test_bound_historical_module_version_is_allowed(self) -> None:
        module_dir = self.skill_root / "templates" / "workflow" / "modules" / "testing"
        old_path = module_dir / "0.9.0.md"
        old_path.write_text((module_dir / "1.0.0.md").read_text(encoding="utf-8").replace('"version": "1.0.0"', '"version": "0.9.0"'), encoding="utf-8")
        result = self.compose(existing_bindings=[{"module_id": "testing", "module_version": "0.9.0", "module_digest": contracts.file_digest(old_path), "selection_method": "binding", "required": True}])
        self.assertEqual(result["modules"][0]["module_version"], "0.9.0")
        self.assertEqual(result["modules"][0]["selection_method"], "binding")

    def test_digest_mismatch_and_missing_module_fail(self) -> None:
        registry = self.registry()
        registry["modules"][0]["digest"] = "0" * 64
        self.save_registry(registry)
        with self.assertRaises(composer.ModuleCompositionError):
            self.compose()
        registry = self.registry()
        registry["modules"][0]["digest"] = contracts.file_digest(self.skill_root / "templates" / "workflow" / "modules" / "testing" / "1.0.0.md")
        registry["modules"][0]["current_version"] = "9.9.9"
        self.save_registry(registry)
        with self.assertRaises(composer.ModuleCompositionError):
            self.compose()

    def test_required_dependency_is_added(self) -> None:
        self.add_module("audit", requires=["testing"])
        result = self.compose(explicit_module_ids=["audit"])
        self.assertEqual([item["module_id"] for item in result["modules"]], ["audit", "testing"])
        self.assertTrue(result["modules"][1]["required"])
        self.assertIn("依赖 audit 要求 testing", result["modules"][1]["matched_signals"])

    def test_dependency_cycle_fails(self) -> None:
        self.add_module("a", requires=["b"])
        self.add_module("b", requires=["a"])
        with self.assertRaises(composer.ModuleCompositionError):
            self.compose(explicit_module_ids=["a"])

    def test_same_level_conflict_fails(self) -> None:
        self.add_module("a", conflicts=["b"])
        self.add_module("b", conflicts=["a"])
        with self.assertRaises(composer.ModuleCompositionError):
            self.compose(explicit_module_ids=["a", "b"])

    def test_explicit_exclusion_blocks_automatic_module(self) -> None:
        with self.assertRaises(composer.ModuleCompositionError):
            self.compose(excluded_module_ids=["testing"])

    def test_rule_module_is_added_after_defaults(self) -> None:
        self.add_module("audit", keywords=["审计"])
        result = self.compose(source_text="审计")
        self.assertEqual([item["module_id"] for item in result["modules"]], ["testing", "audit"])
        self.assertEqual(result["modules"][1]["selection_method"], "rule")

    def test_conflict_higher_priority_explicit_wins_over_rule(self) -> None:
        self.add_module("review", keywords=["复核"], conflicts=["audit"])
        self.add_module("audit", keywords=["审计"], conflicts=["review"])
        result = self.compose(source_text="审计 复核", explicit_module_ids=["audit"])
        self.assertEqual([item["module_id"] for item in result["modules"]], ["audit", "testing"])
        self.assertTrue(result["warnings"])

    def test_required_template_module_conflict_fails_explicitly(self) -> None:
        self.add_module("audit", conflicts=["testing"])
        with self.assertRaisesRegex(composer.ModuleCompositionError, "required template module conflicts"):
            self.compose(explicit_module_ids=["audit"])

    def test_result_contains_no_external_selection_method(self) -> None:
        result = self.compose()
        self.assertTrue(all(item["selection_method"] in {"explicit", "binding", "template-default", "rule"} for item in result["modules"]))


if __name__ == "__main__":
    unittest.main()
