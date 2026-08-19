#!/usr/bin/env python3
# Version source: ../VERSION
"""Cross-platform project foundation initializer for planning-with-files."""

from __future__ import annotations

import argparse
import datetime as dt
import difflib
import json
import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path
from typing import Iterable

from pwf_governed._legacy import workflow_contracts as workflow
from pwf_governed._legacy import workflow_module_composer as composer
from pwf_governed._legacy import workflow_template_matcher as matcher
from pwf_governed._legacy import planning_layout as layout
from pwf_governed._legacy import root_resolver as root_resolver
from pwf_governed.edition import (
    NOT_HANDLED,
    EditionBoundaryError,
    current_edition,
    edition_operation,
)


CORE_FILES = (
    "00_PROJECT_INDEX.md",
    "1_master_plan.md",
    "2_execution_log.md",
    "3_status_update.md",
    "4_handoff.md",
    "5_audit.md",
    workflow.CHECKLIST_NAME,
    "AGENTS.md",
    "CLAUDE.md",
)
PROJECT_FILES = CORE_FILES + ("README.md",)
INIT_PLANNING_FILES = ("task_plan.md", "findings.md", "progress.md", "CONTEXT.md")
INIT_PROJECT_FILES = PROJECT_FILES + INIT_PLANNING_FILES
# Resource resolution: installed mode uses importlib.resources, repository mode uses filesystem
try:
    from importlib.resources import files as _resource_files
    _templates_traversable = _resource_files("pwf_governed.resources") / "templates"
    # Check if resources are available as real files (not in zip)
    if hasattr(_templates_traversable, "__fspath__"):
        TEMPLATE_DIR = Path(_templates_traversable)
    else:
        # Fallback: extract to temp or use filesystem path
        TEMPLATE_DIR = Path(__file__).resolve().parent.parent.parent / "resources" / "templates"
except (ImportError, ModuleNotFoundError, FileNotFoundError):
    TEMPLATE_DIR = Path(__file__).resolve().parent.parent.parent / "resources" / "templates"
VERSION_FILE = Path(__file__).resolve().parent.parent / "VERSION"
LOCK_NAME = ".planning-init.lock"


def timestamp() -> str:
    return dt.datetime.now().astimezone().replace(microsecond=0).isoformat()


def current_agent() -> str:
    return os.environ.get("CODEX_AGENT_ID") or os.environ.get("AGENT_ID") or "Codex"


def infer_machine() -> str:
    if os.name == "nt":
        return "Win11"
    if "microsoft" in os.uname().release.lower():
        return "WSL"
    return "Mac mini M4"


def default_skill_root(raw: str | None = None) -> Path:
    script_path = __file__
    if "project_init" in sys.modules:
        facade_mod = sys.modules["project_init"]
        if hasattr(facade_mod, "__file__") and facade_mod.__file__:
            script_path = facade_mod.__file__
    return root_resolver.resolve_skill_root(raw, script_path=script_path)


def project_has_governance(root: Path) -> bool:
    if any((root / name).exists() for name in ("AGENTS.md", "CLAUDE.md", "00_PROJECT_INDEX.md", "INDEX.md")):
        return True
    return any((root / name).exists() for name in CORE_FILES) or any(
        (root / layout.CANONICAL_DIR_NAME / name).exists() for name in layout.PLANNING_DOCUMENTS
    )


def planning_layout(root: Path, args: argparse.Namespace, *, mode: str | None = None) -> layout.Layout:
    selected_mode = mode or ("repair" if getattr(args, "repair", False) else "adopt" if getattr(args, "adopt", False) else "new")
    return layout.layout_for_init(
        root,
        mode=selected_mode,
        planning_dir=getattr(args, "planning_dir", None),
        task_id=getattr(args, "task_id", None),
    )


def planning_path(root: Path, name: str, active_layout: layout.Layout | None = None) -> Path:
    selected = active_layout or layout.resolve_layout(root, require=False)
    if selected is None:
        selected = layout.layout_for_init(root, mode="new")
    return selected.path(name)


def project_file_target(root: Path, name: str, active_layout: layout.Layout | None = None) -> Path:
    return planning_path(root, name, active_layout) if name in layout.PLANNING_DOCUMENTS else root / name


