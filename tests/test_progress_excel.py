import os
import datetime as dt
import shutil
import tempfile
import unittest
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pwf_governed.progress_excel import (
    BOSS_LOG_HEADERS,
    CANONICAL_PROGRESS_EXCEL,
    DECISION_HEADERS,
    EXCEL_FILE_NAME,
    PHASE_STEPS_HEADERS,
    PROJECT_OVERVIEW_HEADERS,
    SHEET_BOSS_LOG,
    SHEET_DECISIONS,
    SHEET_PHASE_STEPS,
    SHEET_PROJECT_OVERVIEW,
    OpenXMLWorkbookBuilder,
    ensure_required_plan_artifacts,
    generate_progress_excel,
    parse_existing_xlsx,
    validate_required_plan_artifacts,
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

> 实施结果：。正在开发中。

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

## 【老板待裁决区】

- [2026-08-19 | Codex] DEC-001 是否批准进入下一阶段：当前基础已具备，待裁决。
- [2026-08-19 | Codex] C016 已执行完毕：治理收口已完成。
""",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_initial_excel_creation(self) -> None:
        """Verify initial creation contains 4 standard sheets with metadata banner, dashboard, and 8-col steps at canonical root."""
        ok, msg = generate_progress_excel(self.temp_dir)
        self.assertTrue(ok, msg)
        excel_path = self.temp_dir / CANONICAL_PROGRESS_EXCEL
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

        # Check 01_项目总览 (Dashboard)
        overview_rows = sheets[SHEET_PROJECT_OVERVIEW]
        self.assertTrue(any("权威来源：Planning Files" in r[0] for r in overview_rows if r))
        self.assertTrue(any("【核心结论】" in r[0] for r in overview_rows if r))
        self.assertTrue(any("【项目核心概览看板】" in r[0] for r in overview_rows if r))

        # Check 02_阶段与步骤明细 (8 columns)
        steps_rows = sheets[SHEET_PHASE_STEPS]
        self.assertEqual(steps_rows[0], PHASE_STEPS_HEADERS)
        self.assertEqual(len(PHASE_STEPS_HEADERS), 8)
        self.assertGreater(len(steps_rows), 1)

        # Check 03_决策与待办
        dec_rows = sheets[SHEET_DECISIONS]
        self.assertTrue(any("【当前待办与待裁决】" in r[0] for r in dec_rows if r))
        self.assertTrue(any("【历史决策与已执行事项】" in r[0] for r in dec_rows if r))

    def test_boss_log_preservation_across_refreshes(self) -> None:
        """Verify that user records in 00_老板记录 are 100% preserved with text, dates, order."""
        ok, msg = generate_progress_excel(self.temp_dir)
        self.assertTrue(ok, msg)
        excel_path = self.temp_dir / CANONICAL_PROGRESS_EXCEL

        sheets = parse_existing_xlsx(excel_path)
        user_rows = [
            ["2026-08-19", "想法", "探索与 Numbers 的集成体验 & 优化表头", "下周讨论", "高", "否"],
            ["2026-08-20", "提醒", "发布 v2.0.0-rc.4 前确认 CI 状态", "检查 GitHub Actions", "中", "否"],
            ['2026-08-21', '改进', '支持特殊字符 <xml>&\" 和多行换行\n第二行', '验证解析', '低', '是'],
        ]
        sheets[SHEET_BOSS_LOG].extend(user_rows)

        builder = OpenXMLWorkbookBuilder()
        builder.add_sheet(SHEET_BOSS_LOG, sheets[SHEET_BOSS_LOG])
        builder.add_sheet(SHEET_PROJECT_OVERVIEW, sheets[SHEET_PROJECT_OVERVIEW])
        builder.add_sheet(SHEET_PHASE_STEPS, sheets[SHEET_PHASE_STEPS])
        builder.add_sheet(SHEET_DECISIONS, sheets[SHEET_DECISIONS])
        excel_path.write_bytes(builder.build_zip_bytes())

        for _ in range(3):
            ok, msg = generate_progress_excel(self.temp_dir)
            self.assertTrue(ok, msg)

        refreshed_sheets = parse_existing_xlsx(excel_path)
        refreshed_boss_rows = refreshed_sheets[SHEET_BOSS_LOG]
        self.assertEqual(len(refreshed_boss_rows), 4)
        self.assertEqual(refreshed_boss_rows[0], BOSS_LOG_HEADERS)
        self.assertEqual(refreshed_boss_rows[1:], user_rows)

    def test_decision_sheet_hybrid_column_and_key_preservation(self) -> None:
        """Verify user-managed columns in 03_决策与待办 are preserved by stable decision_key."""
        ok, msg = generate_progress_excel(self.temp_dir)
        self.assertTrue(ok, msg)
        excel_path = self.temp_dir / CANONICAL_PROGRESS_EXCEL

        sheets = parse_existing_xlsx(excel_path)
        dec_rows = sheets[SHEET_DECISIONS]

        # Find DEC-001 row
        target_r_idx = None
        for r_i, r in enumerate(dec_rows):
            if r and r[0] == "DEC-001":
                target_r_idx = r_i
                break
        self.assertIsNotNone(target_r_idx)

        dec_rows[target_r_idx][4] = "同意方案B"  # 老板裁决
        dec_rows[target_r_idx][5] = "按标准库方案推进，不引入 openpyxl"  # 老板备注
        dec_rows[target_r_idx][6] = "2026年08月20日 00:23:53"  # 裁决时间

        builder = OpenXMLWorkbookBuilder()
        builder.add_sheet(SHEET_BOSS_LOG, sheets[SHEET_BOSS_LOG])
        builder.add_sheet(SHEET_PROJECT_OVERVIEW, sheets[SHEET_PROJECT_OVERVIEW])
        builder.add_sheet(SHEET_PHASE_STEPS, sheets[SHEET_PHASE_STEPS])
        builder.add_sheet(SHEET_DECISIONS, dec_rows)
        excel_path.write_bytes(builder.build_zip_bytes())

        ok, msg = generate_progress_excel(self.temp_dir)
        self.assertTrue(ok, msg)

        refreshed_sheets = parse_existing_xlsx(excel_path)
        refreshed_dec_rows = refreshed_sheets[SHEET_DECISIONS]

        refreshed_target = None
        for r in refreshed_dec_rows:
            if r and r[0] == "DEC-001":
                refreshed_target = r
                break
        self.assertIsNotNone(refreshed_target)
        self.assertEqual(refreshed_target[4], "同意方案B")
        self.assertEqual(refreshed_target[5], "按标准库方案推进，不引入 openpyxl")
        self.assertEqual(refreshed_target[6], "2026年08月20日 00:23:53")

    def test_semantic_idempotence_across_multiple_runs(self) -> None:
        """Verify that 10 consecutive refreshes produce semantically identical workbook content."""
        ok, msg = generate_progress_excel(self.temp_dir)
        self.assertTrue(ok, msg)
        excel_path = self.temp_dir / CANONICAL_PROGRESS_EXCEL

        baseline_sheets = parse_existing_xlsx(excel_path)

        for _ in range(10):
            ok, msg = generate_progress_excel(self.temp_dir)
            self.assertTrue(ok, msg)
            current_sheets = parse_existing_xlsx(excel_path)

            self.assertEqual(current_sheets[SHEET_BOSS_LOG], baseline_sheets[SHEET_BOSS_LOG])
            self.assertEqual(current_sheets[SHEET_PHASE_STEPS], baseline_sheets[SHEET_PHASE_STEPS])
            self.assertEqual(current_sheets[SHEET_DECISIONS], baseline_sheets[SHEET_DECISIONS])

    def test_status_update_isolation(self) -> None:
        """Verify that when task status updates to SEALED, overview updates and boss log remains intact."""
        generate_progress_excel(self.temp_dir)
        excel_path = self.temp_dir / CANONICAL_PROGRESS_EXCEL

        sheets = parse_existing_xlsx(excel_path)
        sheets[SHEET_BOSS_LOG].append(["2026-08-19", "备忘", "观察封板联动", "", "高", "否"])
        builder = OpenXMLWorkbookBuilder()
        for s_name in (SHEET_BOSS_LOG, SHEET_PROJECT_OVERVIEW, SHEET_PHASE_STEPS, SHEET_DECISIONS):
            builder.add_sheet(s_name, sheets[s_name])
        excel_path.write_bytes(builder.build_zip_bytes())

        # Update markdown status to SEALED
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

        ok, msg = generate_progress_excel(self.temp_dir)
        self.assertTrue(ok, msg)

        refreshed = parse_existing_xlsx(excel_path)
        overview_rows = refreshed[SHEET_PROJECT_OVERVIEW]
        self.assertTrue(any("【核心结论】" in r[0] for r in overview_rows if r))

        # Boss log is untouched
        self.assertEqual(len(refreshed[SHEET_BOSS_LOG]), 2)
        self.assertEqual(refreshed[SHEET_BOSS_LOG][1][2], "观察封板联动")

    def test_fail_closed_on_corrupt_file(self) -> None:
        """Verify that if existing xlsx is corrupted, original file is preserved and error reported."""
        excel_path = self.temp_dir / CANONICAL_PROGRESS_EXCEL
        corrupted_content = b"INVALID_CORRUPT_NOT_A_ZIP"
        excel_path.write_bytes(corrupted_content)

        ok, msg = generate_progress_excel(self.temp_dir)
        self.assertFalse(ok)
        self.assertIn("EXCEL_REFRESH_FAILED_PRESERVED", msg)
        self.assertEqual(excel_path.read_bytes(), corrupted_content)

    def test_validate_required_plan_artifacts_integrity(self) -> None:
        """Verify validate_required_plan_artifacts checks presence, 4 sheets, rels, and boss log."""
        # 1. Missing file
        res = validate_required_plan_artifacts(self.temp_dir)
        self.assertFalse(res["REQUIRED_EXCEL_EXISTS"])
        self.assertFalse(res["REQUIRED_EXCEL_VALID"])

        # 2. Valid file
        ok, msg = generate_progress_excel(self.temp_dir)
        self.assertTrue(ok, msg)
        res_valid = validate_required_plan_artifacts(self.temp_dir)
        self.assertTrue(res_valid["REQUIRED_EXCEL_EXISTS"])
        self.assertTrue(res_valid["REQUIRED_EXCEL_VALID"])

        # 3. Corrupt file
        excel_path = self.temp_dir / CANONICAL_PROGRESS_EXCEL
        excel_path.write_bytes(b"BAD_ZIP_DATA")
        res_corrupt = validate_required_plan_artifacts(self.temp_dir)
        self.assertTrue(res_corrupt["REQUIRED_EXCEL_EXISTS"])
        self.assertFalse(res_corrupt["REQUIRED_EXCEL_VALID"])

    def test_transactional_legacy_migration_with_equivalence(self) -> None:
        """Verify legacy 00.项目规划与治理/ Excel is safely migrated to canonical root with 5 gates."""
        legacy_path = self.planning_dir / CANONICAL_PROGRESS_EXCEL
        # Generate legacy file first
        ok, msg = generate_progress_excel(self.temp_dir, output_path=legacy_path)
        self.assertTrue(ok, msg)
        self.assertTrue(legacy_path.exists())

        # Add boss note in legacy file
        sheets = parse_existing_xlsx(legacy_path)
        sheets[SHEET_BOSS_LOG].append(["2026-08-19", "想法", "历史迁移测试", "保留人工数据", "高", "否"])
        builder = OpenXMLWorkbookBuilder()
        for s_name in (SHEET_BOSS_LOG, SHEET_PROJECT_OVERVIEW, SHEET_PHASE_STEPS, SHEET_DECISIONS):
            builder.add_sheet(s_name, sheets[s_name])
        legacy_path.write_bytes(builder.build_zip_bytes())

        # Now run canonical generation without output_path
        ok, msg = generate_progress_excel(self.temp_dir)
        self.assertTrue(ok, msg)

        canonical_path = self.temp_dir / CANONICAL_PROGRESS_EXCEL
        self.assertTrue(canonical_path.exists())
        # Legacy file should be safely removed after successful verified migration
        self.assertFalse(legacy_path.exists())

        # Verify boss note was preserved in new canonical file
        canonical_sheets = parse_existing_xlsx(canonical_path)
        self.assertEqual(len(canonical_sheets[SHEET_BOSS_LOG]), 2)
        self.assertEqual(canonical_sheets[SHEET_BOSS_LOG][1][2], "历史迁移测试")

    def test_doctor_and_verify_are_strictly_read_only(self) -> None:
        """Verify doctor and verify_plan_summary do NOT write/create Excel files when missing."""
        from pwf_governed._legacy.runtime import doctor
        from pwf_governed.verify import verify_plan_summary

        canonical_path = self.temp_dir / CANONICAL_PROGRESS_EXCEL
        self.assertFalse(canonical_path.exists())

        # Run doctor
        lines = doctor(self.temp_dir)
        self.assertTrue(any("required-excel: missing" in l for l in lines))
        # Verify no file was created
        self.assertFalse(canonical_path.exists())

        # Run verify_plan_summary
        summary = verify_plan_summary(self.task_dir)
        self.assertFalse(summary.get("required_excel_exists", True))
        self.assertFalse(canonical_path.exists())

    def test_lifecycle_auto_creates_excel_without_explicit_flag(self) -> None:
        """Verify ensure_required_plan_artifacts creates canonical excel at project root."""
        canonical_path = self.temp_dir / CANONICAL_PROGRESS_EXCEL
        self.assertFalse(canonical_path.exists())

        ok, msg = ensure_required_plan_artifacts(self.task_dir)
        self.assertTrue(ok, msg)
        self.assertTrue(canonical_path.exists())

    def test_windows_wsl_posix_path_handling(self) -> None:
        """Verify path resolution works with nested paths and trailing slashes."""
        nested = self.task_dir / "sub" / ".."
        ok, msg = generate_progress_excel(nested)
        self.assertTrue(ok, msg)
        canonical_path = self.temp_dir / CANONICAL_PROGRESS_EXCEL
        self.assertTrue(canonical_path.exists())


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

        exit_code = _dispatch(["progress", "--excel"])
        self.assertEqual(exit_code, 0)
        default_excel = self.temp_dir / CANONICAL_PROGRESS_EXCEL
        self.assertTrue(default_excel.exists())

        custom_out = self.temp_dir / "custom_progress.xlsx"
        exit_code2 = _dispatch(["export-excel", "--output", str(custom_out)])
        self.assertEqual(exit_code2, 0)
        self.assertTrue(custom_out.exists())


if __name__ == "__main__":
    unittest.main()
