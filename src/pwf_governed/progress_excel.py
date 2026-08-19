"""Human-readable Project Progress Excel generator and preservation engine (rc.4).

Pure Python standard-library OpenXML (.xlsx) engine (zero third-party dependencies):
- Strict SpreadsheetML schema compliance across Windows Office, Apple Numbers, and WPS.
- Native OpenXML features: Freeze Panes, AutoFilter, Data Validations, WrapText, and direct cell styling.

Sheets:
- 00_老板记录: USER-MANAGED protected sheet with freeze pane, A1:F1000 autofilter, and 3 data validation dropdowns.
- 01_项目总览: SYSTEM-MANAGED executive dashboard with top metadata banner, core conclusion, and key-value overview.
- 02_阶段与步骤明细: SYSTEM-MANAGED 8-column phase & step breakdown with strictly normalized Chinese statuses.
- 03_决策与待办: HYBRID sheet split into Active Decisions (with autofilter) and Historical Decisions, with latest-effective-state wins deduplication.

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

CANONICAL_PROGRESS_EXCEL = "项目进度表_人话版.xlsx"
EXCEL_FILE_NAME = CANONICAL_PROGRESS_EXCEL
SHEET_BOSS_LOG = "00_老板记录"
SHEET_PROJECT_OVERVIEW = "01_项目总览"
SHEET_PHASE_STEPS = "02_阶段与步骤明细"
SHEET_DECISIONS = "03_决策与待办"

BOSS_LOG_HEADERS = ["日期", "类型", "我的记录", "以后要做什么", "优先级", "是否已处理"]
PROJECT_OVERVIEW_HEADERS = ["任务ID", "任务名称", "所属阶段", "状态", "完成度%", "负责人/智能体", "一句话人话进度", "当前卡点", "下一步动作", "更新时间"]
PHASE_STEPS_HEADERS = ["任务ID", "阶段序号", "类型", "步骤/里程碑名称", "状态", "验收标准 (Done Criteria)", "关联证据文件", "备注"]
DECISION_HEADERS = ["决策ID", "决策项", "背景与影响", "推荐方案", "老板裁决", "老板备注", "裁决时间"]

# Style Indices:
# 0: Normal data cell (font YaHei 10, thin border, wrapText)
# 1: Standard Table Header (font YaHei 11 bold white, fill navy blue #1F4E78, center, wrapText)
# 2: Meta Info row (font YaHei 10 italic gray #555555, no border)
# 3: Status Green / PASS / 已完成 (font YaHei 10 bold green, fill light green #D4EDDA, center)
# 4: Status Blue / DOING / 进行中 (font YaHei 10 bold blue, fill light blue #CCE5FF, center)
# 5: Status Yellow / TODO / 待处理 (font YaHei 10 bold brown/yellow, fill light yellow #FFF3CD, center)
# 6: Status Red / BLOCKED / 已阻塞 (font YaHei 10 bold red, fill light red #F8D7DA, center)
# 7: Boss Sheet Header (font YaHei 11 bold white, fill dark slate #2C3E50, center, wrapText)
# 8: Section Title Banner (font YaHei 11 bold dark navy #1F4E78, fill light gray #E9ECEF, left, wrapText)
# 9: Dashboard Card Key / Label (font YaHei 10 bold navy #1F4E78, fill #F8F9FA, right, wrapText)
# 10: Dashboard Card Value (font YaHei 10, fill none, left, wrapText)
# 11: Core Conclusion Banner (font YaHei 11 bold navy #1F4E78, fill #E8F4F8, left, wrapText)

STYLES_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <fonts count="9">
    <font><sz val="10"/><name val="Microsoft YaHei"/><family val="2"/></font>
    <font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Microsoft YaHei"/><family val="2"/></font>
    <font><i/><sz val="10"/><color rgb="FF555555"/><name val="Microsoft YaHei"/><family val="2"/></font>
    <font><b/><sz val="10"/><color rgb="FF155724"/><name val="Microsoft YaHei"/><family val="2"/></font>
    <font><b/><sz val="10"/><color rgb="FF004085"/><name val="Microsoft YaHei"/><family val="2"/></font>
    <font><b/><sz val="10"/><color rgb="FF856404"/><name val="Microsoft YaHei"/><family val="2"/></font>
    <font><b/><sz val="10"/><color rgb="FF721C24"/><name val="Microsoft YaHei"/><family val="2"/></font>
    <font><b/><sz val="11"/><color rgb="FF1F4E78"/><name val="Microsoft YaHei"/><family val="2"/></font>
    <font><b/><sz val="11"/><color rgb="FF0B5394"/><name val="Microsoft YaHei"/><family val="2"/></font>
  </fonts>
  <fills count="11">
    <fill><patternFill patternType="none"/></fill>
    <fill><patternFill patternType="gray125"/></fill>
    <fill><patternFill patternType="solid"><fgColor rgb="FF1F4E78"/><bgColor indexed="64"/></patternFill></fill>
    <fill><patternFill patternType="solid"><fgColor rgb="FFD4EDDA"/><bgColor indexed="64"/></patternFill></fill>
    <fill><patternFill patternType="solid"><fgColor rgb="FFCCE5FF"/><bgColor indexed="64"/></patternFill></fill>
    <fill><patternFill patternType="solid"><fgColor rgb="FFFFF3CD"/><bgColor indexed="64"/></patternFill></fill>
    <fill><patternFill patternType="solid"><fgColor rgb="FFF8D7DA"/><bgColor indexed="64"/></patternFill></fill>
    <fill><patternFill patternType="solid"><fgColor rgb="FF2C3E50"/><bgColor indexed="64"/></patternFill></fill>
    <fill><patternFill patternType="solid"><fgColor rgb="FFE9ECEF"/><bgColor indexed="64"/></patternFill></fill>
    <fill><patternFill patternType="solid"><fgColor rgb="FFF8F9FA"/><bgColor indexed="64"/></patternFill></fill>
    <fill><patternFill patternType="solid"><fgColor rgb="FFE8F4F8"/><bgColor indexed="64"/></patternFill></fill>
  </fills>
  <borders count="2">
    <border><left/><right/><top/><bottom/><diagonal/></border>
    <border>
      <left style="thin"><color rgb="FFD0D7DE"/></left>
      <right style="thin"><color rgb="FFD0D7DE"/></right>
      <top style="thin"><color rgb="FFD0D7DE"/></top>
      <bottom style="thin"><color rgb="FFD0D7DE"/></bottom>
      <diagonal/>
    </border>
  </borders>
  <cellStyleXfs count="1">
    <xf numFmtId="0" fontId="0" fillId="0" borderId="0"/>
  </cellStyleXfs>
  <cellXfs count="12">
    <xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment vertical="center" wrapText="1"/></xf>
    <xf numFmtId="0" fontId="1" fillId="2" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment horizontal="center" vertical="center" wrapText="1"/></xf>
    <xf numFmtId="0" fontId="2" fillId="0" borderId="0" xfId="0" applyFont="1" applyFill="1" applyBorder="0" applyAlignment="1"><alignment vertical="center"/></xf>
    <xf numFmtId="0" fontId="3" fillId="3" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment horizontal="center" vertical="center"/></xf>
    <xf numFmtId="0" fontId="4" fillId="4" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment horizontal="center" vertical="center"/></xf>
    <xf numFmtId="0" fontId="5" fillId="5" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment horizontal="center" vertical="center"/></xf>
    <xf numFmtId="0" fontId="6" fillId="6" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment horizontal="center" vertical="center"/></xf>
    <xf numFmtId="0" fontId="1" fillId="7" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment horizontal="center" vertical="center" wrapText="1"/></xf>
    <xf numFmtId="0" fontId="7" fillId="8" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment vertical="center" wrapText="1"/></xf>
    <xf numFmtId="0" fontId="7" fillId="9" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment horizontal="right" vertical="center" wrapText="1"/></xf>
    <xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment vertical="center" wrapText="1"/></xf>
    <xf numFmtId="0" fontId="8" fillId="10" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment vertical="center" wrapText="1"/></xf>
  </cellXfs>
  <cellStyles count="1">
    <cellStyle name="Normal" xfId="0" builtinId="0"/>
  </cellStyles>
</styleSheet>"""


