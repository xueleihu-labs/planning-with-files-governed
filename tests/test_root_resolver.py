from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import project_init  # noqa: E402
import root_resolver  # noqa: E402


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def make_skill_tree(root: Path) -> tuple[Path, Path]:
    skill = root
    (skill / "scripts").mkdir(parents=True)
    write_text(skill / "VERSION", "1.0.0\n")
    write_text(skill / "SKILL.md", "planning-with-files-governed\n")
    script = skill / "scripts" / "runner.py"
    write_text(script, "# fixture\n")
    return skill, script


class RootResolverTests(unittest.TestCase):
    def test_explicit_argument_precedes_environment_and_normalises(self) -> None:
        with tempfile.TemporaryDirectory(prefix="root-resolver-用户-") as directory:
            base = Path(directory)
            explicit = base / "显式根" / ".." / "显式根"
            configured = base / "环境根"
            with mock.patch.dict(os.environ, {"PWF_ROOT": str(configured)}):
                result = root_resolver.resolve_skill_root(explicit, script_path=base / "unknown.py")
            self.assertEqual(result, explicit.resolve())

    def test_environment_root_is_used_without_trusted_layout(self) -> None:
        with tempfile.TemporaryDirectory(prefix="root-resolver-other-user-") as directory:
            configured = Path(directory) / "test-skill-root"
            with mock.patch.dict(os.environ, {"PWF_ROOT": str(configured)}):
                result = root_resolver.resolve_skill_root(script_path=Path(directory) / "unknown.py")
            self.assertEqual(result, configured.resolve())

    def test_auto_discovery_uses_cross_user_layout(self) -> None:
        with tempfile.TemporaryDirectory(prefix="another-user-test-skill-root-") as directory:
            root = Path(directory)
            _skill, script = make_skill_tree(root)
            with mock.patch.dict(os.environ, {}, clear=True):
                self.assertEqual(root_resolver.resolve_skill_root(script_path=script), root.resolve())


    def test_project_init_does_not_guess_a_win11_root(self) -> None:
        with tempfile.TemporaryDirectory(prefix="root-resolver-platform-") as directory:
            args = project_init.parse_args(["--relative-path", "demo"])
            values = project_init.build_values(args, Path(directory) / "project", Path(directory) / "test-skill-root")
            self.assertEqual(values["WIN_ROOT"], "")

    def test_symlink_script_uses_real_path_for_discovery(self) -> None:
        with tempfile.TemporaryDirectory(prefix="root-resolver-symlink-") as directory:
            root = Path(directory) / "test-skill-root"
            _skill, script = make_skill_tree(root)
            alias = Path(directory) / "bin" / "runner.py"
            alias.parent.mkdir()
            alias.symlink_to(script)
            with mock.patch.dict(os.environ, {}, clear=True):
                self.assertEqual(root_resolver.discover_skill_root(alias), root.resolve())

    def test_untrusted_layout_fails_closed_without_writes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="root-resolver-untrusted-") as directory:
            base = Path(directory)
            script = base / "scripts" / "runner.py"
            write_text(script, "# fixture\n")
            before = sorted(path.relative_to(base).as_posix() for path in base.rglob("*"))
            with mock.patch.dict(os.environ, {}, clear=True):
                with self.assertRaises(root_resolver.RootResolutionError):
                    root_resolver.resolve_skill_root(script_path=script)
            after = sorted(path.relative_to(base).as_posix() for path in base.rglob("*"))
            self.assertEqual(after, before)

    def test_project_init_reports_untrusted_root_before_project_write(self) -> None:
        with tempfile.TemporaryDirectory(prefix="root-resolver-cli-") as directory:
            base = Path(directory)
            project = base / "new-project"
            fake_script = base / "scripts" / "project_init.py"
            with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(project_init, "__file__", str(fake_script)):
                result = project_init.main([
                    "--new",
                    "--project-root",
                    str(project),
                    "--relative-path",
                    "demo",
                    "--index-mode",
                    "skip",
                ])
            self.assertEqual(result, 5)
            self.assertFalse(project.exists())


if __name__ == "__main__":
    unittest.main()
