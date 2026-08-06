from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "scripts" / "runtime.py"
RESOLVE_SH = ROOT / "scripts" / "resolve-plan-dir.sh"
INJECT_SH = ROOT / "scripts" / "inject-plan.sh"
ATTEST_SH = ROOT / "scripts" / "attest-plan.sh"
DOCTOR_SH = ROOT / "scripts" / "plan-doctor.sh"
sys.path.insert(0, str(ROOT / "scripts"))
import planning_layout as layout  # noqa: E402
import runtime  # noqa: E402


class RuntimeAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="pwf-runtime-")
        self.root = Path(self.tmp.name) / "项目 with spaces"
        self.root.mkdir()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def write_plan(self, directory: Path, body: str | None = None) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        plan = directory / "task_plan.md"
        plan.write_text(body or (ROOT / "templates" / "task_plan.md").read_text(encoding="utf-8").replace("{{PROJECT_NAME}}", "runtime"), encoding="utf-8")
        (directory / "progress.md").write_text("recent action\n", encoding="utf-8")
        (directory / "findings.md").write_text("finding\n", encoding="utf-8")
        return plan

    def run_cli(self, *args: str, env: dict[str, str] | None = None, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, str(RUNTIME), *args], cwd=str(cwd or self.root), env=env, text=True, capture_output=True)

    def test_canonical_resolution_from_child_and_smart_injection(self) -> None:
        planning = self.root / layout.CANONICAL_DIR_NAME
        plan = self.write_plan(planning)
        child = planning / "evidence"
        child.mkdir()
        resolved = runtime.resolve_plan(child)
        self.assertIsNotNone(resolved)
        assert resolved is not None
        self.assertEqual(resolved.project_root, self.root.resolve())
        self.assertEqual(resolved.plan_dir, planning.resolve())
        output = runtime.inject(resolved)
        self.assertIn("ACTIVE PLAN", output)
        os.environ["PWF_INJECT"] = "smart"
        try:
            smart = runtime.inject(resolved)
        finally:
            os.environ.pop("PWF_INJECT", None)
        self.assertIn("phases:", smart)
        self.assertIn("## Next Step", smart)
        self.assertEqual(plan.read_text(encoding="utf-8").count("task_plan"), 0)

    def test_legacy_layout_and_conflict_are_fail_closed(self) -> None:
        self.write_plan(self.root)
        resolved = runtime.resolve_plan(self.root)
        self.assertIsNotNone(resolved)
        assert resolved is not None
        self.assertTrue(resolved.is_legacy)
        self.write_plan(self.root / layout.CANONICAL_DIR_NAME, "# different\n")
        with self.assertRaises(runtime.RuntimeError_) as caught:
            runtime.resolve_plan(self.root)
        self.assertIn("LAYOUT_CONFLICT", str(caught.exception))

    def test_plan_id_and_path_escape_are_blocked(self) -> None:
        scoped = self.root / ".planning" / "alpha"
        self.write_plan(scoped)
        selected = runtime.resolve_plan(self.root, plan_id="alpha")
        self.assertIsNotNone(selected)
        assert selected is not None
        self.assertEqual(selected.plan_dir, scoped.resolve())
        with self.assertRaises(runtime.RuntimeError_) as caught:
            runtime.resolve_plan(self.root, plan_id="../escape")
        self.assertEqual(caught.exception.code, "UNSAFE_PLAN_ID")
        outside = Path(self.tmp.name) / "outside"
        outside.mkdir()
        link = self.root / ".planning-link"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation unavailable")
        with self.assertRaises(runtime.RuntimeError_) as caught:
            runtime.resolve_plan(self.root, planning_dir=".planning-link")
        self.assertIn(caught.exception.code, {"PATH_ESCAPE_BLOCKED", "UNSAFE_LAYOUT_PATH"})

    def test_task_id_resolution_and_multiple_task_gate(self) -> None:
        first = self.root / layout.CANONICAL_DIR_NAME / "task-one"
        second = self.root / layout.CANONICAL_DIR_NAME / "task-two"
        self.write_plan(first)
        self.write_plan(second)
        layout.write_task_index(self.root, {"task-one": "task-one", "task-two": "task-two"})
        with self.assertRaises(runtime.RuntimeError_) as caught:
            runtime.resolve_plan(self.root)
        self.assertEqual(caught.exception.code, "TASK_SELECTION_REQUIRED")
        selected = runtime.resolve_plan(self.root, task_id="task-two")
        self.assertIsNotNone(selected)
        assert selected is not None
        self.assertEqual(selected.task_id, "task-two")
        self.assertEqual(selected.plan_dir, second.resolve())
        from_child = runtime.resolve_plan(first / "evidence")
        self.assertIsNotNone(from_child)
        assert from_child is not None
        self.assertEqual(from_child.task_id, "task-one")

    def test_attestation_round_trip_and_tamper_block(self) -> None:
        plan_file = self.write_plan(self.root / layout.CANONICAL_DIR_NAME)
        resolved = runtime.resolve_plan(self.root)
        assert resolved is not None
        result = runtime.attest(resolved)
        self.assertEqual(result["sha256"], hashlib.sha256(plan_file.read_bytes()).hexdigest())
        self.assertEqual(runtime.attest(resolved, show=True)["sha256"], result["sha256"])
        plan_file.write_text(plan_file.read_text(encoding="utf-8") + "tampered\n", encoding="utf-8")
        blocked = runtime.inject(resolved)
        self.assertIn("PLAN TAMPERED", blocked)
        runtime.attest(resolved, clear=True)
        self.assertFalse(resolved.attestation_file.exists())

    def test_disabled_wrappers_are_silent_and_do_not_write(self) -> None:
        planning = self.root / layout.CANONICAL_DIR_NAME
        self.write_plan(planning)
        env = os.environ.copy()
        env["PLANNING_DISABLED"] = "1"
        before = sorted(path.relative_to(self.root).as_posix() for path in self.root.rglob("*"))
        for script in (INJECT_SH, DOCTOR_SH):
            result = subprocess.run(["sh", str(script)], cwd=str(self.root), env=env, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "")
        result = subprocess.run(["sh", str(ROOT / "scripts" / "check-complete.sh")], cwd=str(self.root), env=env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        after = sorted(path.relative_to(self.root).as_posix() for path in self.root.rglob("*"))
        self.assertEqual(before, after)

    def test_shell_resolution_and_attestation_wrappers(self) -> None:
        planning = self.root / layout.CANONICAL_DIR_NAME
        self.write_plan(planning)
        resolved = subprocess.run(["sh", str(RESOLVE_SH)], cwd=str(self.root), text=True, capture_output=True)
        self.assertEqual(resolved.returncode, 0, resolved.stderr)
        self.assertEqual(Path(resolved.stdout.strip()), planning.resolve())
        attested = subprocess.run(["sh", str(ATTEST_SH)], cwd=str(self.root), text=True, capture_output=True)
        self.assertEqual(attested.returncode, 0, attested.stderr)
        self.assertIn("SHA-256", attested.stdout)

    def test_shell_wrappers_accept_explicit_task_id(self) -> None:
        first = self.root / layout.CANONICAL_DIR_NAME / "task-one"
        second = self.root / layout.CANONICAL_DIR_NAME / "task-two"
        self.write_plan(first)
        self.write_plan(second)
        layout.write_task_index(self.root, {"task-one": "task-one", "task-two": "task-two"})

        unresolved = subprocess.run(["sh", str(RESOLVE_SH)], cwd=str(self.root), text=True, capture_output=True)
        self.assertEqual(unresolved.returncode, 0)
        self.assertEqual(unresolved.stdout, "")
        selected = subprocess.run(
            ["sh", str(RESOLVE_SH), "--task-id", "task-two"],
            cwd=str(self.root),
            text=True,
            capture_output=True,
        )
        self.assertEqual(selected.returncode, 0, selected.stderr)
        self.assertEqual(Path(selected.stdout.strip()), second.resolve())
        attested = subprocess.run(
            ["sh", str(ATTEST_SH), "--task-id", "task-two"],
            cwd=str(self.root),
            text=True,
            capture_output=True,
        )
        self.assertEqual(attested.returncode, 0, attested.stderr)
        self.assertIn("SHA-256", attested.stdout)
        injected = subprocess.run(
            ["sh", str(INJECT_SH), "--context", "userprompt", "--task-id", "task-two"],
            cwd=str(self.root),
            text=True,
            capture_output=True,
        )
        self.assertEqual(injected.returncode, 0, injected.stderr)
        self.assertIn("ACTIVE PLAN", injected.stdout)

    def test_doctor_reports_missing_plan_without_failing_hook_loop(self) -> None:
        result = subprocess.run(["sh", str(DOCTOR_SH)], cwd=str(self.root), text=True, capture_output=True)
        self.assertEqual(result.returncode, 0)
        self.assertIn("no plan", result.stdout)

    def test_cross_platform_wrappers_are_static_safe(self) -> None:
        for name in ("resolve-plan-dir.ps1", "inject-plan.ps1", "attest-plan.ps1", "plan-doctor.ps1"):
            text = (ROOT / "scripts" / name).read_text(encoding="utf-8-sig")
            self.assertIn("runtime.py", text)
            self.assertIn("TaskId", text)
            self.assertFalse(any("/Users/" in line and "/Users/<" not in line for line in text.splitlines()), "wrapper scripts must not contain personal paths")  # privacy guard: no personal paths
            self.assertFalse(any("E:\\" in line and "<" not in line for line in text.splitlines()), "wrapper scripts must not contain personal Windows paths")
        for name in ("resolve-plan-dir.sh", "inject-plan.sh", "attest-plan.sh", "plan-doctor.sh"):
            result = subprocess.run(["sh", "-n", str(ROOT / "scripts" / name)], text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