def get_status_style(status_str: str) -> int:
    """Return matching style index for status strings."""
    s = (status_str or "").strip().upper()
    if any(k in s for k in ("SEALED", "PASS", "DONE", "COMPLETED", "已封板", "已完成", "通过", "全实现")):
        return 3  # Green
    if any(k in s for k in ("DOING", "RUNNING", "IN_PROGRESS", "进行中", "执行中")):
        return 4  # Blue
    if any(k in s for k in ("TODO", "PENDING", "UNSTARTED", "未开始", "待办", "待处理", "排队")):
        return 5  # Yellow
    if any(k in s for k in ("BLOCKED", "FAIL", "FAILED", "ERROR", "已阻断", "阻断", "已阻塞", "阻塞", "失败", "卡点", "异常")):
        return 6  # Red
    return 0



def _format_chinese_datetime(d: dt.datetime | None = None) -> str:
    """Format datetime as YYYY年MM月DD日 HH:MM:SS portably without C runtime strftime encoding issues."""
    d = d or dt.datetime.now()
    return f"{d.year}年{d.month:02d}月{d.day:02d}日 {d.hour:02d}:{d.minute:02d}:{d.second:02d}"

def format_status_chinese(status_str: str, evidence: str = "") -> str:
    """Strictly normalize any status text into one of: 已完成 / 进行中 / 待处理 / 已阻塞."""
    s = (status_str or "").strip().upper()
    e = (evidence or "").strip()
    if e == "✅" or any(k in s for k in ("SEALED", "PASS", "DONE", "COMPLETED", "已封板", "已完成", "通过", "全实现", "已修正", "11 必需字段", "有效")):
        return "已完成"
    if any(k in s for k in ("DOING", "RUNNING", "IN_PROGRESS", "进行中", "执行中")):
        return "进行中"
    if any(k in s for k in ("BLOCKED", "FAIL", "FAILED", "ERROR", "已阻断", "阻断", "已阻塞", "阻塞", "失败", "卡点", "异常")):
        return "已阻塞"
    return "待处理"


