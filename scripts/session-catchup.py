#!/usr/bin/env python3
# VERSION source: ../VERSION
"""Restore planning-with-files context from project-root governance files first."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import workflow_contracts as workflow
import planning_layout as layout


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
ROOT_ENTRY_FILES = ("00_PROJECT_INDEX.md", "AGENTS.md", "CLAUDE.md")
SESSION_PLANNING_FILES = {"task_plan.md", "findings.md", "progress.md", "CONTEXT.md", "WORKFLOW_CHECKLIST.md"}


def first_meaningful_lines(path: Path, count: int = 3) -> list[str]:
    lines = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        cleaned = line.strip()
        if cleaned and not cleaned.startswith("<!--"):
            lines.append(cleaned)
        if len(lines) == count:
            break
    return lines


def claude_project_dir(project_path: Path) -> Path:
    """Return Claude Code's sanitized project session directory."""
    value = str(project_path.resolve())
    if ":" in value or "\\" in value:
        sanitized = value.replace("\\", "-").replace("/", "-").replace(":", "-")
    else:
        sanitized = value.replace("/", "-")
        if not sanitized.startswith("-"):
            sanitized = "-" + sanitized
    projects = Path.home() / ".claude" / "projects"
    exact = projects / sanitized
    legacy = projects / sanitized.replace("_", "-")
    return exact if exact.is_dir() or not legacy.is_dir() else legacy


def scan_session_planning_update(session_file: Path) -> tuple[int, str | None]:
    last_line = -1
    last_file: str | None = None
    try:
        for line_number, line in enumerate(session_file.read_text(encoding="utf-8", errors="replace").splitlines()):
            if '"Write"' not in line and '"Edit"' not in line and '"Bash"' not in line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if data.get("type") != "assistant":
                continue
            content = data.get("message", {}).get("content", [])
            if not isinstance(content, list):
                continue
            for item in content:
                if not isinstance(item, dict) or item.get("type") != "tool_use":
                    continue
                tool_input = item.get("input") or {}
                target = tool_input.get("file_path") or tool_input.get("filePath") or ""
                if Path(str(target)).name in SESSION_PLANNING_FILES:
                    last_line, last_file = line_number, Path(str(target)).name
    except OSError:
        return -1, None
    return last_line, last_file


def extract_session_messages(session_file: Path, after_line: int) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    try:
        lines = session_file.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return messages
    for line_number, line in enumerate(lines):
        if line_number <= after_line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        kind = data.get("type")
        if kind == "user" and not data.get("isMeta", False):
            content = data.get("message", {}).get("content", "")
            if isinstance(content, list):
                content = next((item.get("text", "") for item in content if isinstance(item, dict) and item.get("type") == "text"), "")
            if isinstance(content, str) and len(content.strip()) > 20 and not content.startswith(("<local-command", "<command-", "<task-notification")):
                messages.append({"role": "user", "content": content[:600], "line": line_number, "session": session_file.stem[:8]})
        elif kind == "assistant":
            content = data.get("message", {}).get("content", "")
            text = ""
            tools: list[str] = []
            if isinstance(content, str):
                text = content
            elif isinstance(content, list):
                for item in content:
                    if not isinstance(item, dict):
                        continue
                    if item.get("type") == "text":
                        text = str(item.get("text", ""))
                    elif item.get("type") == "tool_use":
                        tool_input = item.get("input") or {}
                        tool = item.get("name", "tool")
                        target = tool_input.get("file_path") or tool_input.get("filePath")
                        tools.append(f"{tool}: {target}" if target else str(tool))
            if text or tools:
                messages.append({"role": "assistant", "content": text[:600], "tools": tools[:4], "line": line_number, "session": session_file.stem[:8]})
    return messages


def collect_claude_session_history(project_root: Path) -> dict[str, Any] | None:
    session_dir = claude_project_dir(project_root)
    if not session_dir.is_dir():
        return None
    sessions = sorted((item for item in session_dir.glob("*.jsonl") if not item.name.startswith("agent-")), key=lambda item: item.stat().st_mtime, reverse=True)
    previous = sessions[1:]
    update_session: Path | None = None
    update_line = -1
    update_file: str | None = None
    update_index = -1
    for index, session in enumerate(previous):
        line, name = scan_session_planning_update(session)
        if line >= 0:
            update_session, update_line, update_file, update_index = session, line, name, index
            break
    if update_session is None:
        return None
    messages = extract_session_messages(update_session, update_line)
    for session in reversed(previous[:update_index]):
        messages.extend(extract_session_messages(session, -1))
    return {"ide": "claude-code", "session": update_session.stem[:8], "file": update_file, "covered": update_index + 1, "messages": messages[-100:]}


