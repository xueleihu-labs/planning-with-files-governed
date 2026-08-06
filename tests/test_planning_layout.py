from __future__ import annotations

import hashlib
import os
import tempfile
import unittest
from pathlib import Path

import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import planning_layout as layout  # noqa: E402


class PlanningLayoutTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="pwf-layout-")
        self.root = Path(self.tmp.name) / "project 中文 with spaces"
        self.root.mkdir()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def write_legacy(self, name: str, content: str) -> Path:
        path = self.root / name
        path.write_text(content, encoding="utf-8")
        return path

    def write_canonical(self, name: str, content: str) -> Path:
        directory = self.root / layout.CANONICAL_DIR_NAME
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / name
        path.write_text(content, encoding="utf-8")
        return path

    def test_init_layout_defaults_to_visible_task_directory(self) -> None:
        selected = layout.layout_for_init(self.root, mode="new", task_id="task-one")
        self.assertEqual(selected.planning_dir, (self.root / layout.CANONICAL_DIR_NAME / "task-one").resolve())
        self.assertEqual(selected.task_id, "task-one")
        self.assertFalse(selected.is_legacy)

    def test_new_task_requires_explicit_task_id(self) -> None:
        with self.assertRaises(layout.LayoutError) as caught:
            layout.layout_for_init(self.root, mode="new")
        self.assertEqual(caught.exception.code, "TASK_ID_REQUIRED")

    def test_new_tasks_cannot_bypass_visible_task_directory(self) -> None:
        with self.assertRaises(layout.LayoutError) as caught:
            layout.layout_for_init(self.root, mode="new", planning_dir="规划 with spaces")
        self.assertEqual(caught.exception.code, "TASK_ID_REQUIRED")

    def test_legacy_layout_is_detected_without_migration(self) -> None:
        self.write_legacy("1_master_plan.md", "legacy\n")
        selected = layout.resolve_layout(self.root, require=True)
        self.assertIsNotNone(selected)
        assert selected is not None
        self.assertTrue(selected.is_legacy)
        self.assertEqual(selected.path("1_master_plan.md"), (self.root / "1_master_plan.md").resolve())
        self.assertFalse((self.root / layout.CANONICAL_DIR_NAME).exists())

    def test_new_layout_wins_when_legacy_is_identical(self) -> None:
        self.write_legacy("1_master_plan.md", "same\n")
        self.write_canonical("1_master_plan.md", "same\n")
        selected = layout.resolve_layout(self.root, require=True)
        self.assertIsNotNone(selected)
        assert selected is not None
        self.assertTrue(selected.is_canonical)

    def test_conflicting_new_and_legacy_layout_fails_closed(self) -> None:
        self.write_legacy("1_master_plan.md", "legacy\n")
        self.write_canonical("1_master_plan.md", "new\n")
        with self.assertRaises(layout.LayoutConflict) as caught:
            layout.resolve_layout(self.root, require=True)
        self.assertIn("1_master_plan.md", str(caught.exception))
        self.assertEqual(caught.exception.code, "LAYOUT_CONFLICT")

    def test_explicit_outside_and_traversal_paths_are_rejected(self) -> None:
        with self.assertRaises(layout.UnsafeLayoutPath):
            layout.layout_for_init(self.root, mode="adopt", planning_dir="../outside")
        with self.assertRaises(layout.UnsafeLayoutPath):
            layout.layout_for_init(self.root, mode="adopt", planning_dir=str(Path(self.tmp.name).parent))
        for value in ("", "..", "a/b", "a\\b", "../escape", ".hidden"):
            with self.assertRaises(layout.LayoutError):
                layout.validate_plan_id(value)

    def test_symlink_escape_is_rejected(self) -> None:
        outside = Path(self.tmp.name) / "outside"
        outside.mkdir()
        link = self.root / layout.CANONICAL_DIR_NAME
        try:
            os.symlink(outside, link, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation unavailable")
        with self.assertRaises(layout.UnsafeLayoutPath):
            layout.resolve_layout(self.root, require=False)

    def test_child_discovery_stops_at_git_boundary(self) -> None:
        outer = Path(self.tmp.name) / "outer"
        inner = outer / "inner"
        child = inner / "src" / "nested"
        (outer / layout.CANONICAL_DIR_NAME).mkdir(parents=True)
        (outer / layout.CANONICAL_DIR_NAME / "1_master_plan.md").write_text("outer\n", encoding="utf-8")
        (inner / ".git").mkdir(parents=True)
        child.mkdir(parents=True)
        self.assertEqual(layout.discover_project_root(child), inner.resolve())

    def create_task(self, task_id: str, content: str = "# task\n") -> Path:
        task = self.root / layout.CANONICAL_DIR_NAME / task_id
        task.mkdir(parents=True, exist_ok=True)
        (task / "task_plan.md").write_text(content, encoding="utf-8")
        existing = layout.read_task_index(self.root, require=False)
        existing[task_id] = task_id
        layout.write_task_index(self.root, existing)
        return task

    def test_single_task_resolves_from_root_and_task_child(self) -> None:
        task = self.create_task("task-one")
        child = task / "evidence"
        child.mkdir()
        selected = layout.resolve_layout(self.root, require=True)
        self.assertEqual(selected.task_id, "task-one")
        self.assertEqual(selected.planning_dir, task.resolve())
        from_child = layout.resolve_layout(self.root, start=child, require=True)
        self.assertEqual(from_child.task_id, "task-one")

    def test_multiple_tasks_require_explicit_selection(self) -> None:
        first = self.create_task("task-one")
        second = self.create_task("task-two")
        with self.assertRaises(layout.LayoutError) as caught:
            layout.resolve_layout(self.root, require=True)
        self.assertEqual(caught.exception.code, "TASK_SELECTION_REQUIRED")
        selected = layout.resolve_layout(self.root, task_id="task-two", require=True)
        self.assertEqual(selected.planning_dir, second.resolve())
        selected_from_child = layout.resolve_layout(self.root, start=first / "evidence", require=True)
        self.assertEqual(selected_from_child.task_id, "task-one")

    def test_task_index_drift_fails_closed(self) -> None:
        self.create_task("task-one")
        layout.write_task_index(self.root, {"missing-task": "missing-task"})
        with self.assertRaises(layout.LayoutError) as caught:
            layout.resolve_layout(self.root, task_id="task-one", require=True)
        self.assertEqual(caught.exception.code, "TASK_INDEX_CONFLICT")

    def test_empty_task_index_drift_fails_closed(self) -> None:
        self.create_task("task-one")
        layout.write_task_index(self.root, {})
        with self.assertRaises(layout.LayoutError) as caught:
            layout.resolve_layout(self.root, require=True)
        self.assertEqual(caught.exception.code, "TASK_INDEX_CONFLICT")

    def test_task_import_is_copy_only_hash_verified_and_rollback_safe(self) -> None:
        source = Path(self.tmp.name) / "external-task"
        (source / "evidence").mkdir(parents=True)
        (source / "task_plan.md").write_text("# imported\n", encoding="utf-8")
        (source / "evidence" / "result.json").write_text("{}\n", encoding="utf-8")
        before = {path.relative_to(source): hashlib.sha256(path.read_bytes()).hexdigest() for path in source.rglob("*") if path.is_file()}
        preview = layout.import_task_package(self.root, source, "imported-task")
        self.assertEqual(preview["mode"], "DRY_RUN")
        self.assertFalse((self.root / layout.CANONICAL_DIR_NAME).exists())
        with self.assertRaises(RuntimeError):
            layout.import_task_package(self.root, source, "imported-task", apply=True, confirm=True, failure_after=1)
        self.assertFalse((self.root / layout.CANONICAL_DIR_NAME / "imported-task").exists())
        applied = layout.import_task_package(self.root, source, "imported-task", apply=True, confirm=True)
        self.assertTrue(applied["source_unchanged"])
        target = self.root / layout.CANONICAL_DIR_NAME / "imported-task"
        self.assertTrue((target / "evidence" / "migration" / "source-manifest.json").is_file())
        after = {path.relative_to(source): hashlib.sha256(path.read_bytes()).hexdigest() for path in source.rglob("*") if path.is_file()}
        self.assertEqual(before, after)
        self.assertEqual(layout.resolve_layout(self.root, require=True).task_id, "imported-task")

    def test_task_import_rejects_runtime_and_secret_material(self) -> None:
        source = Path(self.tmp.name) / "unsafe-task"
        source.mkdir()
        (source / "task_plan.md").write_text("# task\n", encoding="utf-8")
        (source / ".env").write_text("SECRET=redacted\n", encoding="utf-8")
        with self.assertRaises(layout.LayoutError) as caught:
            layout.import_task_package(self.root, source, "unsafe-task")
        self.assertEqual(caught.exception.code, "UNSAFE_IMPORT_CONTENT")

    def test_task_import_rejects_symlinked_source_boundary(self) -> None:
        source = Path(self.tmp.name) / "real-task"
        source.mkdir()
        (source / "task_plan.md").write_text("# task\n", encoding="utf-8")
        link = Path(self.tmp.name) / "linked-task"
        try:
            link.symlink_to(source, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation unavailable")
        with self.assertRaises(layout.UnsafeLayoutPath):
            layout.import_task_package(self.root, link, "linked-task")

    def test_migration_dry_run_has_zero_writes_and_protects_business_files(self) -> None:
        source = self.write_legacy("1_master_plan.md", "plan\n")
        checklist = self.write_legacy("WORKFLOW_CHECKLIST.md", "checklist\n")
        readme = self.write_legacy("README.md", "business README\n")
        before = {path: path.read_bytes() for path in (source, checklist, readme)}
        result = layout.migrate_layout(self.root)
        self.assertEqual(result["mode"], "DRY_RUN")
        self.assertEqual(result["writes"], 0)
        self.assertFalse((self.root / layout.CANONICAL_DIR_NAME).exists())
        self.assertEqual({path: path.read_bytes() for path in before}, before)

    def test_migration_apply_preserves_hashes_and_is_idempotent(self) -> None:
        files = {
            "1_master_plan.md": "plan\n",
            "2_execution_log.md": "log\n",
            "WORKFLOW_CHECKLIST.md": "checklist\n",
        }
        original = {}
        for name, content in files.items():
            path = self.write_legacy(name, content)
            original[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        self.write_legacy("README.md", "do not move\n")
        applied = layout.migrate_layout(self.root, apply=True, confirm=True)
        self.assertEqual(applied["mode"], "APPLY")
        for name, digest in original.items():
            target = self.root / layout.CANONICAL_DIR_NAME / name
            self.assertTrue(target.is_file())
            self.assertEqual(hashlib.sha256(target.read_bytes()).hexdigest(), digest)
            self.assertFalse((self.root / name).exists())
        self.assertTrue((self.root / "README.md").exists())
        repeated = layout.migrate_layout(self.root, apply=True, confirm=True)
        self.assertEqual(repeated["status"], "NOOP")
        self.assertTrue(repeated["idempotent"])

    def test_migration_requires_explicit_confirmation(self) -> None:
        self.write_legacy("1_master_plan.md", "plan\n")
        with self.assertRaises(layout.LayoutError) as caught:
            layout.migrate_layout(self.root, apply=True)
        self.assertEqual(caught.exception.code, "CONFIRM_REQUIRED")
        self.assertTrue((self.root / "1_master_plan.md").exists())

    def test_migration_conflict_does_not_overwrite_destination(self) -> None:
        source = self.write_legacy("1_master_plan.md", "legacy\n")
        self.write_canonical("1_master_plan.md", "different\n")
        with self.assertRaises(layout.LayoutConflict):
            layout.migrate_layout(self.root, apply=True, confirm=True)
        self.assertEqual(source.read_text(encoding="utf-8"), "legacy\n")
        self.assertEqual((self.root / layout.CANONICAL_DIR_NAME / "1_master_plan.md").read_text(encoding="utf-8"), "different\n")

    def test_failed_migration_rolls_back_already_moved_files(self) -> None:
        self.write_legacy("1_master_plan.md", "one\n")
        self.write_legacy("2_execution_log.md", "two\n")
        with self.assertRaises(RuntimeError):
            layout.migrate_layout(self.root, apply=True, confirm=True, failure_after=1)
        self.assertTrue((self.root / "1_master_plan.md").exists())
        self.assertTrue((self.root / "2_execution_log.md").exists())
        self.assertFalse((self.root / layout.CANONICAL_DIR_NAME / "1_master_plan.md").exists())
        self.assertFalse((self.root / layout.CANONICAL_DIR_NAME / "2_execution_log.md").exists())


if __name__ == "__main__":
    unittest.main()