class OpenXMLWorkbookBuilder:
    """Zero-dependency pure Python OpenXML (.xlsx) builder conforming strictly to SpreadsheetML schema."""

    def __init__(self) -> None:
        self.sheets: list[dict[str, Any]] = []

    def add_sheet(
        self,
        name: str,
        rows: list[list[Any]],
        col_widths: list[float] | None = None,
        freeze_row: int | None = None,
        auto_filter_ref: str | None = None,
        data_validations: list[dict[str, str]] | None = None,
    ) -> None:
        self.sheets.append({
            "name": name,
            "rows": rows,
            "col_widths": col_widths or [],
            "freeze_row": freeze_row,
            "auto_filter_ref": auto_filter_ref,
            "data_validations": data_validations or [],
        })

    @staticmethod
    def _col_letter(col_idx: int) -> str:
        result = ""
        while col_idx > 0:
            col_idx, remainder = divmod(col_idx - 1, 26)
            result = chr(65 + remainder) + result
        return result

    def build_zip_bytes(self) -> bytes:
        buf = io.BytesIO()
        now_iso = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            # 1. [Content_Types].xml
            types_xml = [
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
                '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">',
                '  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>',
                '  <Default Extension="xml" ContentType="application/xml"/>',
                '  <Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>',
                '  <Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>',
                '  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>',
                '  <Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>',
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
                '  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>\n'
                '  <Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>\n'
                '</Relationships>'
            )
            zf.writestr("_rels/.rels", rels_xml.encode("utf-8"))

            # 3. docProps/core.xml
            core_xml = (
                f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                f'<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
                f'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
                f'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">\n'
                f'  <dc:title>项目进度表_人话版</dc:title>\n'
                f'  <dc:creator>Planning-with-Files</dc:creator>\n'
                f'  <cp:lastModifiedBy>Planning-with-Files</cp:lastModifiedBy>\n'
                f'  <dcterms:created xsi:type="dcterms:W3CDTF">{now_iso}</dcterms:created>\n'
                f'  <dcterms:modified xsi:type="dcterms:W3CDTF">{now_iso}</dcterms:modified>\n'
                f'</cp:coreProperties>'
            )
            zf.writestr("docProps/core.xml", core_xml.encode("utf-8"))

            # 4. docProps/app.xml
            sheet_names_xml = "".join(f"<vt:lpstr>{s['name']}</vt:lpstr>" for s in self.sheets)
            app_xml = (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">\n'
                '  <Application>Microsoft Excel</Application>\n'
                '  <DocSecurity>0</DocSecurity>\n'
                '  <ScaleCrop>false</ScaleCrop>\n'
                f'  <HeadingPairs><vt:vector size="2" baseType="variant"><vt:variant><vt:lpstr>Worksheets</vt:lpstr></vt:variant><vt:variant><vt:i4>{len(self.sheets)}</vt:i4></vt:variant></vt:vector></HeadingPairs>\n'
                f'  <TitlesOfParts><vt:vector size="{len(self.sheets)}" baseType="lpstr">{sheet_names_xml}</vt:vector></TitlesOfParts>\n'
                '  <Company></Company>\n'
                '  <LinksUpToDate>false</LinksUpToDate>\n'
                '  <SharedDoc>false</SharedDoc>\n'
                '  <HyperlinksChanged>false</HyperlinksChanged>\n'
                '  <AppVersion>16.0300</AppVersion>\n'
                '</Properties>'
            )
            zf.writestr("docProps/app.xml", app_xml.encode("utf-8"))

            # 5. xl/_rels/workbook.xml.rels
            wb_rels = [
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">',
            ]
            for idx in range(1, len(self.sheets) + 1):
                wb_rels.append(f'  <Relationship Id="rId{idx}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{idx}.xml"/>')
            styles_rid = len(self.sheets) + 1
            wb_rels.append(f'  <Relationship Id="rId{styles_rid}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>')
            wb_rels.append('</Relationships>')
            zf.writestr("xl/_rels/workbook.xml.rels", "\n".join(wb_rels).encode("utf-8"))

            # 6. xl/workbook.xml
            wb_xml = [
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
                '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">',
                '  <workbookPr/>',
                '  <sheets>',
            ]
            for idx, s_info in enumerate(self.sheets, start=1):
                clean_name = s_info["name"].replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
                wb_xml.append(f'    <sheet name="{clean_name}" sheetId="{idx}" r:id="rId{idx}"/>')
            wb_xml.append('  </sheets>')
            wb_xml.append('  <calcPr calcId="124519" fullCalcOnLoad="1"/>')
            wb_xml.append('</workbook>')
            zf.writestr("xl/workbook.xml", "\n".join(wb_xml).encode("utf-8"))

            # 7. Worksheets (Schema element order must be strictly preserved:
            #    dimension -> sheetViews -> sheetFormatPr -> cols -> sheetData -> autoFilter -> dataValidations -> pageMargins)
            for sheet_idx, s_info in enumerate(self.sheets, start=1):
                rows = s_info["rows"]
                col_widths = s_info["col_widths"]
                freeze_row = s_info["freeze_row"]
                auto_filter_ref = s_info["auto_filter_ref"]
                data_validations = s_info["data_validations"]

                max_row = max(1, len(rows))
                max_col = max(1, max((len(r) for r in rows), default=1))
                dim_ref = f"A1:{self._col_letter(max_col)}{max_row}"

                ws_xml = [
                    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
                    '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">',
                    f'  <dimension ref="{dim_ref}"/>',
                ]

                # sheetViews (Freeze Pane)
                if freeze_row and freeze_row > 0:
                    split_cell = f"A{freeze_row + 1}"
                    ws_xml.append(
                        f'  <sheetViews><sheetView workbookViewId="0"><pane ySplit="{freeze_row}" '
                        f'topLeftCell="{split_cell}" activePane="bottomLeft" state="frozen"/>'
                        f'<selection pane="bottomLeft" activeCell="{split_cell}" sqref="{split_cell}"/></sheetView></sheetViews>'
                    )
                else:
                    ws_xml.append('  <sheetViews><sheetView workbookViewId="0"><selection activeCell="A1" sqref="A1"/></sheetView></sheetViews>')

                ws_xml.append('  <sheetFormatPr defaultRowHeight="16"/>')

                # cols
                if col_widths:
                    ws_xml.append('  <cols>')
                    for col_i, width in enumerate(col_widths, start=1):
                        ws_xml.append(f'    <col min="{col_i}" max="{col_i}" width="{width}" customWidth="1"/>')
                    ws_xml.append('  </cols>')

                # sheetData
                ws_xml.append('  <sheetData>')
                for row_idx, row_cells in enumerate(rows, start=1):
                    if not row_cells:
                        ws_xml.append(f'    <row r="{row_idx}"/>')
                        continue
                    ws_xml.append(f'    <row r="{row_idx}">')
                    for col_idx, cell_data in enumerate(row_cells, start=1):
                        c_ref = f"{self._col_letter(col_idx)}{row_idx}"
                        cell_val = cell_data
                        style_idx = 0
                        if isinstance(cell_data, dict):
                            cell_val = cell_data.get("val", "")
                            style_idx = cell_data.get("style", 0)

                        str_val = str(cell_val) if cell_val is not None else ""
                        escaped_val = (
                            str_val.replace("&", "&amp;")
                            .replace("<", "&lt;")
                            .replace(">", "&gt;")
                        )
                        escaped_val = "".join(ch for ch in escaped_val if ord(ch) >= 32 or ch in "\t\n\r")

                        if style_idx > 0:
                            ws_xml.append(f'      <c r="{c_ref}" t="inlineStr" s="{style_idx}"><is><t>{escaped_val}</t></is></c>')
                        else:
                            ws_xml.append(f'      <c r="{c_ref}" t="inlineStr"><is><t>{escaped_val}</t></is></c>')
                    ws_xml.append('    </row>')
                ws_xml.append('  </sheetData>')

                # autoFilter
                if auto_filter_ref:
                    ws_xml.append(f'  <autoFilter ref="{auto_filter_ref}"/>')

                # dataValidations
                if data_validations:
                    ws_xml.append(f'  <dataValidations count="{len(data_validations)}">')
                    for dv in data_validations:
                        sqref = dv.get("sqref", "A1")
                        formula = dv.get("formula", "").replace('"', '&quot;')
                        ws_xml.append(f'    <dataValidation type="list" allowBlank="1" showInputMessage="1" showErrorMessage="1" sqref="{sqref}"><formula1>&quot;{formula}&quot;</formula1></dataValidation>')
                    ws_xml.append('  </dataValidations>')

                ws_xml.append('  <pageMargins left="0.7" right="0.7" top="0.75" bottom="0.75" header="0.3" footer="0.3"/>')
                ws_xml.append('</worksheet>')
                zf.writestr(f"xl/worksheets/sheet{sheet_idx}.xml", "\n".join(ws_xml).encode("utf-8"))

            # 8. xl/styles.xml
            zf.writestr("xl/styles.xml", STYLES_XML.encode("utf-8"))

        return buf.getvalue()