def atomic_write(path: Path, content: str) -> None:
    tmp = path.with_name(f".planning-tmp-{path.name}-{os.getpid()}")
    try:
        with tmp.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
        tmp.replace(path)
    finally:
        if tmp.exists():
            tmp.unlink()


def _resource_roots() -> tuple[object, ...]:
    return current_edition().project_init_hooks.resource_roots(TEMPLATE_DIR.parent)


def _workflow_resource_root() -> Path:
    for root in _resource_roots():
        candidate = Path(str(root))
        if (candidate / "templates" / "workflow" / "template_registry.json").is_file():
            return candidate
    raise FileNotFoundError("workflow template registry is unavailable")


def template_content(name: str, values: dict[str, str]) -> str:
    content = None
    for root in _resource_roots():
        template_path = Path(str(root)) / "templates" / name
        if template_path.is_file():
            content = template_path.read_text(encoding="utf-8")
            break
    if content is None:
        raise FileNotFoundError(f"project template is unavailable: {name}")
    for key, value in values.items():
        content = content.replace("{{" + key + "}}", value)
    return content


def pid_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
            if handle:
                kernel32.CloseHandle(handle)
                return True
            if ctypes.get_last_error() == 5:  # ERROR_ACCESS_DENIED: the process still exists.
                return True
        except (AttributeError, OSError):
            pass
    try:
        os.kill(pid, 0)
    except PermissionError:
        # The process exists but cannot be inspected; retain the lock.
        return True
    except OSError:
        return False
    return True


def canonical_lock_root(root: str | Path) -> str:
    """Normalize lock roots consistently across Windows and POSIX hosts."""

    return os.path.normcase(os.path.realpath(os.path.abspath(os.path.expanduser(str(root)))))


class ProjectLock:
    def __init__(self, root: Path, mode: str, agent: str) -> None:
        self.root = root
        self.root_key = canonical_lock_root(root)
        self.path = root / LOCK_NAME
        self.mode = mode
        self.agent = agent
        self.acquired = False

    def acquire(self) -> None:
        if self.path.exists():
            try:
                old = json.loads(self.path.read_text(encoding="utf-8"))
                valid = (
                    isinstance(old.get("root"), str)
                    and canonical_lock_root(old["root"]) == self.root_key
                    and isinstance(old.get("pid"), int)
                    and pid_exists(old["pid"])
                )
            except (OSError, ValueError, json.JSONDecodeError):
                valid = False
            if valid:
                raise RuntimeError("active initialization lock")
            diagnostic = self.path.with_name(f"{LOCK_NAME}.{dt.datetime.now():%Y%m%dT%H%M%S}.diagnostic")
            self.path.replace(diagnostic)
            print(f"WARN: stale lock retained as {diagnostic.name}")
        payload = {
            "agent": self.agent,
            "pid": os.getpid(),
            "mode": self.mode,
            "started_at": timestamp(),
            "root": self.root_key,
        }
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self.acquired = True

    def release(self) -> None:
        if self.acquired and self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if data.get("pid") == os.getpid():
                    self.path.unlink()
            except (OSError, ValueError, json.JSONDecodeError):
                pass




def run_index_preflight(root: Path, args: argparse.Namespace) -> tuple[str, str]:
    if args.index_mode == "skip":
        return "skipped", "user explicitly skipped"
    return "equivalent-flow", "index manager not available in community edition"

