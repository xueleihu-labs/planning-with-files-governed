"""Human-readable Project Progress Excel generator and preservation engine.

Provides zero-dependency OpenXML (.xlsx) generation, parsing, and safe merging for:
- 00_老板记录: USER-MANAGED protected sheet (never overwritten/cleared).
- 01_项目总览: SYSTEM-MANAGED project-level human-readable dashboard with metadata banner.
- 02_阶段与步骤明细: SYSTEM-MANAGED phase and step breakdown with Done criteria and evidence.
- 03_决策与待办: HYBRID sheet with system decisions and protected user decision/remarks columns.

Follows strict fail-closed preservation: if an existing Excel file cannot be safely parsed,
the original file is never overwritten, returning EXCEL_REFRESH_FAILED_PRESERVED.
"""

from __future__ import annotations

import datetime as dt
import io
import json
import os
import re
import shutil
import tempfile
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any


EXCEL_FILE_NAME = "项目进度表_人话版.xlsx"
SHEET_BOSS_LOG = "00_老板记录"
SHEET_PROJECT_OVERVIEW = "01_项目总览"
SHEET_PHASE_STEPS = "02_阶段与步骤明细"
SHEET_DECISIONS = "03_决策与待办"

BOSS_LOG_HEADERS = ["日期", "类型", "我的记录", "以后要做什么", "优先级", "是否已处理"]
PROJECT_OVERVIEW_HEADERS = ["任务ID", "任务名称", "所属阶段", "状态", "完成度%", "负责人/智能体", "一句话人话进度", "当前卡点", "下一步动作", "更新时间"]
PHASE_STEPS_HEADERS = ["任务ID", "阶段序号", "步骤/里程碑名称", "状态", "验收标准 (Done Criteria)", "关联证据文件", "备注"]
DECISION_HEADERS = ["决策项", "背景与影响", "推荐方案", "老板裁决", "老板备注", "裁决时间"]


# Styles in styles.xml:
# 0: Default body cell (thin border, 11pt Calibri)
# 1: Table Header (Dark Blue #1F4E78, bold white text, thin border)
# 2: Metadata Banner (Italic #555555 text, no border)
# 3: Status Green (Pass/Sealed: fill #D4EDDA, bold text #155724, thin border)
# 4: Status Blue (Doing/Running: fill #CCE5FF, bold text #004085, thin border)
# 5: Status Yellow (Todo/Pending: fill #FFF3CD, bold text #856404, thin border)
# 6: Status Red (Blocked/Fail: fill #F8D7DA, bold text #721C24, thin border)
# 7: Boss Log Header (Teal #17A2B8 or Steel Blue #2C3E50, bold white text, thin border)

