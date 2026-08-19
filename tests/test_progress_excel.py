import os
import datetime as dt
import shutil
import tempfile
import unittest
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pwf_governed.progress_excel import (
    BOSS_LOG_HEADERS,
    DECISION_HEADERS,
    EXCEL_FILE_NAME,
    PHASE_STEPS_HEADERS,
    PROJECT_OVERVIEW_HEADERS,
    SHEET_BOSS_LOG,
    SHEET_DECISIONS,
    SHEET_PHASE_STEPS,
    SHEET_PROJECT_OVERVIEW,
    OpenXMLWorkbookBuilder,
    generate_progress_excel,
    parse_existing_xlsx,
)


class ProgressExcelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp())
        self.planning_dir = self.temp_dir / "00.项目规划与治理"
        self.planning_dir.mkdir(parents=True, exist_ok=True)
        self.task_dir = self.planning_dir / "TASK-001"
        self.task_dir.mkdir(parents=True, exist_ok=True)

        # Seed sample markdown plan files
        master_plan = self.task_dir / "1_master_plan.md"
        master_plan.write_text(
            """---
title: 测试任务一期
summary: 完成人话版 Excel 进度表开发与验证
owner: mac-codex
status: DOING
phase: P1
updated: 2026-08-19
---

# 测试任务一期

> 实施结果：`FEATURE_IN_PROGRESS`。正在开发中。

## 阶段与 Done Criteria

1. 建立零依赖 OpenXML 引擎与受控解析
2. 实现 00_老板记录与 03_决策待办保护
3. 接入 CLI 与 Checkpoint 钩子
4. 完成全量单测与 12 矩阵 CI
""",
            encoding="utf-8",
        )

        status_update = self.task_dir / "3_status_update.md"
        status_update.write_text(
            """---
title: 测试任务状态更新
status: DOING
updated: 2026-08-19
---

# 测试任务状态更新

| Gate | 状态 | 证据 |
|---|---|---|
| OpenXML Engine | PASS | tests/test_progress_excel.py |
| Boss Log Preservation | DOING | in progress |
""",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_initial_excel_creation(self) -> None:
        """Verify initial creation contains 4 standard sheets with metadata banner and empty boss log."""
        ok, msg = generate_progress_excel(self.temp_dir)
        self.assertTrue(ok, msg)
        excel_path = self.planning_dir / EXCEL_FILE_NAME
        self.assertTrue(excel_path.exists())

        sheets = parse_existing_xlsx(excel_path)
        self.assertIn(SHEET_BOSS_LOG, sheets)
        self.assertIn(SHEET_PROJECT_OVERVIEW, sheets)
        self.assertIn(SHEET_PHASE_STEPS, sheets)
        self.assertIn(SHEET_DECISIONS, sheets)

        # Check 00_老板记录
        boss_rows = sheets[SHEET_BOSS_LOG]
        self.assertEqual(len(boss_rows), 1)
        self.assertEqual(boss_rows[0], BOSS_LOG_HEADERS)

        # Check 01_项目总览
        overview_rows = sheets[SHEET_PROJECT_OVERVIEW]
        self.assertTrue(any("权威来源：Planning Files" in r[0] for r in overview_rows if r))
        self.assertTrue(any("说明：本表为面向老板的可视化进度视图" in r[0] for r in overview_rows if r))
        self.assertIn(PROJECT_OVERVIEW_HEADERS, overview_rows)

        # Check 02_阶段与步骤明细
        steps_rows = sheets[SHEET_PHASE_STEPS]
        self.assertEqual(steps_rows[0], PHASE_STEPS_HEADERS)
        self.assertGreater(len(steps_rows), 1)

        # Check 03_决策与待办
        dec_rows = sheets[SHEET_DECISIONS]
        self.assertEqual(dec_rows[0], DECISION_HEADERS)

    def test_boss_log_preservation_across_refreshes(self) -> None:
        """Verify that user records in 00_老板记录 are 100% preserved with text, dates, order."""
        # 1. Initial creation
        ok, msg = generate_progress_excel(self.temp_dir)
        self.assertTrue(ok, msg)
        excel_path = self.planning_dir / EXCEL_FILE_NAME

        # 2. Simulate boss manually adding 3 records
        sheets = parse_existing_xlsx(excel_path)
        user_rows = [
            ["2026-08-19", "想法", "探索与 Numbers 的集成体验 & 优化表头", "下周讨论", "高", "否"],
            ["2026-08-20", "提醒", "发布 v2.0.0-rc.2 前确认 CI 状态", "检查 GitHub Actions", "紧急", "否"],
            ["2026-08-21", "改进", "支持特殊字符 <xml>&\"' 和多行换行\n第二行", "验证解析", "中", "是"],
        ]
        sheets[SHEET_BOSS_LOG].extend(user_rows)

        # Re-save with user edits
        builder = OpenXMLWorkbookBuilder()
        builder.add_sheet(SHEET_BOSS_LOG, sheets[SHEET_BOSS_LOG])
        builder.add_sheet(SHEET_PROJECT_OVERVIEW, sheets[SHEET_PROJECT_OVERVIEW])
        builder.add_sheet(SHEET_PHASE_STEPS, sheets[SHEET_PHASE_STEPS])
        builder.add_sheet(SHEET_DECISIONS, sheets[SHEET_DECISIONS])
        excel_path.write_bytes(builder.build_zip_bytes())

        # 3. Trigger multiple system refreshes
        for _ in range(3):
            ok, msg = generate_progress_excel(self.temp_dir)
            self.assertTrue(ok, msg)

        # 4. Assert boss rows are 100% preserved
        refreshed_sheets = parse_existing_xlsx(excel_path)
        refreshed_boss_rows = refreshed_sheets[SHEET_BOSS_LOG]
        self.assertEqual(len(refreshed_boss_rows), 4) # 1 header + 3 user rows
        self.assertEqual(refreshed_boss_rows[0], BOSS_LOG_HEADERS)
        self.assertEqual(refreshed_boss_rows[1:], user_rows)

    def test_decision_sheet_hybrid_column_preservation(self) -> None:
        """Verify user-managed columns in 03_决策与待办 are preserved when system columns update."""
        ok, msg = generate_progress_excel(self.temp_dir)
        self.assertTrue(ok, msg)
        excel_path = self.planning_dir / EXCEL_FILE_NAME

        # Read decision sheet and fill boss decision columns
        sheets = parse_existing_xlsx(excel_path)
        dec_rows = sheets[SHEET_DECISIONS]
        # Header: 决策项 | 背景与影响 | 推荐方案 | 老板裁决 | 老板备注 | 裁决时间
        self.assertGreater(len(dec_rows), 1)
        # Fill row 1 user columns
        dec_rows[1][3] = "同意方案B" # 老板裁决
        dec_rows[1][4] = "按标准库方案推进，不引入 openpyxl" # 老板备注
        dec_rows[1][5] = "2026-08-19 22:30" # 裁决时间

        builder = OpenXMLWorkbookBuilder()
        builder.add_sheet(SHEET_BOSS_LOG, sheets[SHEET_BOSS_LOG])
        builder.add_sheet(SHEET_PROJECT_OVERVIEW, sheets[SHEET_PROJECT_OVERVIEW])
        builder.add_sheet(SHEET_PHASE_STEPS, sheets[SHEET_PHASE_STEPS])
        builder.add_sheet(SHEET_DECISIONS, dec_rows)
        excel_path.write_bytes(builder.build_zip_bytes())

        # Refresh Excel
        ok, msg = generate_progress_excel(self.temp_dir)
        self.assertTrue(ok, msg)

        # Assert user columns are preserved
        refreshed_sheets = parse_existing_xlsx(excel_path)
        refreshed_dec_rows = refreshed_sheets[SHEET_DECISIONS]
        self.assertEqual(refreshed_dec_rows[1][3], "同意方案B")
        self.assertEqual(refreshed_dec_rows[1][4], "按标准库方案推进，不引入 openpyxl")
        self.assertEqual(refreshed_dec_rows[1][5], "2026-08-19 22:30")

    def test_semantic_idempotence_across_multiple_runs(self) -> None:
        """Verify that 10 consecutive refreshes produce semantically identical workbook content."""
        ok, msg = generate_progress_excel(self.temp_dir)
        self.assertTrue(ok, msg)
        excel_path = self.planning_dir / EXCEL_FILE_NAME

        baseline_sheets = parse_existing_xlsx(excel_path)

        for _ in range(10):
            ok, msg = generate_progress_excel(self.temp_dir)
            self.assertTrue(ok, msg)
            current_sheets = parse_existing_xlsx(excel_path)

            # Compare sheets semantically
            self.assertEqual(current_sheets[SHEET_BOSS_LOG], baseline_sheets[SHEET_BOSS_LOG])
            self.assertEqual(current_sheets[SHEET_PHASE_STEPS], baseline_sheets[SHEET_PHASE_STEPS])
            self.assertEqual(current_sheets[SHEET_DECISIONS], baseline_sheets[SHEET_DECISIONS])

            # In overview, skip row 1 (contains timestamp "最近同步：...") and compare data table
            self.assertEqual(current_sheets[SHEET_PROJECT_OVERVIEW][4:], baseline_sheets[SHEET_PROJECT_OVERVIEW][4:])

    def test_status_update_isolation(self) -> None:
        """Verify that when task status updates to SEALED, overview updates and boss log remains intact."""
        # Initial creation
        generate_progress_excel(self.temp_dir)
        excel_path = self.planning_dir / EXCEL_FILE_NAME

        # Add boss note
        sheets = parse_existing_xlsx(excel_path)
        sheets[SHEET_BOSS_LOG].append(["2026-08-19", "备忘", "观察封板联动", "", "高", "否"])
        builder = OpenXMLWorkbookBuilder()
        for s_name in (SHEET_BOSS_LOG, SHEET_PROJECT_OVERVIEW, SHEET_PHASE_STEPS, SHEET_DECISIONS):
            builder.add_sheet(s_name, sheets[s_name])
        excel_path.write_bytes(builder.build_zip_bytes())

        # Update markdown status to SEALED in both files
        (self.task_dir / "1_master_plan.md").write_text(
            """---
title: 测试任务一期
summary: 任务已封板
owner: mac-codex
status: SEALED
phase: P1
updated: 2026-08-19
---

# 测试任务一期
""",
            encoding="utf-8",
        )
        (self.task_dir / "3_status_update.md").write_text(
            """---
title: 测试任务状态更新
status: SEALED
updated: 2026-08-19
---

# 测试任务状态更新
""",
            encoding="utf-8",
        )

        # Refresh
        ok, msg = generate_progress_excel(self.temp_dir)
        self.assertTrue(ok, msg)

        # Verify
        refreshed = parse_existing_xlsx(excel_path)
        overview_rows = refreshed[SHEET_PROJECT_OVERVIEW]
        task_row = [r for r in overview_rows if r and r[0] == "TASK-001"][0]
        self.assertEqual(task_row[3], "SEALED")
        self.assertEqual(task_row[4], "100%")

        # Boss log is untouched
        self.assertEqual(len(refreshed[SHEET_BOSS_LOG]), 2)
        self.assertEqual(refreshed[SHEET_BOSS_LOG][1][2], "观察封板联动")

    def test_fail_closed_on_corrupt_file(self) -> None:
        """Verify that if existing xlsx is corrupted, original file is preserved and error reported."""
        excel_path = self.planning_dir / EXCEL_FILE_NAME
        corrupted_content = b"INVALID_CORRUPT_NOT_A_ZIP"
        excel_path.write_bytes(corrupted_content)

        ok, msg = generate_progress_excel(self.temp_dir)
        self.assertFalse(ok)
        self.assertIn("EXCEL_REFRESH_FAILED_PRESERVED", msg)
        # Original corrupted content was NOT overwritten
        self.assertEqual(excel_path.read_bytes(), corrupted_content)


if __name__ == "__main__":
    unittest.main()


class ProgressExcelCLITests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp())
        self.planning_dir = self.temp_dir / "00.项目规划与治理"
        self.planning_dir.mkdir(parents=True, exist_ok=True)
        self.orig_cwd = os.getcwd()
        os.chdir(self.temp_dir)

    def tearDown(self) -> None:
        os.chdir(self.orig_cwd)
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_cli_progress_excel_and_export_excel(self) -> None:
        from pwf_governed.cli import _dispatch

        # 1. Test pwf progress --excel
        exit_code = _dispatch(["progress", "--excel"])
        self.assertEqual(exit_code, 0)
        default_excel = self.planning_dir / EXCEL_FILE_NAME
        self.assertTrue(default_excel.exists())

        # 2. Test pwf export-excel with custom output path
        custom_out = self.temp_dir / "custom_progress.xlsx"
        exit_code2 = _dispatch(["export-excel", "--output", str(custom_out)])
        self.assertEqual(exit_code2, 0)
        self.assertTrue(custom_out.exists())