def write_equivalent_index(root: Path, agent: str, note: str, active_layout: layout.Layout | None = None) -> None:
    index = root / "INDEX.md"
    log = root / "LIBRARY_LOG.md"
    if not index.exists():
        atomic_write(index, "# 项目目录索引\n\n由 planning-with-files 等价索引流程创建。\n")
    entry = f"- {timestamp()} | {agent} | equivalent-index\n  {note}\n"
    with log.open("a", encoding="utf-8") as handle:
        if log.stat().st_size == 0:
            handle.write("# 图书管理员登记日志\n\n")
        handle.write(entry)
    project_index = root / "00_PROJECT_INDEX.md"
    if project_index.exists():
        original = project_index.read_text(encoding="utf-8")
        selected = active_layout or layout.resolve_layout(root, require=False) or layout.layout_for_init(root, mode="new")
        rows = ["| 文件 | 用途 | 状态 |", "|---|---|---|"]
        for name in INIT_PROJECT_FILES:
            target = project_file_target(root, name, selected)
            relative = target.relative_to(root).as_posix()
            rows.append(f"| `{relative}` | 核心治理文件 | {'存在' if target.exists() else '缺失'} |")
        rows.extend([
            "| `INDEX.md` | 等价流程目录索引 | 存在 |",
            "| `LIBRARY_LOG.md` | 等价流程登记日志 | 存在 |",
        ])
        replacement = "<!-- INDEX-MANAGER:AUTO-START -->\n## 文件索引\n\n" + "\n".join(rows) + "\n\n索引方式：等价流程。\n<!-- INDEX-MANAGER:AUTO-END -->"
        start = original.find("<!-- INDEX-MANAGER:AUTO-START -->")
        end = original.find("<!-- INDEX-MANAGER:AUTO-END -->")
        if start >= 0 and end >= start:
            updated = original[:start] + replacement + original[end + len("<!-- INDEX-MANAGER:AUTO-END -->"):]
            atomic_write(project_index, updated)
        atomic_write(project_index, layout.render_index_links(selected, existing=project_index.read_text(encoding="utf-8")))


def run_index_update(root: Path, args: argparse.Namespace, agent: str, active_layout: layout.Layout | None = None) -> tuple[str, str]:
    if args.index_mode == "skip":
        return "skipped", "user explicitly skipped"
    write_equivalent_index(root, agent, "index manager not available, using equivalent flow", active_layout)
    return "equivalent-flow", "created INDEX.md and LIBRARY_LOG.md"

def record_index_result(root: Path, status: str, result: str) -> None:
    path = root / "00_PROJECT_INDEX.md"
    if not path.exists():
        return
    original = path.read_text(encoding="utf-8")
    updated = original.replace(
        "| 索引管理员 | 待调用 |",
        f"| 索引管理员 | {status} |",
    ).replace(
        "等待地基创建后正式更新",
        result,
    )
    if updated != original:
        atomic_write(path, updated)


def ensure_gitignore(root: Path) -> None:
    result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--is-inside-work-tree"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0 or result.stdout.strip() != "true":
        return
    path = root / ".gitignore"
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    required = [LOCK_NAME, f"{LOCK_NAME}.*", ".planning-tmp-*"]
    additions = [line for line in required if line not in existing.splitlines()]
    if additions:
        prefix = "" if not existing or existing.endswith("\n") else "\n"
        atomic_write(path, existing + prefix + "\n".join(additions) + "\n")


def build_values(args: argparse.Namespace, root: Path, skill_root: Path) -> dict[str, str]:
    relative = args.relative_path
    relative_source = "user-provided"
    if not relative:
        try:
            relative = root.resolve().relative_to(skill_root.resolve()).as_posix()
            relative_source = "auto-detected"
        except ValueError as exc:
            raise ValueError("cannot safely derive relative path; provide --relative-path") from exc
    agent = args.primary_agent or current_agent()
    machine = args.primary_machine or infer_machine()
    project_id = args.project_id or f"PROJECT-{dt.datetime.now():%Y%m%d-%H%M%S}"
    mac_root = args.mac_root or str(skill_root)
    win_root = args.win_root or ""
    wsl_root = args.wsl_root or ""
    def joined(base: str) -> str:
        if base == "":
            return ""
        return str(Path(base) / relative)
    return {
        "PROJECT_NAME": args.project_name or root.name,
        "PROJECT_ID": project_id,
        "TASK_ID": getattr(args, "task_id", None) or project_id,
        "PROJECT_ID_SOURCE": "user-provided" if args.project_id else "auto-detected",
        "BUSINESS_LINE": args.business_line or "ad-hoc project (auto-detected)",
        "PRIMARY_MACHINE": machine,
        "PRIMARY_MACHINE_SOURCE": "user-provided" if args.primary_machine else "auto-detected",
        "PRIMARY_AGENT": agent,
        "PRIMARY_AGENT_SOURCE": "user-provided" if args.primary_agent else "auto-detected",
        "RELATIVE_PATH": relative,
        "RELATIVE_PATH_SOURCE": relative_source,
        "INPUT_DIR": args.input_dir or "",
        "OUTPUT_DIR": args.output_dir or "",
        "MAC_ROOT": mac_root,
        "WIN_ROOT": win_root,
        "WSL_ROOT": wsl_root,
        "MAC_PROJECT_PATH": joined(mac_root),
        "WIN_PROJECT_PATH": joined(win_root),
        "WSL_PROJECT_PATH": joined(wsl_root),
        "AUDIT_LEVEL": args.audit_level,
        "TIMESTAMP": timestamp(),
        "INDEX_STATUS": "pending",
        "INDEX_RESULT": "awaiting foundation creation",
        "INDEX_EVIDENCE": "project initialization",
        "RULE_PROFILE_BLOCK": "",
        "RULE_PROFILE_STATUS": "",
    }