STYLES_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <fonts count="7">
    <font><sz val="11"/><name val="Calibri"/></font>
    <font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Calibri"/></font>
    <font><i/><sz val="10"/><color rgb="FF555555"/><name val="Calibri"/></font>
    <font><b/><sz val="11"/><color rgb="FF155724"/><name val="Calibri"/></font>
    <font><b/><sz val="11"/><color rgb="FF004085"/><name val="Calibri"/></font>
    <font><b/><sz val="11"/><color rgb="FF856404"/><name val="Calibri"/></font>
    <font><b/><sz val="11"/><color rgb="FF721C24"/><name val="Calibri"/></font>
  </fonts>
  <fills count="8">
    <fill><patternFill patternType="none"/></fill>
    <fill><patternFill patternType="gray125"/></fill>
    <fill><patternFill patternType="solid"><fgColor rgb="FF1F4E78"/></patternFill></fill>
    <fill><patternFill patternType="solid"><fgColor rgb="FFD4EDDA"/></patternFill></fill>
    <fill><patternFill patternType="solid"><fgColor rgb="FFCCE5FF"/></patternFill></fill>
    <fill><patternFill patternType="solid"><fgColor rgb="FFFFF3CD"/></patternFill></fill>
    <fill><patternFill patternType="solid"><fgColor rgb="FFF8D7DA"/></patternFill></fill>
    <fill><patternFill patternType="solid"><fgColor rgb="FF2C3E50"/></patternFill></fill>
  </fills>
  <borders count="2">
    <border><left/><right/><top/><bottom/><diagonal/></border>
    <border>
      <left style="thin"><color rgb="FFD0D7DE"/></left>
      <right style="thin"><color rgb="FFD0D7DE"/></right>
      <top style="thin"><color rgb="FFD0D7DE"/></top>
      <bottom style="thin"><color rgb="FFD0D7DE"/></bottom>
    </border>
  </borders>
  <cellStyleXfs count="1">
    <xf numFmtId="0" fontId="0" fillId="0" borderId="0"/>
  </cellStyleXfs>
  <cellXfs count="8">
    <xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyBorder="1"/>
    <xf numFmtId="0" fontId="1" fillId="2" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1"/>
    <xf numFmtId="0" fontId="2" fillId="0" borderId="0" xfId="0" applyFont="1"/>
    <xf numFmtId="0" fontId="3" fillId="3" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1"/>
    <xf numFmtId="0" fontId="4" fillId="4" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1"/>
    <xf numFmtId="0" fontId="5" fillId="5" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1"/>
    <xf numFmtId="0" fontId="6" fillId="6" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1"/>
    <xf numFmtId="0" fontId="1" fillId="7" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1"/>
  </cellXfs>