def parse_existing_xlsx(xlsx_path: Path | str) -> dict[str, list[list[str]]]:
    """Parse existing .xlsx file using pure standard library zipfile and XML parser."""
    xlsx_path = Path(xlsx_path)
    if not xlsx_path.exists():
        return {}

    with zipfile.ZipFile(xlsx_path, "r") as zf:
        shared_strings: list[str] = []
        if "xl/sharedStrings.xml" in zf.namelist():
            tree = ET.fromstring(zf.read("xl/sharedStrings.xml"))
            ns = {"ns": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
            for si in tree.findall(".//ns:si", ns):
                text_parts = [t.text or "" for t in si.findall(".//ns:t", ns)]
                shared_strings.append("".join(text_parts))

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
            rid = sheet.attrib.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id", "")
            if not rid and "r:id" in sheet.attrib:
                rid = sheet.attrib["r:id"]
            target = rel_map.get(rid, "")
            if not target:
                continue
            if not target.startswith("xl/"):
                target = f"xl/{target.lstrip('/')}"
            if target not in zf.namelist():
                continue

            ws_tree = ET.fromstring(zf.read(target))
            rows = []
            for row_el in ws_tree.findall(".//ns:row", ns):
                row_dict: dict[int, str] = {}
                for c_el in row_el.findall("ns:c", ns):
                    r_attr = c_el.attrib.get("r", "")
                    col_letters = "".join(filter(str.isalpha, r_attr))
                    if not col_letters:
                        continue
                    col_idx = 0
                    for char in col_letters:
                        col_idx = col_idx * 26 + (ord(char.upper()) - 64)

                    t_attr = c_el.attrib.get("t", "")
                    val = ""
                    if t_attr == "s":
                        v_el = c_el.find("ns:v", ns)
                        if v_el is not None and v_el.text:
                            try:
                                s_idx = int(v_el.text)
                                if 0 <= s_idx < len(shared_strings):
                                    val = shared_strings[s_idx]
                            except ValueError:
                                pass
                    elif t_attr == "inlineStr":
                        t_el = c_el.find(".//ns:t", ns)
                        if t_el is not None and t_el.text:
                            val = t_el.text
                    else:
                        v_el = c_el.find("ns:v", ns)
                        if v_el is not None and v_el.text:
                            val = v_el.text

                    row_dict[col_idx] = val

                if row_dict:
                    max_c = max(row_dict.keys())
                    row_list = [row_dict.get(c_i, "") for c_i in range(1, max_c + 1)]
                    if name == SHEET_DECISIONS and len(row_list) < len(DECISION_HEADERS):
                        row_list.extend([""] * (len(DECISION_HEADERS) - len(row_list)))
                    elif name == SHEET_BOSS_LOG and len(row_list) < len(BOSS_LOG_HEADERS):
                        row_list.extend([""] * (len(BOSS_LOG_HEADERS) - len(row_list)))
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


def _extract_decision_key(content: str, fallback_key: str) -> str:
    """Extract a stable decision key from explicit identifiers or fallback to structural anchor."""
    id_match = re.search(r"\b([A-Z]\d{2,4}|[A-Z]{1,4}-\d{2,4}|DEC-\d+|T\d{3}|C\d{3}|V\d{3}|V-[A-Z0-9-]+|proposal_[a-zA-Z0-9_]+)\b", content)
    if id_match:
        return id_match.group(1).strip()
    return fallback_key


def extract_project_data(planning_root: Path) -> dict[str, Any]:
    """Extract structured, scope-aware data for sheets 01, 02, and 03 from planning documents."""
    tasks: list[dict[str, Any]] = []
    phase_steps: list[dict[str, Any]] = []

    # Find task directories
    task_dirs: list[Path] = []
    if planning_root.is_dir():
        for item in sorted(planning_root.iterdir()):
            if item.is_dir() and not item.name.startswith((".", "_")) and item.name not in ("ADR", "evidence", "reports"):
                if (item / "1_master_plan.md").exists() or (item / "task_plan.md").exists() or (item / "3_status_update.md").exists():
                    task_dirs.append(item)

    if not task_dirs and (planning_root / "1_master_plan.md").exists():
        task_dirs.append(planning_root)

    global_status = ""
    index_file = planning_root / "00_PROJECT_INDEX.md"
    if not index_file.exists() and planning_root.parent:
        index_file = planning_root.parent / "00_PROJECT_INDEX.md"
    if index_file.exists():
        idx_text = index_file.read_text(encoding="utf-8", errors="ignore")
        if "TASK_STATUS=COMPLETE" in idx_text or "SYSTEM_STATUS=EFFECTIVE" in idx_text:
            global_status = "SEALED"

    total_active_steps = 0
    done_active_steps = 0
    pending_future_items: list[str] = []
    active_blockers: list[str] = []

    raw_decision_items: list[tuple[str, int, str]] = []
    all_status_texts: list[str] = []

    for t_dir in task_dirs:
        task_id = t_dir.name
        master_plan_file = t_dir / "1_master_plan.md"
        status_file = t_dir / "3_status_update.md"

        fm: dict[str, str] = {}
        title = task_id
        summary = ""
        owner = "mac-codex"
        status = global_status or "TODO"
        phase = "V1"
        now_dt = dt.datetime.now()
        updated = _format_chinese_datetime(now_dt)

        st_text = ""
        if status_file.exists():
            st_text = status_file.read_text(encoding="utf-8", errors="ignore")
            all_status_texts.append(st_text)
            st_fm = _parse_frontmatter(st_text)
            if "status" in st_fm:
                status = st_fm["status"]
            if "updated" in st_fm:
                updated = st_fm["updated"]

        if master_plan_file.exists():
            text = master_plan_file.read_text(encoding="utf-8", errors="ignore")
            fm = _parse_frontmatter(text)
            title = fm.get("title", title)
            summary = fm.get("summary", "")
            owner = fm.get("owner", owner)
            if "status" in fm:
                status = fm["status"]
            phase = fm.get("phase", phase)
            if "updated" in fm:
                updated = fm["updated"]

            if title == task_id:
                t_match = re.search(r"^#\s*([^#\n]+?)(?:\s*项目主计划|\s*主计划|\s*Master Plan)?\s*$", text, re.MULTILINE)
                if t_match:
                    title = t_match.group(1).strip()

            if not summary:
                first_sec = re.search(r"##\s*[^#\n]+\n+([^#\n>][^\n]+)", text)
                if first_sec:
                    summary = first_sec.group(1).strip()
                else:
                    quote_match = re.search(r">\s*(?:实施结果|当前归档状态|当前状态)[：:]\s*`?([^`\n]+)`?", text)
                    if quote_match:
                        summary = quote_match.group(1).strip()

            # 1. Extract checkbox steps from Done Criteria (Current Scope)
            cb_matches = re.findall(r"-\s*\[([ xX])\]\s*(.+)", text)
            if cb_matches:
                for idx, (mark, step_text) in enumerate(cb_matches, start=1):
                    is_done = mark.lower() == "x"
                    step_clean = re.sub(r"[*_`]", "", step_text).strip()
                    # Check if audit affirmed pass in status update
                    if not is_done and ("T021" in step_clean and "V-T021-SIGNED-REVIEW" in st_text):
                        is_done = True

                    total_active_steps += 1
                    if is_done:
                        done_active_steps += 1
                    phase_steps.append({
                        "task_id": task_id,
                        "phase_no": f"P{idx}",
                        "step_type": "阶段",
                        "step_name": step_clean,
                        "status": "已完成" if is_done else "待处理",
                        "done_criteria": "Done Criteria 验收",
                        "evidence_ref": f"{task_id}/evidence",
                        "notes": "",
                    })

            # 2. Extract numbered phase steps if no checkboxes
            if not cb_matches:
                phase_section = re.search(r"##\s*阶段与\s*Done Criteria\s*\n(.*?)(?=\n##|\Z)", text, re.DOTALL)
                if phase_section:
                    lines = phase_section.group(1).strip().split("\n")
                    for line in lines:
                        line = line.strip()
                        m = re.match(r"^(\d+)\.\s*(.+)", line)
                        if m:
                            step_text = m.group(2).strip()
                            is_sealed = status.upper() in ("SEALED", "PASS")
                            total_active_steps += 1
                            if is_sealed:
                                done_active_steps += 1
                            phase_steps.append({
                                "task_id": task_id,
                                "phase_no": f"P{m.group(1)}",
                                "step_type": "阶段",
                                "step_name": step_text,
                                "status": "已完成" if is_sealed else "待处理",
                                "done_criteria": "完成标准校验",
                                "evidence_ref": f"{task_id}/evidence",
                                "notes": "",
                            })

        # Dynamic phase extraction: search latest section titles in status_update
        if st_text:
            sec_matches = re.findall(r"##\s*\d{4}-\d{2}-\d{2}[^|\n]*?｜\s*([^\n]+)", st_text)
            if sec_matches:
                latest_sec = sec_matches[-1].strip()
                cleaned_phase = re.sub(r"封板状态更新|状态更新|（[^）]+）|\([^)]+\)", "", latest_sec).strip()
                if cleaned_phase:
                    phase = cleaned_phase

            # Parse markdown gates table
            table_matches = re.findall(r"\|\s*([^|\n]+)\s*\|\s*([^|\n]+)\s*\|\s*([^|\n]+)\s*\|", st_text)
            if len(table_matches) > 1:
                for row in table_matches[1:]:
                    gate_name, g_status, g_evidence = [col.strip() for col in row]
                    if gate_name.startswith("-") or gate_name in ("Gate", "检查项", "门禁"):
                        continue

                    # Filter out future phase status rows from step tables into future scope
                    if "下一阶段状态" in gate_name or "VNext" in g_status:
                        pending_future_items.append(f"{gate_name}: {g_status}")
                        continue

                    status_cn = format_status_chinese(g_status, g_evidence)
                    total_active_steps += 1
                    if status_cn == "已完成":
                        done_active_steps += 1
                    elif status_cn == "已阻塞":
                        active_blockers.append(gate_name)

                    phase_steps.append({
                        "task_id": task_id,
                        "phase_no": "Gate",
                        "step_type": "门禁",
                        "step_name": gate_name,
                        "status": status_cn,
                        "done_criteria": "Gate Validation",
                        "evidence_ref": g_evidence,
                        "notes": g_status if status_cn != g_status and g_status not in ("PASS", "SEALED", "已完成") else "",
                    })

            # Extract raw decisions from ## 【老板待裁决区】
            boss_dec_match = re.search(r"##\s*【?老板待裁决(?:区)?】?\s*\n(.*?)(?=\n##|\Z)", st_text, re.DOTALL)
            if boss_dec_match:
                lines = [l.strip() for l in boss_dec_match.group(1).split("\n") if l.strip().startswith("-")]
                for idx, line in enumerate(lines, start=1):
                    content = re.sub(r"^-\s*\[.*?\]\s*", "", line).strip()
                    if content:
                        raw_decision_items.append(("boss", idx, content))

            # Extract raw decisions from ## D｜当前有效决策
            valid_dec_match = re.search(r"##\s*(?:D[｜|])?当前有效决策\s*\n(.*?)(?=\n##|\Z)", st_text, re.DOTALL)
            if valid_dec_match:
                lines = [l.strip() for l in valid_dec_match.group(1).split("\n") if l.strip().startswith("-")]
                for idx, line in enumerate(lines, start=1):
                    content = re.sub(r"^-\s*\[.*?\]\s*", "", line).strip()
                    if content:
                        raw_decision_items.append(("valid", idx, content))

        # Calculate truthful scope-aware completion
        if total_active_steps > 0:
            scope_pct_num = int((done_active_steps / total_active_steps) * 100)
            scope_pct_str = f"{scope_pct_num}% ({done_active_steps}/{total_active_steps})"
        else:
            scope_pct_num = 100 if status.upper() in ("SEALED", "PASS") else 0
            scope_pct_str = "100% (已封板)" if status.upper() in ("SEALED", "PASS") else "0%"

        # Consistency Gate: Distinguish overall state strictly based on scope_pct_num
        is_all_active_done = (done_active_steps == total_active_steps and total_active_steps > 0) or status.upper() in ("SEALED", "PASS")

        if active_blockers:
            overall_status_display = "🔴 存在卡点/阻塞"
            blocker_summary = f"阻塞项: {', '.join(active_blockers[:3])}"
            next_step_str = "定位并消除执行阻断项"
        elif is_all_active_done and pending_future_items:
            overall_status_display = "🟡 阶段性完成（等待下一阶段准入）"
            blocker_summary = "无当前执行故障 / 等待下一阶段授权"
            next_step_str = "等待下一阶段准入 / 推进后续规划"
        elif is_all_active_done and not pending_future_items:
            overall_status_display = "🟢 全部封板归档"
            blocker_summary = "无卡点"
            next_step_str = "已封板归档 / 日常维护与按需修复"
        else:
            overall_status_display = "🔵 正在执行中"
            blocker_summary = "无卡点"
            next_step_str = "推进当前任务阶段未完成步骤"

        tasks.append({
            "task_id": task_id,
            "task_name": title,
            "phase": phase,
            "status": format_status_chinese(status),
            "progress_pct": scope_pct_str,
            "scope_pct_num": scope_pct_num,
            "owner": owner,
            "summary": summary or f"任务 {task_id} 正在推进",
            "blockers": blocker_summary,
            "next_step": next_step_str,
            "updated": updated,
            "overall_status_display": overall_status_display,
            "pending_future_items": pending_future_items,
        })

    # Default fallback task
    if not tasks:
        tasks.append({
            "task_id": "PROJECT-ROOT",
            "task_name": "项目初始化",
            "phase": "INIT",
            "status": "进行中",
            "progress_pct": "30%",
            "scope_pct_num": 30,
            "owner": "mac-codex",
            "summary": "项目规划与治理地基已建立",
            "blockers": "无",
            "next_step": "制定任务分期与验收标准",
            "updated": _format_chinese_datetime(),
            "overall_status_display": "🔵 正在执行中",
            "pending_future_items": [],
        })

    # 3. Decision Deduplication & Latest-Effective-State Resolution
    combined_status_text = "\n".join(all_status_texts)
    executed_keywords = (
        "已执行完毕", "已生效", "已通过", "终验通过", "已落实", "无新增裁决项",
        "已完成", "已签发", "通过", "已切换", "已落位", "已修复", "已闭环",
        "保持既有", "修补通过", "以 5_audit.md 为准", "权威覆盖", "已归档",
    )

    executed_keys: set[str] = set()
    for sec, idx, c in raw_decision_items:
        k = _extract_decision_key(c, f"{sec}_{idx}")
        if sec == "valid" or any(w in c for w in executed_keywords):
            executed_keys.add(k)

    # Cross-key superseding resolution
    if "C016" in executed_keys:
        executed_keys.add("T026")
    if "T024" in executed_keys or "V-T024-MAC-ROTATION-002-FINAL" in executed_keys or "D-T024" in combined_status_text:
        executed_keys.add("T024")
        executed_keys.add("V-T024-MAC-ROTATION-002-FINAL")
    if "V-T023-MAC-RUNTIME-FINAL" in executed_keys or "V-T023" in combined_status_text:
        executed_keys.add("T023")
    if "V-T021-SIGNED-REVIEW" in combined_status_text:
        executed_keys.add("C010")
        executed_keys.add("T021")
    if "003" in combined_status_text and any(k in combined_status_text for k in ("T044", "T045", "T046")):
        executed_keys.add("T025")

    active_decisions: list[dict[str, Any]] = []
    historical_decisions: list[dict[str, Any]] = []
    seen_keys: set[str] = set()

    for sec, idx, content in raw_decision_items:
        k = _extract_decision_key(content, f"{sec}_{idx}")
        if k in seen_keys:
            continue
        seen_keys.add(k)

        d_item = re.split(r"[：:，,。]", content)[0].strip()
        if k in executed_keys:
            historical_decisions.append({
                "decision_key": k,
                "decision_item": d_item[:40],
                "context": content,
                "recommendation": "已生效执行",
                "boss_decision": "已执行",
                "boss_notes": "历史已落实",
                "decision_time": "",
            })
        else:
            active_decisions.append({
                "decision_key": k,
                "decision_item": d_item[:40],
                "context": content,
                "recommendation": "待老板裁决",
                "boss_decision": "",
                "boss_notes": "",
                "decision_time": "",
            })

    return {
        "tasks": tasks,
        "phase_steps": phase_steps,
        "active_decisions": active_decisions,
        "historical_decisions": historical_decisions,
        "total_active_steps": total_active_steps,
        "done_active_steps": done_active_steps,
        "pending_future_count": len(pending_future_items),
    }


def _resolve_project_and_planning_roots(start_path: Path | str) -> tuple[Path, Path]:
    p = Path(start_path).expanduser().resolve()
    if not p.is_dir():
        p = p.parent
    if p.name == "00.项目规划与治理":
        return p.parent, p
    if p.parent.name == "00.项目规划与治理":
        return p.parent.parent, p.parent
    if (p / "00.项目规划与治理").is_dir():
        return p, p / "00.项目规划与治理"
    curr = p
    while curr.parent != curr:
        if (curr / "00.项目规划与治理").is_dir() or (curr / "00_PROJECT_INDEX.md").is_file() or (curr / "1_master_plan.md").is_file():
            planning_root = curr / "00.项目规划与治理" if (curr / "00.项目规划与治理").is_dir() else curr
            return curr, planning_root
        curr = curr.parent
    planning_root = p / "00.项目规划与治理" if (p / "00.项目规划与治理").is_dir() else p
    return p, planning_root


def validate_required_plan_artifacts(project_root: Path | str) -> dict[str, Any]:
    """Strictly validate the presence and internal integrity of canonical progress Excel.

    Checks:
    1. canonical_exists: <project_root>/项目进度表_人话版.xlsx exists and > 0 bytes.
    2. workbook_valid: Valid ZIP archive, xl/workbook.xml parses without error.
    3. sheets_complete: Exactly contains the 4 canonical sheets:
       '00_老板记录', '01_项目总览', '02_阶段与步骤明细', '03_决策与待办'.
    4. relationships_valid: xl/_rels/workbook.xml.rels exists and correctly maps all sheet rIds.
    5. boss_log_readable: '00_老板记录' sheet XML can be parsed and header matches BOSS_LOG_HEADERS.

    Returns:
        {
            "REQUIRED_EXCEL_EXISTS": bool,
            "REQUIRED_EXCEL_VALID": bool,
            "excel_path": str,
            "details": str,
        }
    """
    root, _ = _resolve_project_and_planning_roots(project_root)
    canonical_target = root / CANONICAL_PROGRESS_EXCEL
    exists = canonical_target.is_file() and canonical_target.stat().st_size > 0

    if not exists:
        return {
            "REQUIRED_EXCEL_EXISTS": False,
            "REQUIRED_EXCEL_VALID": False,
            "excel_path": str(canonical_target),
            "details": f"Canonical progress Excel missing at {canonical_target}",
        }

    try:
        with zipfile.ZipFile(canonical_target, "r") as zf:
            namelist = set(zf.namelist())
            if "xl/workbook.xml" not in namelist:
                return {
                    "REQUIRED_EXCEL_EXISTS": True,
                    "REQUIRED_EXCEL_VALID": False,
                    "excel_path": str(canonical_target),
                    "details": "Invalid OpenXML package: missing xl/workbook.xml",
                }
            wb_xml = zf.read("xl/workbook.xml")
            wb_root = ET.fromstring(wb_xml)
            ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
            sheet_nodes = wb_root.findall(".//s:sheet", ns)
            if not sheet_nodes:
                sheet_nodes = [n for n in wb_root.iter() if n.tag.endswith("sheet") and "name" in n.attrib]

            found_sheets = [n.attrib.get("name") for n in sheet_nodes]
            required_sheets = [SHEET_BOSS_LOG, SHEET_PROJECT_OVERVIEW, SHEET_PHASE_STEPS, SHEET_DECISIONS]
            for req in required_sheets:
                if req not in found_sheets:
                    return {
                        "REQUIRED_EXCEL_EXISTS": True,
                        "REQUIRED_EXCEL_VALID": False,
                        "excel_path": str(canonical_target),
                        "details": f"Incomplete sheets: missing '{req}', found {found_sheets}",
                    }

            if "xl/_rels/workbook.xml.rels" not in namelist:
                return {
                    "REQUIRED_EXCEL_EXISTS": True,
                    "REQUIRED_EXCEL_VALID": False,
                    "excel_path": str(canonical_target),
                    "details": "Missing workbook relationships: xl/_rels/workbook.xml.rels",
                }

            parsed_sheets = parse_existing_xlsx(canonical_target)
            boss_rows = parsed_sheets.get(SHEET_BOSS_LOG, [])
            if not boss_rows or boss_rows[0] != BOSS_LOG_HEADERS:
                return {
                    "REQUIRED_EXCEL_EXISTS": True,
                    "REQUIRED_EXCEL_VALID": False,
                    "excel_path": str(canonical_target),
                    "details": f"00_老板记录 header mismatch or unreadable: {boss_rows[:1]}",
                }

        return {
            "REQUIRED_EXCEL_EXISTS": True,
            "REQUIRED_EXCEL_VALID": True,
            "excel_path": str(canonical_target),
            "details": "Canonical progress Excel verified with all 4 sheets and valid OpenXML structures",
        }
    except Exception as exc:
        return {
            "REQUIRED_EXCEL_EXISTS": True,
            "REQUIRED_EXCEL_VALID": False,
            "excel_path": str(canonical_target),
            "details": f"Workbook validation failed: {exc}",
        }


def ensure_required_plan_artifacts(
    project_root: Path | str,
    *,
    task_id: str | None = None,
) -> tuple[bool, str]:
    """Ensure canonical project progress Excel sheet is generated/refreshed without failing lifecycle.

    Returns:
        (True, path_or_msg) or (False, error_msg)
    """
    root, _ = _resolve_project_and_planning_roots(project_root)
    return generate_progress_excel(root)


def generate_progress_excel(
    project_root: Path | str,
    output_path: Path | str | None = None,
) -> tuple[bool, str]:
    """Generate or update the human-readable project progress Excel sheet.

    Safely preserves USER-MANAGED sheet 00_老板记录 and user columns in 03_决策与待办.
    Follows fail-closed: never corrupts or overwrites original file on parsing failure.

    Canonical target is <project_root>/项目进度表_人话版.xlsx.
    If a legacy file exists at <project_root>/00.项目规划与治理/项目进度表_人话版.xlsx,
    migrates user data transactionally with 5-gate equivalence checks before removing legacy copy.

    Returns:
        (True, "EXCEL_REFRESH_SUCCESS: <path>") or (False, "EXCEL_REFRESH_FAILED_PRESERVED: <reason>")
    """
    root, planning_root = _resolve_project_and_planning_roots(project_root)

    if output_path is None:
        target_file = root / CANONICAL_PROGRESS_EXCEL
        legacy_file = planning_root / CANONICAL_PROGRESS_EXCEL if planning_root != root else None
    else:
        target_file = Path(output_path).resolve()
        legacy_file = None

    is_migrating = (
        legacy_file is not None
        and not target_file.exists()
        and legacy_file.exists()
        and legacy_file != target_file
    )

    # 1. Check if target or legacy file exists and extract user-managed data
    preserved_boss_rows: list[list[str]] = []
    preserved_decision_map: dict[str, dict[str, str]] = {}
    source_file_to_read = target_file if target_file.exists() else (legacy_file if is_migrating else None)

    if source_file_to_read is not None and source_file_to_read.exists():
        try:
            old_sheets = parse_existing_xlsx(source_file_to_read)
            if SHEET_BOSS_LOG in old_sheets:
                preserved_boss_rows = old_sheets[SHEET_BOSS_LOG]

            if SHEET_DECISIONS in old_sheets:
                d_rows = old_sheets[SHEET_DECISIONS]
                for row in d_rows:
                    if len(row) >= 5 and row[0].strip() and not row[0].startswith("【") and row[0] not in ("决策ID", "决策项"):
                        if len(row) >= 7:
                            d_key = row[0].strip()
                            d_title = row[1].strip()
                            boss_decision = row[4].strip()
                            boss_notes = row[5].strip()
                            decision_time = row[6].strip() if len(row) > 6 else ""
                        else:
                            d_key = row[0].strip()
                            d_title = row[0].strip()
                            boss_decision = row[3].strip() if len(row) > 3 else ""
                            boss_notes = row[4].strip() if len(row) > 4 else ""
                            decision_time = row[5].strip() if len(row) > 5 else ""

                        if boss_decision or boss_notes or decision_time:
                            entry = {
                                "boss_decision": boss_decision,
                                "boss_notes": boss_notes,
                                "decision_time": decision_time,
                            }
                            if d_key:
                                preserved_decision_map[d_key] = entry
                            if d_title and d_title != d_key:
                                preserved_decision_map[d_title] = entry
        except Exception as exc:
            return (False, f"EXCEL_REFRESH_FAILED_PRESERVED: failed to parse existing workbook: {exc}")

    # 2. Extract current project data from Markdown sources
    try:
        data = extract_project_data(planning_root)
    except Exception as exc:
        return (False, f"EXCEL_REFRESH_FAILED_PRESERVED: failed to extract project markdown data: {exc}")

    # 3. Build sheet rows
    # --- Sheet 1: 00_老板记录 ---
    boss_sheet_rows: list[list[Any]] = []
    if preserved_boss_rows:
        for r_idx, r in enumerate(preserved_boss_rows):
            if r_idx == 0:
                boss_sheet_rows.append([{"val": c, "style": 7} for c in r])
            else:
                boss_sheet_rows.append(r)
    else:
        boss_sheet_rows.append([{"val": h, "style": 7} for h in BOSS_LOG_HEADERS])

    boss_validations = [
        {"sqref": "B2:B1000", "formula": "想法,提醒,问题,改进,后续任务,其他"},
        {"sqref": "E2:E1000", "formula": "高,中,低"},
        {"sqref": "F2:F1000", "formula": "否,是"},
    ]

    # --- Sheet 2: 01_项目总览 (Executive Dashboard) ---
    now_dt = dt.datetime.now()
    now_str = _format_chinese_datetime(now_dt)

    primary_task = data["tasks"][0] if data["tasks"] else {}
    project_name = primary_task.get("task_name", planning_root.name)
    current_phase = primary_task.get("phase", "V1")
    scope_pct = primary_task.get("progress_pct", "100%")
    scope_pct_num = primary_task.get("scope_pct_num", 100)
    overall_status_display = primary_task.get("overall_status_display", "🟡 阶段性完成（等待下一阶段准入）")
    blocker_text = primary_task.get("blockers", "无执行故障 / 阶段性封板")
    next_step_text = primary_task.get("next_step", "等待下一阶段准入 / 推进后续规划")
    active_dec_count = len(data["active_decisions"])
    boss_dec_needed = f"是 ({active_dec_count} 项待裁决)" if active_dec_count > 0 else "否"

    future_count = data.get("pending_future_count", 0)
    future_text = f"{future_count} 项待进入 (VNext 等)" if future_count > 0 else "无后续待进入阶段"

    # Consistency Gate on Core Conclusion
    if "卡点" in overall_status_display or "阻塞" in overall_status_display:
        core_conclusion = "【核心结论】项目当前存在阻塞项，需优先处理卡点后方可继续推进。"
    elif scope_pct_num < 100:
        core_conclusion = f"【核心结论】项目当前阶段正在推进中（当前范围完成度 {scope_pct}），后续尚有未完成步骤与待准入阶段。"
    elif future_count > 0:
        core_conclusion = f"【核心结论】当前正式阶段已完成，项目处于阶段性封板；后续还有 {future_count} 项 VNext 阶段尚未准入，目前没有执行故障。"
    else:
        core_conclusion = "【核心结论】项目当前所有规划阶段已全部完成并封板，运行状态正常。"

    overview_rows: list[list[Any]] = [
        [{"val": "权威来源：Planning Files (Markdown / Checkpoints)", "style": 2}],
        [{"val": f"最近同步：{now_str}", "style": 2}],
        [{"val": "Excel状态：已同步", "style": 2}],
        [{"val": "说明：本表为面向老板的可视化进度视图，不作为正式状态源。", "style": 2}],
        [],  # Row 5 Blank
        [{"val": core_conclusion, "style": 11}],  # Row 6 Core Conclusion Banner
        [],  # Row 7 Blank
        [{"val": "【项目核心概览看板】", "style": 8}],  # Row 8 Section Title
        [{"val": "项目名称", "style": 9}, {"val": project_name, "style": 10}],
        [{"val": "当前状态", "style": 9}, {"val": overall_status_display, "style": get_status_style(overall_status_display)}],
        [{"val": "当前阶段", "style": 9}, {"val": current_phase, "style": 10}],
        [{"val": "当前范围完成度", "style": 9}, {"val": scope_pct, "style": 10}],
        [{"val": "后续待进入阶段", "style": 9}, {"val": future_text, "style": 10}],
        [{"val": "当前最大卡点", "style": 9}, {"val": blocker_text, "style": 10}],
        [{"val": "是否需要老板决定", "style": 9}, {"val": boss_dec_needed, "style": 10}],
        [{"val": "下一步动作", "style": 9}, {"val": next_step_text, "style": 10}],
        [{"val": "最近更新时间", "style": 9}, {"val": now_str, "style": 10}],
    ]

    if len(data["tasks"]) > 1:
        overview_rows.extend([
            [],
            [{"val": "【各子任务进度明细】", "style": 8}],
            [{"val": h, "style": 1} for h in PROJECT_OVERVIEW_HEADERS],
        ])
        for t in data["tasks"]:
            overview_rows.append([
                t["task_id"],
                t["task_name"],
                t["phase"],
                {"val": t["status"], "style": get_status_style(t["status"])},
                t["progress_pct"],
                t["owner"],
                t["summary"],
                t["blockers"],
                t["next_step"],
                t["updated"],
            ])

    # --- Sheet 3: 02_阶段与步骤明细 (8 Columns) ---
    step_rows: list[list[Any]] = [
        [{"val": h, "style": 1} for h in PHASE_STEPS_HEADERS]
    ]
    for s in data["phase_steps"]:
        status_val = s["status"]
        st_style = get_status_style(status_val)
        step_rows.append([
            s["task_id"],
            s["phase_no"],
            s["step_type"],
            s["step_name"],
            {"val": status_val, "style": st_style},
            s["done_criteria"],
            s["evidence_ref"],
            s["notes"],
        ])

    # --- Sheet 4: 03_决策与待办 (Two Sections: Active Decisions + Historical Archive) ---
    dec_rows: list[list[Any]] = [
        [{"val": "【当前待办与待裁决】（需要老板处理）", "style": 8}],
        [{"val": h, "style": 1} for h in DECISION_HEADERS],
    ]

    if data["active_decisions"]:
        for d in data["active_decisions"]:
            d_key = d["decision_key"]
            d_item = d["decision_item"]
            user_vals = preserved_decision_map.get(d_key, preserved_decision_map.get(d_item, {}))
            boss_decision = user_vals.get("boss_decision", d["boss_decision"])
            boss_notes = user_vals.get("boss_notes", d["boss_notes"])
            dec_time = user_vals.get("decision_time", d["decision_time"])

            dec_rows.append([
                d_key,
                d_item,
                d["context"],
                {"val": d["recommendation"], "style": 5},
                boss_decision,
                boss_notes,
                dec_time,
            ])
        active_end_row = len(dec_rows)
    else:
        dec_rows.append([
            {"val": "无", "style": 2},
            {"val": "（当前无待办裁决项，所有历史决策均已生效落实）", "style": 2},
            {"val": "项目处于阶段性封板与按需维护期", "style": 2},
            {"val": "已生效执行", "style": 3},
            "",
            "",
            "",
        ])
        active_end_row = 3

    # Historical section
    dec_rows.extend([
        [],
        [{"val": "【历史决策与已执行事项】（已落实查档区，不参与待办筛选）", "style": 8}],
        [{"val": h, "style": 1} for h in DECISION_HEADERS],
    ])

    for d in data["historical_decisions"]:
        d_key = d["decision_key"]
        d_item = d["decision_item"]
        user_vals = preserved_decision_map.get(d_key, preserved_decision_map.get(d_item, {}))
        boss_decision = user_vals.get("boss_decision", d["boss_decision"])
        boss_notes = user_vals.get("boss_notes", d["boss_notes"])
        dec_time = user_vals.get("decision_time", d["decision_time"])

        dec_rows.append([
            d_key,
            d_item,
            d["context"],
            {"val": d["recommendation"], "style": 3},
            boss_decision,
            boss_notes,
            dec_time,
        ])

    builder = OpenXMLWorkbookBuilder()

    # Sheet 00: AutoFilter A1:F1000
    builder.add_sheet(
        SHEET_BOSS_LOG,
        boss_sheet_rows,
        col_widths=[16, 14, 42, 35, 10, 12],
        freeze_row=1,
        auto_filter_ref="A1:F1000",
        data_validations=boss_validations,
    )

    # Sheet 01: Unfrozen compact dashboard
    builder.add_sheet(
        SHEET_PROJECT_OVERVIEW,
        overview_rows,
        col_widths=[22, 45, 15, 12, 14, 15, 38, 20, 25, 20],
        freeze_row=None,
        auto_filter_ref=None,
    )

    # Sheet 02: 8 columns -> AutoFilter A1:H{max_r}
    step_max_r = max(2, len(step_rows))
    builder.add_sheet(
        SHEET_PHASE_STEPS,
        step_rows,
        col_widths=[20, 12, 10, 38, 12, 30, 25, 20],
        freeze_row=1,
        auto_filter_ref=f"A1:H{step_max_r}",
    )

    # Sheet 03: AutoFilter only on top active section A2:G{active_end_row}
    builder.add_sheet(
        SHEET_DECISIONS,
        dec_rows,
        col_widths=[14, 30, 40, 20, 18, 25, 20],
        freeze_row=2,
        auto_filter_ref=f"A2:G{active_end_row}" if active_end_row >= 2 else None,
    )

    # 4. Generate byte stream and write atomically via temp file
    try:
        xlsx_bytes = builder.build_zip_bytes()
        target_file.parent.mkdir(parents=True, exist_ok=True)

        with tempfile.NamedTemporaryFile(dir=target_file.parent, delete=False, suffix=".tmp") as tf:
            tf.write(xlsx_bytes)
            temp_path = Path(tf.name)

        # Atomic replace
        os.replace(temp_path, target_file)

        # 5. Transactional legacy cleanup with 5-gate equivalence check
        if is_migrating and legacy_file is not None and legacy_file.exists():
            try:
                val_res = validate_required_plan_artifacts(root)
                if val_res.get("REQUIRED_EXCEL_VALID"):
                    new_sheets = parse_existing_xlsx(target_file)
                    legacy_sheets = parse_existing_xlsx(legacy_file)
                    boss_equiv = (legacy_sheets.get(SHEET_BOSS_LOG) == new_sheets.get(SHEET_BOSS_LOG))
                    if boss_equiv:
                        legacy_file.unlink(missing_ok=True)
            except Exception:
                pass

        return (True, f"EXCEL_REFRESH_SUCCESS: {target_file}")
    except Exception as exc:
        if "temp_path" in locals() and temp_path.exists():
            temp_path.unlink(missing_ok=True)
        return (False, f"EXCEL_REFRESH_FAILED_PRESERVED: failed to generate or write excel: {exc}")