def create_missing(root: Path, values: dict[str, str], active_layout: layout.Layout | None = None) -> list[str]:
    selected = active_layout or layout.layout_for_init(root, mode="new")
    created = []
    for name in INIT_PROJECT_FILES:
        if name == workflow.CHECKLIST_NAME:
            continue
        target = project_file_target(root, name, selected)
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(target, template_content(name, values))
        created.append(target.relative_to(root).as_posix())
    for directory in layout.PLANNING_DIRS:
        target = selected.planning_dir / directory
        if not target.exists():
            target.mkdir(parents=True, exist_ok=True)
            created.append(target.relative_to(root).as_posix() + "/")
    index = root / "00_PROJECT_INDEX.md"
    if index.exists():
        atomic_write(index, layout.render_index_links(selected, existing=index.read_text(encoding="utf-8")))
    return created


def workflow_task_text(root: Path, args: argparse.Namespace, active_layout: layout.Layout | None = None) -> str:
    parts = [args.project_name or root.name, args.business_line or "", args.relative_path or ""]
    selected = active_layout or layout.resolve_layout(root, require=False)
    if root.exists():
        for name in ("00_PROJECT_INDEX.md", "1_master_plan.md", "3_status_update.md", "README.md", "AGENTS.md", "CLAUDE.md"):
            path = project_file_target(root, name, selected)
            if path.exists():
                parts.append(path.read_text(encoding="utf-8", errors="replace")[:12000])
    return "\n".join(parts)