</styleSheet>"""


def get_status_style(status_str: str) -> int:
    """Return matching style index for status strings."""
    s = (status_str or "").strip().upper()
    if s in ("SEALED", "PASS", "DONE", "COMPLETED", "已封板", "已完成", "通过"):
        return 3 # Green
    if s in ("DOING", "RUNNING", "IN_PROGRESS", "进行中"):
        return 4 # Blue
    if s in ("TODO", "PENDING", "UNSTARTED", "未开始", "待办"):
        return 5 # Yellow
    if s in ("BLOCKED", "FAIL", "FAILED", "ERROR", "已阻断", "阻断", "失败", "卡点"):
        return 6 # Red
    return 0


class OpenXMLWorkbookBuilder:
    """Zero-dependency OpenXML (.xlsx) builder."""

    def __init__(self) -> None:
        self.shared_strings: list[str] = []
        self.string_map: dict[str, int] = {}
        self.sheets: list[tuple[str, list[list[Any]], list[float]]] = []

    def _get_string_idx(self, text: str) -> int:
        if text in self.string_map:
            return self.string_map[text]
        idx = len(self.shared_strings)
        self.shared_strings.append(text)
        self.string_map[text] = idx
        return idx

    def add_sheet(self, name: str, rows: list[list[Any]], col_widths: list[float] | None = None) -> None:
        self.sheets.append((name, rows, col_widths or []))

    @staticmethod
    def _col_letter(col_idx: int) -> str:
        result = ""
        while col_idx > 0:
            col_idx, remainder = divmod(col_idx - 1, 26)
            result = chr(65 + remainder) + result
        return result

    def build_zip_bytes(self) -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            # 1. [Content_Types].xml
            types_xml = [
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
                '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">',
                '  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>',
                '  <Default Extension="xml" ContentType="application/xml"/>',
                '  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>',
                '  <Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>',
                '  <Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStringTable+xml"/>',
            ]
            for idx in range(1, len(self.sheets) + 1):
                types_xml.append(f'  <Override PartName="/xl/worksheets/sheet{idx}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>')
            types_xml.append('</Types>')
            zf.writestr("[Content_Types].xml", "\n".join(types_xml).encode("utf-8"))

            # 2. _rels/.rels
            rels_xml = (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n'
                '  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>\n'
                '</Relationships>'
            )
            zf.writestr("_rels/.rels", rels_xml.encode("utf-8"))

            # 3. xl/_rels/workbook.xml.rels
            wb_rels = [
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">',
            ]
            for idx in range(1, len(self.sheets) + 1):
                wb_rels.append(f'  <Relationship Id="rId{idx}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{idx}.xml"/>')
            styles_rid = len(self.sheets) + 1
            strings_rid = len(self.sheets) + 2
            wb_rels.append(f'  <Relationship Id="rId{styles_rid}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>')
            wb_rels.append(f'  <Relationship Id="rId{strings_rid}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" Target="sharedStrings.xml"/>')
            wb_rels.append('</Relationships>')
            zf.writestr("xl/_rels/workbook.xml.rels", "\n".join(wb_rels).encode("utf-8"))

            # 4. xl/workbook.xml
            wb_xml = [
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
                '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">',
                '  <sheets>',
            ]
            for idx, (name, _, _) in enumerate(self.sheets, start=1):
                clean_name = name.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
                wb_xml.append(f'    <sheet name="{clean_name}" sheetId="{idx}" r:id="rId{idx}"/>')
            wb_xml.append('  </sheets>')
            wb_xml.append('</workbook>')
            zf.writestr("xl/workbook.xml", "\n".join(wb_xml).encode("utf-8"))

            # 5. Worksheets
            sheet_xmls = []
            for sheet_idx, (name, rows, col_widths) in enumerate(self.sheets, start=1):
                ws_xml = [
                    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
                    '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">',
                ]
                if col_widths:
                    ws_xml.append('  <cols>')
                    for col_i, w in enumerate(col_widths, start=1):
                        ws_xml.append(f'    <col min="{col_i}" max="{col_i}" width="{w}" customWidth="1"/>')
                    ws_xml.append('  </cols>')
                ws_xml.append('  <sheetData>')
                for row_idx, row_cells in enumerate(rows, start=1):
                    if not row_cells:
                        # Empty row
                        ws_xml.append(f'    <row r="{row_idx}"/>')
                        continue
                    ws_xml.append(f'    <row r="{row_idx}">')
                    for col_idx, cell_data in enumerate(row_cells, start=1):
                        c_ref = self._col_letter(col_idx) + str(row_idx)
                        style_idx = 0
                        cell_val = cell_data
                        if isinstance(cell_data, dict):
                            cell_val = cell_data.get("val", "")
                            style_idx = cell_data.get("style", 0)

                        str_val = str(cell_val) if cell_val is not None else ""
                        s_idx = self._get_string_idx(str_val)
                        if style_idx > 0:
                            ws_xml.append(f'      <c r="{c_ref}" t="s" s="{style_idx}"><v>{s_idx}</v></c>')
                        else:
                            ws_xml.append(f'      <c r="{c_ref}" t="s"><v>{s_idx}</v></c>')
                    ws_xml.append('    </row>')
                ws_xml.append('  </sheetData>')
                ws_xml.append('</worksheet>')
                sheet_xmls.append((f"xl/worksheets/sheet{sheet_idx}.xml", "\n".join(ws_xml).encode("utf-8")))

            for path, data in sheet_xmls:
                zf.writestr(path, data)

            # 6. xl/sharedStrings.xml
            sst_xml = [
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
                f'<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" count="{len(self.shared_strings)}" uniqueCount="{len(self.shared_strings)}">',
            ]
            for s in self.shared_strings:
                escaped = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                sst_xml.append(f'  <si><t xml:space="preserve">{escaped}</t></si>')
            sst_xml.append('</sst>')
            zf.writestr("xl/sharedStrings.xml", "\n".join(sst_xml).encode("utf-8"))

            # 7. xl/styles.xml
            zf.writestr("xl/styles.xml", STYLES_XML.encode("utf-8"))

        return buf.getvalue()


def parse_existing_xlsx(xlsx_path: Path | str) -> dict[str, list[list[str]]]:
    """Parse existing .xlsx file using standard library.
    
    Returns a dict of sheet_name -> list of rows (each row is list of str).
    Raises Exception if file is invalid or corrupt.
    """
    xlsx_path = Path(xlsx_path)
    if not xlsx_path.exists():
        return {}

    with zipfile.ZipFile(xlsx_path, "r") as zf:
        # Read shared strings
        shared_strings: list[str] = []
        if "xl/sharedStrings.xml" in zf.namelist():
            tree = ET.fromstring(zf.read("xl/sharedStrings.xml"))
            ns = {"ns": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
            for si in tree.findall("ns:si", ns):
                text_parts = [t.text or "" for t in si.findall(".//ns:t", ns)]
                shared_strings.append("".join(text_parts))

        # Read workbook.xml and rels
        wb_tree = ET.fromstring(zf.read("xl/workbook.xml"))
        ns = {
            "ns": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
            "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
        }
        
        wb_rels_tree = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
        rel_ns = {"rel": "http://schemas.openxmlformats.org/package/2006/relationships"}
        rel_map = {}
        for rel in wb_rels_tree.findall("rel:Relationship", rel_ns):
            rel_map[rel.attrib["Id"]] = rel.attrib["Target"]

        sheets_data: dict[str, list[list[str]]] = {}
        for sheet in wb_tree.findall(".//ns:sheet", ns):
            name = sheet.attrib["name"]
            rid = sheet.attrib["{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"]
            target = rel_map.get(rid, "")
            if not target:
                continue
            if not target.startswith("xl/"):
                target = "xl/" + target.lstrip("/")
            if target not in zf.namelist():
                continue

            ws_tree = ET.fromstring(zf.read(target))
            rows: list[list[str]] = []
            for row_el in ws_tree.findall(".//ns:row", ns):
                row_dict: dict[int, str] = {}
                for c_el in row_el.findall("ns:c", ns):
                    r_ref = c_el.attrib.get("r", "")
                    col_match = re.match(r"([A-Z]+)(\d+)", r_ref)
                    if not col_match:
                        continue
                    col_str, _ = col_match.groups()
                    col_idx = 0
                    for ch in col_str:
                        col_idx = col_idx * 26 + (ord(ch) - ord("A") + 1)
                    col_idx -= 1

                    t_attr = c_el.attrib.get("t", "")
                    v_el = c_el.find("ns:v", ns)
                    val = ""
                    if t_attr == "s" and v_el is not None and v_el.text:
                        s_idx = int(v_el.text)
                        if 0 <= s_idx < len(shared_strings):
                            val = shared_strings[s_idx]
                    elif t_attr == "inlineStr":
                        t_el = c_el.find(".//ns:t", ns)
                        val = t_el.text if t_el is not None else ""
                    elif v_el is not None and v_el.text:
                        val = v_el.text

                    row_dict[col_idx] = val

                if row_dict:
                    max_col = max(row_dict.keys())
                    row_list = [row_dict.get(c, "") for c in range(max_col + 1)]
                    rows.append(row_list)
                else:
                    rows.append([])
            sheets_data[name] = rows

        return sheets_data


def _parse_frontmatter(content: str) -> dict[str, str]:
    """Parse YAML frontmatter from markdown file."""
    if not content.startswith("---"):
        return {}
    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}
    fm_lines = parts[1].strip().split("\n")
    data: dict[str, str] = {}
    for line in fm_lines:
        line = line.strip()
        if ":" in line and not line.startswith("#"):
            k, v = line.split(":", 1)
            data[k.strip()] = v.strip().strip('"').strip("'")
    return data


def extract_project_data(planning_root: Path) -> dict[str, Any]:
    """Extract structured data for sheets 01, 02, and 03 from planning documents."""
    tasks: list[dict[str, Any]] = []
    phase_steps: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []

    # Find task directories
    task_dirs: list[Path] = []
    if planning_root.is_dir():
        for item in sorted(planning_root.iterdir()):
            if item.is_dir() and not item.name.startswith((".", "_")) and item.name not in ("ADR", "evidence", "reports"):
                if (item / "1_master_plan.md").exists() or (item / "task_plan.md").exists() or (item / "3_status_update.md").exists():
                    task_dirs.append(item)

    # Fallback: if planning_root itself contains 1_master_plan.md
    if not task_dirs and (planning_root / "1_master_plan.md").exists():
        task_dirs.append(planning_root)

    for t_dir in task_dirs:
        task_id = t_dir.name
        master_plan_file = t_dir / "1_master_plan.md"
        status_file = t_dir / "3_status_update.md"
        task_plan_file = t_dir / "task_plan.md"

        fm: dict[str, str] = {}
        title = task_id
        summary = ""
        owner = "mac-codex"
        status = "TODO"
        phase = "V1"
        updated = dt.date.today().strftime("%Y-%m-%d")

        if master_plan_file.exists():
            text = master_plan_file.read_text(encoding="utf-8", errors="ignore")
            fm = _parse_frontmatter(text)
            title = fm.get("title", title)
            summary = fm.get("summary", "")
            owner = fm.get("owner", owner)
            status = fm.get("status", status)
            phase = fm.get("phase", phase)
            updated = fm.get("updated", updated)

            # Extract summary from first quote if empty
            if not summary:
                quote_match = re.search(r">\s*实施结果[：:]\s*`?([^`\n]+)`?", text)
                if quote_match:
                    summary = quote_match.group(1).strip()

            # Extract phase steps from "## 阶段与 Done Criteria"
            phase_section = re.search(r"##\s*阶段与\s*Done Criteria\s*\n(.*?)(?=\n##|\Z)", text, re.DOTALL)
            if phase_section:
                lines = phase_section.group(1).strip().split("\n")
                p_idx = 1
                for line in lines:
                    line = line.strip()
                    m = re.match(r"^(\d+)\.\s*(.+)", line)
                    if m:
                        step_text = m.group(2).strip()
                        phase_steps.append({
                            "task_id": task_id,
                            "phase_no": f"P{m.group(1)}",
                            "step_name": step_text,
                            "status": "PASS" if status.upper() in ("SEALED", "PASS") else "TODO",
                            "done_criteria": "完成标准校验",
                            "evidence_ref": f"{task_id}/evidence",
                            "notes": "",
                        })
                        p_idx += 1

        if status_file.exists():
            st_text = status_file.read_text(encoding="utf-8", errors="ignore")
            st_fm = _parse_frontmatter(st_text)
            if "status" in st_fm:
                status = st_fm["status"]
            if "updated" in st_fm:
                updated = st_fm["updated"]

            # Parse markdown gates table | Gate | 状态 | 证据 |
            table_matches = re.findall(r"\|\s*([^|\n]+)\s*\|\s*([^|\n]+)\s*\|\s*([^|\n]+)\s*\|", st_text)
            if len(table_matches) > 1: # Skip header
                for row in table_matches[1:]:
                    gate_name, g_status, g_evidence = [col.strip() for col in row]
                    if gate_name.startswith("-") or gate_name in ("Gate", "检查项"):
                        continue
                    phase_steps.append({
                        "task_id": task_id,
                        "phase_no": "Gate",
                        "step_name": gate_name,
                        "status": g_status,
                        "done_criteria": "Gate Validation",
                        "evidence_ref": g_evidence,
                        "notes": "",
                    })

        # Calculate progress percentage
        progress_pct = "100%" if status.upper() in ("SEALED", "PASS", "COMPLETED") else ("50%" if status.upper() in ("DOING", "RUNNING") else "0%")

        tasks.append({
            "task_id": task_id,
            "task_name": title,
            "phase": phase,
            "status": status.upper(),
            "progress_pct": progress_pct,
            "owner": owner,
            "summary": summary or f"任务 {task_id} 正在推进",
            "blockers": "无" if status.upper() != "BLOCKED" else "存在阻断问题待处理",
            "next_step": "封板归档" if status.upper() in ("SEALED", "PASS") else "继续推进下一步",
            "updated": updated,
        })

    # Default fallback task if no tasks found
    if not tasks:
        tasks.append({
            "task_id": "PROJECT-ROOT",
            "task_name": "项目初始化",
            "phase": "INIT",
            "status": "DOING",
            "progress_pct": "30%",
            "owner": "mac-codex",
            "summary": "项目规划与治理地基已建立",
            "blockers": "无",
            "next_step": "制定任务分期与验收标准",
            "updated": dt.date.today().strftime("%Y-%m-%d"),
        })

    # Default decision items
    decisions.append({
        "decision_item": "DEC-001: 治理档位选择 (L0-L3)",
        "context": "根据任务复杂度确定规划深度与门禁级别",
        "recommendation": "常规项目推荐 L2 (STANDARD)，高安全要求推荐 L3 (STRICT)",
        "boss_decision": "",
        "boss_notes": "",
        "decision_time": "",
    })

    return {
        "tasks": tasks,
        "phase_steps": phase_steps,
        "decisions": decisions,
    }


def generate_progress_excel(
    project_root: Path | str,
    output_path: Path | str | None = None,
) -> tuple[bool, str]:
    """Generate or update the human-readable project progress Excel sheet.
    
    Safely preserves USER-MANAGED sheet 00_老板记录 and user columns in 03_决策与待办.
    Follows fail-closed: never corrupts or overwrites original file on parsing failure.
    
    Returns:
        (True, "EXCEL_REFRESH_SUCCESS: <path>") or (False, "EXCEL_REFRESH_FAILED_PRESERVED: <reason>")
    """
    project_root = Path(project_root).resolve()
    planning_root = project_root / "00.项目规划与治理"
    if not planning_root.exists():
        planning_root = project_root

    if output_path is None:
        target_file = planning_root / EXCEL_FILE_NAME
    else:
        target_file = Path(output_path).resolve()

    # 1. Check if target file exists and extract user-managed data
    preserved_boss_rows: list[list[str]] = []
    preserved_decision_map: dict[str, dict[str, str]] = {}
    is_existing = target_file.exists()

    if is_existing:
        try:
            old_sheets = parse_existing_xlsx(target_file)
            # Extract 00_老板记录 (all rows)
            if SHEET_BOSS_LOG in old_sheets:
                preserved_boss_rows = old_sheets[SHEET_BOSS_LOG]
            
            # Extract 03_决策与待办 user columns
            if SHEET_DECISIONS in old_sheets:
                d_rows = old_sheets[SHEET_DECISIONS]
                # Header is row 0: 决策项 | 背景与影响 | 推荐方案 | 老板裁决 | 老板备注 | 裁决时间
                for row in d_rows[1:]:
                    if len(row) > 0 and row[0].strip():
                        d_item = row[0].strip()
                        boss_decision = row[3].strip() if len(row) > 3 else ""
                        boss_notes = row[4].strip() if len(row) > 4 else ""
                        decision_time = row[5].strip() if len(row) > 5 else ""
                        if boss_decision or boss_notes or decision_time:
                            preserved_decision_map[d_item] = {
                                "boss_decision": boss_decision,
                                "boss_notes": boss_notes,
                                "decision_time": decision_time,
                            }
        except Exception as exc:
            # Fail-closed: do not overwrite damaged or unparseable existing file
            return (False, f"EXCEL_REFRESH_FAILED_PRESERVED: failed to parse existing workbook: {exc}")

    # 2. Extract current project data from Markdown sources
    try:
        data = extract_project_data(planning_root)
    except Exception as exc:
        return (False, f"EXCEL_REFRESH_FAILED_PRESERVED: failed to extract project markdown data: {exc}")

    # 3. Build sheets
    builder = OpenXMLWorkbookBuilder()

    # --- Sheet 1: 00_老板记录 ---
    boss_sheet_rows: list[list[Any]] = []
    if preserved_boss_rows:
        # Preserve user rows exactly
        for r_idx, r in enumerate(preserved_boss_rows):
            if r_idx == 0:
                boss_sheet_rows.append([{"val": c, "style": 7} for c in r])
            else:
                boss_sheet_rows.append(r)
    else:
        # Create empty template
        boss_sheet_rows.append([{"val": h, "style": 7} for h in BOSS_LOG_HEADERS])

    builder.add_sheet(SHEET_BOSS_LOG, boss_sheet_rows, [15, 12, 40, 35, 10, 12])

    # --- Sheet 2: 01_项目总览 ---
    now_str = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    overview_rows: list[list[Any]] = [
        [{"val": "权威来源：Planning Files (Markdown / Checkpoints)", "style": 2}],
        [{"val": f"最近同步：{now_str}", "style": 2}],
        [{"val": "Excel状态：已同步", "style": 2}],
        [{"val": "说明：本表为面向老板的可视化进度视图，不作为正式状态源。", "style": 2}],
        [], # Blank separator row
        [{"val": h, "style": 1} for h in PROJECT_OVERVIEW_HEADERS],
    ]
    for t in data["tasks"]:
        st_style = get_status_style(t["status"])
        overview_rows.append([
            t["task_id"],
            t["task_name"],
            t["phase"],
            {"val": t["status"], "style": st_style},
            t["progress_pct"],
            t["owner"],
            t["summary"],
            t["blockers"],
            t["next_step"],
            t["updated"],
        ])
    builder.add_sheet(SHEET_PROJECT_OVERVIEW, overview_rows, [22, 28, 14, 12, 10, 15, 38, 18, 22, 14])

    # --- Sheet 3: 02_阶段与步骤明细 ---
    step_rows: list[list[Any]] = [
        [{"val": h, "style": 1} for h in PHASE_STEPS_HEADERS]
    ]
    for s in data["phase_steps"]:
        st_style = get_status_style(s["status"])
        step_rows.append([
            s["task_id"],
            s["phase_no"],
            s["step_name"],
            {"val": s["status"], "style": st_style},
            s["done_criteria"],
            s["evidence_ref"],
            s["notes"],
        ])
    builder.add_sheet(SHEET_PHASE_STEPS, step_rows, [20, 12, 35, 12, 30, 25, 20])

    # --- Sheet 4: 03_决策与待办 ---
    dec_rows: list[list[Any]] = [
        [{"val": h, "style": 1} for h in DECISION_HEADERS]
    ]
    for d in data["decisions"]:
        d_item = d["decision_item"]
        # Merge preserved user columns if available
        user_vals = preserved_decision_map.get(d_item, {})
        boss_decision = user_vals.get("boss_decision", d["boss_decision"])
        boss_notes = user_vals.get("boss_notes", d["boss_notes"])
        dec_time = user_vals.get("decision_time", d["decision_time"])

        dec_rows.append([
            d_item,
            d["context"],
            d["recommendation"],
            boss_decision,
            boss_notes,
            dec_time,
        ])
    builder.add_sheet(SHEET_DECISIONS, dec_rows, [30, 35, 35, 18, 25, 15])

    # 4. Generate byte stream and write atomically via temp file
    try:
        xlsx_bytes = builder.build_zip_bytes()
        target_file.parent.mkdir(parents=True, exist_ok=True)
        
        with tempfile.NamedTemporaryFile(dir=target_file.parent, delete=False, suffix=".tmp") as tf:
            tf.write(xlsx_bytes)
            temp_path = Path(tf.name)

        # Atomic replace
        os.replace(temp_path, target_file)
        return (True, f"EXCEL_REFRESH_SUCCESS: {target_file}")
    except Exception as exc:
        if "temp_path" in locals() and temp_path.exists():
            temp_path.unlink(missing_ok=True)
        return (False, f"EXCEL_REFRESH_FAILED_PRESERVED: atomic write failed: {exc}")