def print_session_history(history: dict[str, Any]) -> None:
    messages = history.get("messages", [])
    print(f"\n[planning-with-files] SESSION CATCHUP DETECTED (IDE: {history['ide']})")
    print(f"Last planning update: {history.get('file') or 'unknown'} in session {history.get('session', '????????')}...")
    if int(history.get("covered", 1)) > 1:
        print(f"Scanning {history['covered']} sessions for unsynced context")
    print(f"Unsynced messages: {len(messages)}")
    print("\n--- UNSYNCED CONTEXT ---")
    current_session = None
    for message in messages:
        if message.get("session") != current_session:
            current_session = message.get("session")
            print(f"\n[Session: {current_session}...]")
        if message.get("role") == "user":
            print(f"USER: {message.get('content', '')[:300]}")
        else:
            if message.get("content"):
                print(f"ASSISTANT: {message['content'][:300]}")
            if message.get("tools"):
                print(f"  Tools: {', '.join(message['tools'])}")
    print("\n--- RECOMMENDED ---")
    print("1. Run: git diff --stat")
    print("2. Read: task_plan.md, progress.md, findings.md")
    print("3. Continue from the latest verified planning state")


def main() -> int:
    if os.environ.get("PLANNING_DISABLED") == "1":
        return 0
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project_path", nargs="?", default=".")
    parser.add_argument("--task-id")
    parser.add_argument("--no-session-history", action="store_true")
    args = parser.parse_args()
    start = Path(args.project_path).expanduser().resolve()
    try:
        resolved = layout.resolve_layout(start=start, task_id=args.task_id, require=False)
    except layout.LayoutError as exc:
        print(f"[planning-with-files] LAYOUT BLOCKED: {exc}")
        return 4
    root = resolved.project_root if resolved is not None else start
    present: list[Path] = []
    if resolved is not None:
        present.extend(root / name for name in ROOT_ENTRY_FILES if (root / name).exists())
        present.extend(resolved.path(name) for name in layout.PLANNING_DOCUMENTS if resolved.path(name).exists())
    else:
        present = [root / name for name in CORE_FILES if (root / name).exists()]
    if not present:
        print("[planning-with-files] No core governance files found.")
    else:
        print("[planning-with-files] PROJECT-ROOT CATCHUP")
        print(f"Root: {root}")
        expected = len(ROOT_ENTRY_FILES) + len(layout.PLANNING_DOCUMENTS) if resolved is not None and not resolved.is_legacy else len(CORE_FILES)
        print(f"Core governance files: {len(present)}/{expected}")
        for path in present:
            summary = " | ".join(first_meaningful_lines(path))
            print(f"- {path.name}: {summary[:360]}")
    checklist = resolved.path(workflow.CHECKLIST_NAME) if resolved is not None else root / workflow.CHECKLIST_NAME
    if present and checklist.exists():
        try:
            summary = workflow.checklist_summary(checklist.read_text(encoding="utf-8"))
            metadata = summary["metadata"]
            print("WORKFLOW CHECKLIST")
            print(f"- Current phase: {metadata.get('current_phase', '未登记')}")
            print(f"- Completed: {', '.join(task['ID'] for task in summary['completed']) or 'none'}")
            print(f"- Active mainline: {', '.join(task['ID'] for task in summary['active']) or 'none'}")
            print(f"- Blocked: {', '.join(task['ID'] for task in summary['blocked']) or 'none'}")
            print(f"- Owner agent: {metadata.get('owner_agent') or '未登记'}")
            print(f"- Recommended next task: {summary['recommended_next_task']}")
        except workflow.ContractError as exc:
            print(f"WORKFLOW CHECKLIST: INVALID - {exc}")
    if resolved is not None:
        missing_paths = [str(resolved.path(name).relative_to(root)) for name in layout.PLANNING_DOCUMENTS if not resolved.path(name).exists()]
        missing_paths.extend(name for name in ROOT_ENTRY_FILES if not (root / name).exists())
        missing = missing_paths
    else:
        missing = [name for name in CORE_FILES if not (root / name).exists()]
    if present and missing:
        print("Missing: " + ", ".join(missing))
    readme = root / "README.md"
    if present and readme.exists():
        print("README available as supplementary context.")
    if present and resolved is not None:
        recommended = ["AGENTS.md", "00_PROJECT_INDEX.md"] + [str(resolved.path(name).relative_to(root)) for name in ("1_master_plan.md", "3_status_update.md", "4_handoff.md")]
        print("Recommended: read " + ", ".join(recommended) + "; then inspect Git state.")
    elif present:
        print("Recommended: read AGENTS.md, 00_PROJECT_INDEX.md, 1_master_plan.md, 3_status_update.md, 4_handoff.md; then inspect Git state.")
    if not args.no_session_history:
        history = collect_claude_session_history(root)
        if history:
            print_session_history(history)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