def create_workflow_checklist(root: Path, values: dict[str, str], args: argparse.Namespace, active_layout: layout.Layout | None = None) -> bool:
    selected_layout = active_layout or layout.resolve_layout(root, require=False) or layout.layout_for_init(root, mode="new")
    target = selected_layout.path(workflow.CHECKLIST_NAME)
    if target.exists():
        original = target.read_text(encoding="utf-8")
        metadata = workflow.validate_checklist_text(original)
        if args.workflow_template or args.workflow_modules or args.workflow_excluded_modules:
            selection = matcher.identify_template(
                _workflow_resource_root(),
                root,
                project_name=values["PROJECT_NAME"],
                source_text=workflow_task_text(root, args, selected_layout),
                explicit_template_id=args.workflow_template,
            )
            binding, _default_modules, _template_path = workflow.template_binding(_workflow_resource_root(), selection)
            composed = composer.compose_modules(
                _workflow_resource_root(),
                root,
                selection,
                source_text=workflow_task_text(root, args, selected_layout),
                explicit_module_ids=args.workflow_modules,
                excluded_module_ids=args.workflow_excluded_modules,
                existing_bindings=metadata.get("modules", []),
            )
            module_changed = metadata.get("modules", []) != composed["modules"]
            template_changed = metadata["template"]["template_id"] != selection["template_id"] or metadata["template"]["template_version"] != selection["template_version"]
            if template_changed or module_changed:
                binding, modules, _template_path = workflow.template_binding(_workflow_resource_root(), selection)
                updated_metadata = workflow.merge_json(metadata, {
                    "template": binding,
                    "modules": composed["modules"],
                    "template_match": {key: selection[key] for key in ("template_id", "template_version", "template_digest", "match_method", "confidence", "matched_signals", "excluded_templates", "fallback_used", "reason")},
                    "checklist_version": workflow.bump_semver(metadata["checklist_version"], "MINOR"),
                    "last_updated_at": values["TIMESTAMP"],
                })
                updated = workflow.replace_machine_json(original, "workflow", updated_metadata)
                workflow.validate_checklist_text(updated)
                lock_path = selected_layout.planning_dir / ".planning" / "workflow.lock"
                conflicts_dir = selected_layout.planning_dir / ".planning" / "conflicts"
                lock = workflow.acquire_workflow_lock(lock_path, workflow.CHECKLIST_NAME, workflow.file_digest(target), values["PRIMARY_AGENT"], conflicts_dir)
                try:
                    if workflow.file_digest(target) != lock["base_digest"]:
                        workflow.write_conflict_report(conflicts_dir, {
                            "conflict_id": f"conflict-{workflow.utc_filename_timestamp()}",
                            "target_file": workflow.CHECKLIST_NAME,
                            "base_digest": lock["base_digest"],
                            "current_digest": workflow.file_digest(target),
                            "conflict_reason": "base digest changed before explicit template binding update",
                            "affected_task_ids": [],
                            "recommended_handling": "人工核对当前清单后合并",
                        })
                        raise workflow.ContractError("workflow checklist base digest changed before binding update")
                    workflow.atomic_write_text(target, updated)
                finally:
                    workflow.release_lock(lock_path, process_id=lock["process_id"], host_name=lock["host_name"])
        return False
    selection = matcher.identify_template(
        _workflow_resource_root(),
        root,
        project_name=values["PROJECT_NAME"],
        source_text=workflow_task_text(root, args, selected_layout),
        explicit_template_id=args.workflow_template,
    )
    binding, _default_modules, template_path = workflow.template_binding(_workflow_resource_root(), selection)
    composed = composer.compose_modules(
        _workflow_resource_root(),
        root,
        selection,
        source_text=workflow_task_text(root, args, selected_layout),
        explicit_module_ids=args.workflow_modules,
        excluded_module_ids=args.workflow_excluded_modules,
    )
    content = workflow.checklist_from_template(values["PROJECT_ID"], template_path, binding, composed["modules"], selection, values["PRIMARY_AGENT"])
    if args.adopt:
        content = workflow.adopt_with_explicit_evidence(content, root)
    lock_path = selected_layout.planning_dir / ".planning" / "workflow.lock"
    conflicts_dir = selected_layout.planning_dir / ".planning" / "conflicts"
    base_digest = workflow.sha256_digest("")
    lock = workflow.acquire_workflow_lock(lock_path, workflow.CHECKLIST_NAME, base_digest, values["PRIMARY_AGENT"], conflicts_dir)
    try:
        if workflow.sha256_digest(target.read_bytes() if target.exists() else b"") != lock["base_digest"]:
            workflow.write_conflict_report(conflicts_dir, {
                "conflict_id": f"conflict-{workflow.utc_filename_timestamp()}",
                "target_file": workflow.CHECKLIST_NAME,
                "base_digest": lock["base_digest"],
                "current_digest": workflow.file_digest(target) if target.exists() else workflow.sha256_digest(""),
                "conflict_reason": "base digest changed before create",
                "affected_task_ids": [],
                "recommended_handling": "人工核对当前清单后合并",
            })
            raise workflow.ContractError("workflow checklist base digest changed before create")
        workflow.atomic_write_text(target, content)
    finally:
        workflow.release_lock(lock_path, process_id=lock["process_id"], host_name=lock["host_name"])
    return True


