#!/usr/bin/env python3
"""Tests for the self-contained, read-only checkpoint boundary."""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import checkpoint_reader as reader  # noqa: E402


def canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


class CheckpointReaderTests(unittest.TestCase):
    def test_missing_head_fails_closed_without_sibling_skill(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project"
            state = Path(directory) / "state"
            root.mkdir()
            result = reader.read_head(root, "task-one", "P01", state)
            self.assertEqual(result["effective_action"], "BLOCKED")
            self.assertEqual(result["reason"], "UNTRUSTED_CHECKPOINT_HEAD")

    def test_valid_published_head_is_verified_and_tamper_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "project"
            state = base / "state"
            root.mkdir()
            task_id, phase_id, commit_id = "task-one", "P01", "CP-task-one-P01-1"
            store = reader._store(root, state)  # type: ignore[attr-defined]
            commit_path = store / "commits" / f"{commit_id}.commit.json"
            result_path = store / "artifacts" / commit_id / "result.json"
            head_path = store / "heads" / f"{task_id}-{phase_id}.json"
            result_path.parent.mkdir(parents=True)
            result_path.write_bytes(b'{"result":"PASS"}\n')
            commit = {
                "effective_action": "ADVANCE_PHASE",
                "result_hash": hashlib.sha256(result_path.read_bytes()).hexdigest(),
            }
            commit_path.parent.mkdir(parents=True)
            commit_path.write_text(json.dumps(commit), encoding="utf-8")
            head = {
                "commit_id": commit_id,
                "commit_hash": hashlib.sha256(canonical(commit)).hexdigest(),
                "head_version": 1,
                "commit_sequence": 1,
            }
            head_path.parent.mkdir(parents=True)
            head_path.write_text(json.dumps(head), encoding="utf-8")

            verified = reader.read_head(root, task_id, phase_id, state)
            self.assertEqual(verified["source"], "PUBLISHED_COMMIT")
            self.assertEqual(verified["effective_action"], "ADVANCE_PHASE")

            result_path.write_bytes(b'{"result":"TAMPERED"}\n')
            blocked = reader.read_head(root, task_id, phase_id, state)
            self.assertEqual(blocked["effective_action"], "BLOCKED")
            self.assertEqual(blocked["reason"], "UNTRUSTED_CHECKPOINT_HEAD")


if __name__ == "__main__":
    unittest.main()
