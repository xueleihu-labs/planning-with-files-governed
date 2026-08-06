#!/usr/bin/env python3
"""Deterministic v0.8.0 workflow contract tests."""

from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import workflow_contracts as contracts  # noqa: E402


class WorkflowContractTests(unittest.TestCase):
    def test_cross_platform_digest_and_bom(self) -> None:
        self.assertEqual(contracts.sha256_digest("中文\r\n\r\n动作\r"), contracts.sha256_digest("中文\n\n动作\n"))
        self.assertEqual(contracts.sha256_digest("\ufeffa\r\n"), contracts.sha256_digest("a\n"))

    def test_candidate_id_is_fixed_format_and_stable(self) -> None:
        now = dt.datetime(2026, 7, 15, tzinfo=dt.timezone.utc)
        payload = {"candidate_schema_version": 1, "source_template_id": "skill-create", "source_template_version": "1.0.0", "status": "PROPOSED"}
        value = contracts.candidate_id(payload, now)
        self.assertRegex(value, r"^cand-20260715-[0-9a-f]{12}$")
        contracts.validate_candidate_id(value)
        with self.assertRaises(contracts.ContractError):
            contracts.validate_candidate_id("cand-20260715-001")

    def test_machine_blocks_round_trip(self) -> None:
        value = {"workflow_schema_version": 1, "project_id": "demo", "unknown": {"keep": True}}
        rendered = contracts.render_machine_block("workflow", value)
        self.assertEqual(contracts.extract_machine_json(rendered, "workflow"), value)
        self.assertIn("BEGIN WORKFLOW METADATA", rendered)

    def test_template_artifact_types_are_distinct_and_validated(self) -> None:
        base = {"workflow_schema_version": 1, "version": "1.0.0", "status": "FORMAL", "template_id": "generic-project"}
        contracts.validate_template_metadata({**base, "artifact_type": "task-template"})
        contracts.validate_template_metadata({**base, "artifact_type": "workflow-module", "module_id": "testing"})
        with self.assertRaises(contracts.ContractError):
            contracts.validate_template_metadata({**base, "artifact_type": "candidate"})

    def test_version_bumps_are_deterministic(self) -> None:
        self.assertEqual(contracts.bump_semver("1.2.3", "PATCH"), "1.2.4")
        self.assertEqual(contracts.bump_semver("1.2.3", "MINOR"), "1.3.0")
        self.assertEqual(contracts.bump_semver("1.2.3", "MAJOR"), "2.0.0")

    def test_merge_writeback_preserves_unknown_fields_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "data.json"
            path.write_text('{"known": 1, "future_field": {"x": true}}\n', encoding="utf-8")
            contracts.merge_json_file(path, {"known": 2})
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"known": 2, "future_field": {"x": True}})
            before = path.read_bytes()
            contracts.merge_json_file(path, {"known": 2})
            self.assertEqual(path.read_bytes(), before)

    def test_registry_projection_is_deterministic(self) -> None:
        registry = {
            "registry_schema_version": 1,
            "templates": [{"id": "generic-project", "current_version": "1.0.0", "lifecycle": "FORMAL", "name": "通用", "keywords": ["项目"]}],
            "modules": [{"id": "testing", "current_version": "1.0.0", "lifecycle": "FORMAL", "name": "测试", "keywords": ["验证"]}],
        }
        first = contracts.registry_markdown(registry)
        second = contracts.registry_markdown(json.loads(json.dumps(registry, sort_keys=True)))
        self.assertEqual(first, second)

    def test_windows_safe_filenames(self) -> None:
        now = dt.datetime(2026, 7, 15, 13, 5, tzinfo=dt.timezone.utc)
        self.assertEqual(contracts.conflict_filename(now, "a1b2c3d4"), "workflow-conflict-20260715T130500Z-a1b2c3d4.md")
        self.assertEqual(contracts.stale_lock_recovery_filename(now), "stale-lock-recovery-20260715T130500Z.md")
        self.assertNotIn(":", contracts.conflict_filename(now))

    def test_stale_lock_same_host_requires_dead_process(self) -> None:
        now = dt.datetime(2026, 7, 15, 13, 10, tzinfo=dt.timezone.utc)
        lock = {
            "lock_schema_version": 1,
            "lock_owner": "test",
            "agent_id": "Codex",
            "process_id": 99999999,
            "host_name": "test-host",
            "created_at": "2026-07-15T13:00:00Z",
            "heartbeat_at": "2026-07-15T13:00:00Z",
            "target_file": "WORKFLOW_CHECKLIST.md",
            "base_digest": "a" * 64,
        }
        stale, reason = contracts.lock_is_stale(lock, now, host_name="test-host")
        self.assertTrue(stale)
        self.assertIn("process is absent", reason)

    def test_cross_host_stale_lock_is_not_auto_recoverable(self) -> None:
        now = dt.datetime(2026, 7, 15, 13, 10, tzinfo=dt.timezone.utc)
        lock = {
            "lock_schema_version": 1,
            "lock_owner": "test",
            "agent_id": "Codex",
            "process_id": 99999999,
            "host_name": "other-host",
            "created_at": "2026-07-15T13:00:00Z",
            "heartbeat_at": "2026-07-15T13:00:00Z",
            "target_file": "WORKFLOW_CHECKLIST.md",
            "base_digest": "a" * 64,
        }
        stale, reason = contracts.lock_is_stale(lock, now, host_name="local-host")
        self.assertFalse(stale)
        self.assertIn("cross-host", reason)

    def test_lock_lifecycle_and_conflict_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            lock_path = root / ".planning" / "workflow.lock"
            lock = contracts.new_lock("WORKFLOW_CHECKLIST.md", "a" * 64, "Codex", host_name="test-host")
            contracts.write_lock(lock_path, lock)
            contracts.heartbeat_lock(lock_path, dt.datetime(2026, 7, 15, 13, 1, tzinfo=dt.timezone.utc))
            loaded = json.loads(lock_path.read_text(encoding="utf-8"))
            self.assertEqual(loaded["heartbeat_at"], "2026-07-15T13:01:00Z")
            report = contracts.write_conflict_report(root / ".planning" / "conflicts", {
                "conflict_id": "conflict-001",
                "target_file": "WORKFLOW_CHECKLIST.md",
                "base_digest": "a" * 64,
                "current_digest": "b" * 64,
                "conflict_reason": "base digest changed",
                "affected_task_ids": ["P01"],
                "recommended_handling": "人工合并",
            }, dt.datetime(2026, 7, 15, 13, 5, tzinfo=dt.timezone.utc))
            self.assertEqual(report.name, "workflow-conflict-20260715T130500Z-ab0d924b.md")
            self.assertEqual(contracts.extract_machine_json(report.read_text(encoding="utf-8"), "conflict")["status"], "OPEN")
            self.assertTrue(contracts.release_lock(lock_path, process_id=lock["process_id"], host_name="test-host"))

    def test_active_workflow_lock_creates_conflict_without_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            lock_path = root / ".planning" / "workflow.lock"
            conflicts = root / ".planning" / "conflicts"
            lock = contracts.new_lock("WORKFLOW_CHECKLIST.md", "a" * 64, "Codex", process_id=os.getpid(), host_name=contracts.socket.gethostname())
            contracts.write_lock(lock_path, lock)
            with self.assertRaises(contracts.ContractError):
                contracts.acquire_workflow_lock(lock_path, "WORKFLOW_CHECKLIST.md", "a" * 64, "Other", conflicts)
            self.assertTrue(list(conflicts.glob("workflow-conflict-*.md")))
            self.assertEqual(json.loads(lock_path.read_text(encoding="utf-8"))["agent_id"], "Codex")


if __name__ == "__main__":
    unittest.main()