def repair_workflow_checklist(root: Path, values: dict[str, str], args: argparse.Namespace, apply: bool, active_layout: layout.Layout | None = None) -> int:
    selected_layout = active_layout or layout.resolve_layout(root, require=False) or layout.layout_for_init(root, mode="new")
    target = selected_layout.path(workflow.CHECKLIST_NAME)
    if not target.exists():
        print(f"MISSING FILE: {workflow.CHECKLIST_NAME}; use --adopt to create it")
        return 0
    original = target.read_text(encoding="utf-8")
    try:
        metadata = workflow.extract_machine_json(original, "workflow")
        workflow.validate_workflow_metadata(metadata)
        errors = workflow.workflow_integrity_errors(original)
        if not errors:
            return 0
        print("WORKFLOW CHECKLIST: integrity issues require task-level review")
        for error in errors:
            print(f"- {error}")
        return 0
    except workflow.ContractError:
        selection = matcher.identify_template(
            _workflow_resource_root(),
            root,
            project_name=values["PROJECT_NAME"],
            source_text=workflow_task_text(root, args, selected_layout),
            explicit_template_id=args.workflow_template,
            allow_malformed_binding=True,
        )
        binding, modules, _template_path = workflow.template_binding(_workflow_resource_root(), selection)
        replacement_metadata = workflow.initial_workflow_metadata(values["PROJECT_ID"], binding, modules, selection, values["PRIMARY_AGENT"])
        try:
            old = workflow.extract_machine_json_lenient(original, "workflow")
            old_version = old.get("checklist_version", "1.0.0")
            replacement_metadata["checklist_version"] = workflow.bump_semver(old_version, "PATCH")
        except workflow.ContractError:
            pass
        updated = workflow.replace_machine_json(original, "workflow", replacement_metadata)
        print("".join(difflib.unified_diff(original.splitlines(True), updated.splitlines(True), fromfile=str(target), tofile=str(target))))
        if not apply:
            return 1
        lock_path = selected_layout.planning_dir / ".planning" / "workflow.lock"
        conflicts_dir = selected_layout.planning_dir / ".planning" / "conflicts"
        base_digest = workflow.file_digest(target)
        lock = workflow.acquire_workflow_lock(lock_path, workflow.CHECKLIST_NAME, base_digest, values["PRIMARY_AGENT"], conflicts_dir)
        try:
            if workflow.file_digest(target) != lock["base_digest"]:
                workflow.write_conflict_report(conflicts_dir, {
                    "conflict_id": f"conflict-{workflow.utc_filename_timestamp()}",
                    "target_file": workflow.CHECKLIST_NAME,
                    "base_digest": lock["base_digest"],
                    "current_digest": workflow.file_digest(target),
                    "conflict_reason": "base digest changed before repair",
                    "affected_task_ids": [],
                    "recommended_handling": "人工核对当前清单后合并",
                })
                raise workflow.ContractError("workflow checklist base digest changed before repair")
            workflow.validate_checklist_text(updated)
            workflow.atomic_write_text(target, updated)
        finally:
            workflow.release_lock(lock_path, process_id=lock["process_id"], host_name=lock["host_name"])
        return 1


REQUIRED_SECTIONS = {
    "00_PROJECT_INDEX.md": ("## 项目概况", "## 执行入口验证状态", "## 运行路径与映射状态", "## Skill 联动"),
    "1_master_plan.md": ("## North Star and MVP", "## 阶段与 Done Criteria", "## 范围与禁止扩展"),
    "2_execution_log.md": ("## 执行记录", "## 问题记录"),
    "3_status_update.md": ("## 老板快速入口", "## 执行与审计状态"),
    "4_handoff.md": ("## 当前现场", "## 接管后的第一步"),
    "5_audit.md": ("## 集中审计",),
    "AGENTS.md": ("## 第一准则", "## 红线", "## 验收"),
    "CLAUDE.md": ("@AGENTS.md",),
}


def repair_preview(root: Path, values: dict[str, str], apply: bool, active_layout: layout.Layout | None = None) -> int:
    selected_layout = active_layout or layout.resolve_layout(root, require=False) or layout.layout_for_init(root, mode="new")
    changes = 0
    for name, sections in REQUIRED_SECTIONS.items():
        path = project_file_target(root, name, selected_layout)
        if not path.exists():
            print(f"MISSING FILE: {name}; use --adopt to create it")
            continue
        original = path.read_text(encoding="utf-8")
        additions = []
        template = template_content(name, values)
        for section in sections:
            if section not in original:
                start = template.find(section)
                end = template.find("\n## ", start + 1)
                additions.append(template[start:] if end < 0 else template[start:end])
        if not additions:
            continue
        updated = original.rstrip() + "\n\n<!-- planning-with-files repair additions -->\n" + "\n\n".join(additions) + "\n"
        print("".join(difflib.unified_diff(original.splitlines(True), updated.splitlines(True), fromfile=name, tofile=name)))
        changes += 1
        if apply:
            atomic_write(path, updated)
    return changes


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--new", action="store_true")
    modes.add_argument("--adopt", action="store_true")
    modes.add_argument("--repair", action="store_true")
    parser.add_argument("legacy_project_name", nargs="?")
    parser.add_argument("--migrate-layout", action="store_true")
    parser.add_argument("--import-task-package", help="copy an external task package into the project task root")
    parser.add_argument("--apply", action="store_true", help="apply an explicitly selected migration")
    parser.add_argument("--confirm", action="store_true", help="confirm a destructive-looking but reversible layout migration")
    parser.add_argument("--apply-repair", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--project-root", default=os.getcwd())
    parser.add_argument("--skill-root", help="explicit skill root directory")
    parser.add_argument("--project-name")
    parser.add_argument("--project-id")
    parser.add_argument("--task-id", help="visible task directory name under 00.项目规划与治理")
    parser.add_argument("--business-line")
    parser.add_argument("--primary-machine")
    parser.add_argument("--primary-agent")
    parser.add_argument("--relative-path")
    parser.add_argument("--planning-dir", help="explicit planning directory, relative to project root or absolute inside it")
    parser.add_argument("--mac-root")
    parser.add_argument("--win-root")
    parser.add_argument("--wsl-root")
    parser.add_argument("--input-dir")
    parser.add_argument("--output-dir")
    parser.add_argument("--audit-level", choices=("A0", "A1", "A2"), default="A1")
    parser.add_argument("--index-mode", choices=("auto", "required", "skip"), default="auto")
    parser.add_argument("--workflow-template")
    parser.add_argument("--module", dest="workflow_modules", action="append", default=[])
    parser.add_argument("--exclude-module", dest="workflow_excluded_modules", action="append", default=[])
    current_edition().project_init_hooks.extend_parser(parser)
    args = parser.parse_args(argv)
    if args.apply_repair and not args.repair:
        parser.error("--apply-repair requires --repair")
    if args.legacy_project_name == "migrate-layout":
        args.migrate_layout = True
        args.legacy_project_name = None
    if (args.migrate_layout or args.import_task_package) and (args.new or args.adopt or args.repair):
        parser.error("migration/import cannot be combined with --new/--adopt/--repair")
    if args.migrate_layout and args.import_task_package:
        parser.error("migrate-layout and import-task-package are mutually exclusive")
    if args.confirm and not (args.migrate_layout or args.import_task_package):
        parser.error("--confirm requires migrate-layout or --import-task-package")
    if args.apply and not (args.migrate_layout or args.import_task_package):
        parser.error("--apply requires migrate-layout or --import-task-package")
    if args.import_task_package and not args.task_id:
        parser.error("--import-task-package requires --task-id")
    if args.task_id:
        try:
            args.task_id = layout.validate_task_id(args.task_id)
        except layout.LayoutError as exc:
            parser.error(str(exc))
    if args.legacy_project_name and not args.project_name:
        args.project_name = args.legacy_project_name
    return args












@edition_operation
def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    root = Path(args.project_root).expanduser().resolve(strict=False)
    readonly = args.dry_run or getattr(args, "rule_sync", None) in {"check", "preview"}
    hooks = current_edition().project_init_hooks
    if args.migrate_layout:
        if not root.exists() or not root.is_dir():
            print(f"ERROR: migrate-layout requires an existing project root: {root}", file=sys.stderr)
            return 2
        try:
            result = layout.migrate_layout(
                root,
                target_dir=args.planning_dir,
                apply=args.apply,
                confirm=args.confirm,
            )
        except layout.LayoutError as exc:
            print(json.dumps(layout.describe_conflict(exc), ensure_ascii=False, indent=2), file=sys.stderr)
            return 4
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.import_task_package:
        if not root.exists() or not root.is_dir():
            print(f"ERROR: import-task-package requires an existing project root: {root}", file=sys.stderr)
            return 2
        try:
            result = layout.import_task_package(
                root,
                Path(args.import_task_package).expanduser().resolve(strict=False),
                args.task_id,
                apply=args.apply,
                confirm=args.confirm,
            )
        except layout.LayoutError as exc:
            print(json.dumps(layout.describe_conflict(exc), ensure_ascii=False, indent=2), file=sys.stderr)
            return 4
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    try:
        resolved = hooks.resolve_root(args)
        skill_root = default_skill_root(args.skill_root) if resolved is NOT_HANDLED else Path(resolved)
    except (root_resolver.RootResolutionError, EditionBoundaryError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 5
    if args.repair and not root.exists():
        print("ERROR: --repair requires an existing project root", file=sys.stderr)
        return 2
    if not root.exists() and readonly:
        print(f"DRY-RUN: would create project root {root}")
    elif not root.exists():
        root.mkdir(parents=True)
    if not args.new and not args.adopt and not args.repair:
        args.new = not project_has_governance(root)
        args.adopt = not args.new
    if args.new and args.task_id:
        target_task = root / layout.CANONICAL_DIR_NAME / args.task_id
        if target_task.exists():
            print(f"ERROR: --new detected existing task: {args.task_id}", file=sys.stderr)
            return 3
    elif args.new and project_has_governance(root):
        print("ERROR: --new detected existing governance files", file=sys.stderr)
        return 3
    try:
        values = build_values(args, root, skill_root)
        active_layout = planning_layout(root, args)
        if active_layout.task_id:
            os.environ["PWF_TASK_ID"] = active_layout.task_id
        values = hooks.prepare(args, root, skill_root, values, active_layout)
        if args.dry_run and args.repair:
            changes = repair_preview(root, values, apply=False, active_layout=active_layout)
            workflow_changes = repair_workflow_checklist(root, values, args, apply=False, active_layout=active_layout)
            print(f"DRY-RUN REPAIR PREVIEW: {changes} file(s) need sections; workflow={workflow_changes}")
            return 0
        if readonly:
            handled = hooks.handle_readonly(args, root, skill_root, values, active_layout)
            return 0 if handled is NOT_HANDLED else int(handled)
        index_preflight = hooks.index_preflight(args, root, skill_root)
        if index_preflight is NOT_HANDLED:
            run_index_preflight(root, args)
    except (ValueError, layout.LayoutError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 3
    except (FileNotFoundError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 5
    lock = ProjectLock(root, "repair" if args.repair else "new" if args.new else "adopt", values["PRIMARY_AGENT"])
    try:
        lock.acquire()
    except RuntimeError:
        print("ERROR: active initialization lock", file=sys.stderr)
        return 6
    try:
        if args.repair:
            changes = repair_preview(root, values, args.apply_repair, active_layout=active_layout)
            workflow_changes = repair_workflow_checklist(root, values, args, args.apply_repair, active_layout=active_layout)
            print(f"REPAIR {'APPLIED' if args.apply_repair else 'PREVIEW'}: {changes} file(s) need sections; workflow={workflow_changes}")
            return 0
        created = create_missing(root, values, active_layout)
        if active_layout.task_id:
            layout.register_task(root, active_layout.task_id)
        checklist_created = create_workflow_checklist(root, values, args, active_layout)
        if checklist_created:
            created.append(active_layout.path(workflow.CHECKLIST_NAME).relative_to(root).as_posix())
        edition_result = hooks.after_create(args, root, skill_root, values, active_layout)
        ensure_gitignore(root)
        try:
            index_result = hooks.index_update(
                args,
                root,
                skill_root,
                values["PRIMARY_AGENT"],
                active_layout,
                index_preflight,
            )
            if index_result is NOT_HANDLED:
                status, result = run_index_update(root, args, values["PRIMARY_AGENT"], active_layout)
            else:
                status, result = index_result
        except (FileNotFoundError, RuntimeError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 5
        index_path = root / "00_PROJECT_INDEX.md"
        if index_path.exists():
            atomic_write(index_path, layout.render_index_links(active_layout, existing=index_path.read_text(encoding="utf-8")))
        record_index_result(root, status, result)
        try:
            from pwf_governed.progress_excel import ensure_required_plan_artifacts
            ensure_required_plan_artifacts(root)
        except Exception:
            pass
        print(f"SUCCESS: mode={'new' if args.new else 'adopt'} created={','.join(created) or 'none'}")
        if isinstance(edition_result, dict) and "rule_state" in edition_result:
            additions = edition_result.get("additions", [])
            registered = bool(edition_result.get("registered"))
            print(
                f"RULE SYNC: {edition_result['rule_state']['status']}; "
                f"log_sections={','.join(additions) or 'none'}; "
                f"registry={'updated' if registered else 'skipped'}"
            )
        print(f"INDEX: {status} - {result}")
        return 0
    except EditionBoundaryError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 4
    except workflow.ContractError as exc:
        print(f"ERROR: workflow module/template composition: {exc}", file=sys.stderr)
        return 3
    finally:
        lock.release()


if __name__ == "__main__":
    raise SystemExit(main())
