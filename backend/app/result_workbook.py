from __future__ import annotations

from datetime import UTC, datetime
from io import BytesIO
import re
from typing import Any
from xml.sax.saxutils import escape
import zipfile

from .analysis import excel_col_name, write_cell_xml
from .models import CalculatorWorkbookRequest, ShiftRule, VirtualPerson

SECTOR_NAMES = ("ALL", "LOWER", "UPPER", "MID", "HIGH", "TOP")
DAY_START = 7

STYLE_NORMAL = 0
STYLE_TITLE = 1
STYLE_SECTION = 2
STYLE_LABEL = 3
STYLE_VALUE = 4
STYLE_SUCCESS = 5
STYLE_WARNING = 6
STYLE_DANGER = 7
STYLE_HEADER = 8
STYLE_FL = 9
STYLE_APS = 10
STYLE_ACS = 11
STYLE_CLOSED = 12
STYLE_BREAK = 13
STYLE_ACCENT = 14
STYLE_MUTED = 15
PERSON_STYLE_START = 16

WORKER_COLORS = (
    "FFFF9F91",
    "FFB7D8FF",
    "FFB9E8C6",
    "FFFFE08A",
    "FFCEB9FF",
    "FF55D6C5",
    "FFFFB47D",
    "FFD6E98D",
    "FFF3AFD7",
    "FF9FC7FF",
    "FFC7C0A6",
    "FFA6E4A0",
    "FFFFCBC5",
    "FFC9A8FF",
    "FFE1BD7F",
    "FFC6B4D9",
    "FFB4E2C9",
    "FFF0C08F",
    "FFB8CFEB",
    "FFE8B5BF",
    "FFC9DF9C",
    "FFB2D8C8",
    "FFF5D17D",
    "FFBCBCEC",
    "FFFFBD9C",
    "FFA9DED4",
    "FFE8B0EE",
    "FFB4D490",
    "FFF0B1A0",
    "FF9ED7F0",
    "FFD9C493",
    "FFB8C8A4",
    "FFDDAED0",
    "FFA7D6AB",
    "FFC8B597",
    "FFA6BFE3",
)


def cell(
    value: Any = None,
    style: int = STYLE_NORMAL,
    formula: str | None = None,
) -> dict[str, Any]:
    return {"value": value, "style": style, "formula": formula}


def styled_row(values: list[Any], style: int) -> list[dict[str, Any]]:
    return [cell(value, style) for value in values]


def write_result_cell_xml(ref: str, value: Any) -> str:
    if isinstance(value, dict):
        raw_value = value.get("value")
        style = int(value.get("style") or 0)
        formula = value.get("formula")
        if raw_value in {None, ""} and style and not formula:
            return f'<c r="{ref}" s="{style}"/>'
    return write_cell_xml(ref, value)


def display_person_id(person: VirtualPerson) -> str:
    return {
        "V1": "Vi1",
        "V2": "Vi2",
        "V3": "Vi3",
    }.get(person.role or "", person.id)


def person_shift_label(person: VirtualPerson) -> str:
    return f"{person.role}/{person.shift}" if person.role else person.shift


def person_source_label(source: str) -> str:
    return "office" if source in {"officer", "office-pool"} else source


def license_style(license_name: str) -> int:
    return {
        "FL": STYLE_FL,
        "APS": STYLE_APS,
        "ACS": STYLE_ACS,
    }.get(license_name, STYLE_VALUE)


def okzp_style_ids(person_count: int) -> dict[str, int]:
    first = PERSON_STYLE_START + person_count
    names = (
        "title",
        "group",
        "subheader",
        "roster",
        "time",
        "count",
        "empty",
        "summary_label",
        "summary_value",
        "note",
    )
    return {name: first + index for index, name in enumerate(names)}


def result_status(request: CalculatorWorkbookRequest) -> tuple[str, int]:
    result = request.result
    if not result.feasible:
        return "NEIZVEDLJIVO", STYLE_DANGER
    if result.missing_sector_hours > 0:
        return "DELNO POKRITO", STYLE_WARNING
    return "POKRITO", STYLE_SUCCESS


def workbook_styles_xml(person_count: int) -> str:
    base_fills = (
        "FF17365D",
        "FF2F88BD",
        "FFEAF4FB",
        "FFFFFFFF",
        "FFDDF3E8",
        "FFFFF3BF",
        "FFFDE2E2",
        "FFFFB8B0",
        "FFB9E8C6",
        "FFB7D8FF",
        "FFEEF2F6",
        "FFFFF0B3",
        "FF55D6C5",
        "FFF8FAFC",
    )
    person_fills = tuple(
        WORKER_COLORS[index % len(WORKER_COLORS)]
        for index in range(person_count)
    )
    okzp_fills = (
        "FF00D974",
        "FFFFFFFF",
        "FFF1F3F5",
        "FFFFFFFF",
        "FFA6D88B",
        "FFFFFF66",
        "FFFFF6A5",
    )
    fills = base_fills + person_fills + okzp_fills
    fill_xml = (
        '<fill><patternFill patternType="none"/></fill>'
        '<fill><patternFill patternType="gray125"/></fill>'
        + "".join(
            '<fill><patternFill patternType="solid">'
            f'<fgColor rgb="{color}"/><bgColor indexed="64"/>'
            "</patternFill></fill>"
            for color in fills
        )
    )

    def xf(
        *,
        font: int = 0,
        fill: int = 0,
        border: int = 1,
        horizontal: str = "left",
        wrap: bool = False,
    ) -> str:
        return (
            f'<xf numFmtId="0" fontId="{font}" fillId="{fill}" borderId="{border}" xfId="0" '
            'applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1">'
            f'<alignment horizontal="{horizontal}" vertical="center"'
            + (' wrapText="1"' if wrap else "")
            + "/></xf>"
        )

    cell_xfs = [
        '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>',
        xf(font=2, fill=2, horizontal="left"),
        xf(font=1, fill=3, horizontal="left"),
        xf(font=3, fill=4),
        xf(fill=5),
        xf(font=3, fill=6, horizontal="center"),
        xf(font=3, fill=7, horizontal="center"),
        xf(font=3, fill=8, horizontal="center"),
        xf(font=1, fill=2, horizontal="center", wrap=True),
        xf(font=3, fill=9, horizontal="center"),
        xf(font=3, fill=10, horizontal="center"),
        xf(font=3, fill=11, horizontal="center"),
        xf(font=0, fill=12, horizontal="center"),
        xf(font=0, fill=13, wrap=True),
        xf(font=1, fill=14, horizontal="center"),
        xf(font=0, fill=15),
    ]
    cell_xfs.extend(
        xf(font=3, fill=16 + index, horizontal="center", wrap=True)
        for index in range(person_count)
    )
    okzp_fill_start = 16 + person_count
    cell_xfs.extend(
        [
            xf(font=4, fill=okzp_fill_start, border=2, horizontal="center", wrap=True),
            xf(font=5, fill=okzp_fill_start + 1, border=2, horizontal="center", wrap=True),
            xf(font=6, fill=okzp_fill_start + 2, border=1, horizontal="center", wrap=True),
            xf(font=6, fill=okzp_fill_start + 3, border=1, horizontal="center"),
            xf(font=6, fill=okzp_fill_start + 3, border=1, horizontal="center", wrap=True),
            xf(font=6, fill=okzp_fill_start + 3, border=1, horizontal="center"),
            xf(font=0, fill=okzp_fill_start + 3, border=1, horizontal="center"),
            xf(font=7, fill=okzp_fill_start + 5, border=2, horizontal="center", wrap=True),
            xf(font=4, fill=okzp_fill_start + 5, border=2, horizontal="center", wrap=True),
            xf(font=3, fill=okzp_fill_start + 6, border=2, horizontal="left", wrap=True),
        ]
    )

    dxf_colors = ("FFCDECCF", "FFB8E1DE", "FFA9CBE8", "FFFFE08A", "FFFFB47D")
    dxfs = "".join(
        '<dxf><fill><patternFill patternType="solid">'
        f'<fgColor rgb="{color}"/><bgColor indexed="64"/>'
        "</patternFill></fill></dxf>"
        for color in dxf_colors
    )

    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<fonts count="8">'
        '<font><sz val="11"/><color rgb="FF24364B"/><name val="Calibri"/><family val="2"/></font>'
        '<font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Calibri"/><family val="2"/></font>'
        '<font><b/><sz val="16"/><color rgb="FFFFFFFF"/><name val="Calibri"/><family val="2"/></font>'
        '<font><b/><sz val="11"/><color rgb="FF17365D"/><name val="Calibri"/><family val="2"/></font>'
        '<font><b/><sz val="16"/><color rgb="FF111827"/><name val="Calibri"/><family val="2"/></font>'
        '<font><b/><i/><sz val="13"/><color rgb="FF111827"/><name val="Calibri"/><family val="2"/></font>'
        '<font><b/><sz val="10"/><color rgb="FF24364B"/><name val="Calibri"/><family val="2"/></font>'
        '<font><b/><sz val="11"/><color rgb="FF111827"/><name val="Calibri"/><family val="2"/></font>'
        '</fonts>'
        f'<fills count="{2 + len(fills)}">{fill_xml}</fills>'
        '<borders count="3">'
        '<border><left/><right/><top/><bottom/><diagonal/></border>'
        '<border><left style="thin"><color rgb="FFB8C7D6"/></left>'
        '<right style="thin"><color rgb="FFB8C7D6"/></right>'
        '<top style="thin"><color rgb="FFB8C7D6"/></top>'
        '<bottom style="thin"><color rgb="FFB8C7D6"/></bottom><diagonal/></border>'
        '<border><left style="medium"><color rgb="FF222222"/></left>'
        '<right style="medium"><color rgb="FF222222"/></right>'
        '<top style="medium"><color rgb="FF222222"/></top>'
        '<bottom style="medium"><color rgb="FF222222"/></bottom><diagonal/></border>'
        '</borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        f'<cellXfs count="{len(cell_xfs)}">{"".join(cell_xfs)}</cellXfs>'
        '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
        f'<dxfs count="5">{dxfs}</dxfs>'
        '<tableStyles count="0" defaultTableStyle="TableStyleMedium2" defaultPivotStyle="PivotStyleLight16"/>'
        '</styleSheet>'
    )


def worksheet_xml(
    rows: list[list[Any]],
    *,
    widths: dict[int, float] | None = None,
    hidden_columns: set[int] | None = None,
    freeze_row: int = 0,
    freeze_col: int = 0,
    row_heights: dict[int, float] | None = None,
    merges: list[str] | None = None,
    auto_filter: str | None = None,
    conditional_formatting: str = "",
    show_grid_lines: bool = True,
    landscape: bool = False,
) -> str:
    max_row = max(len(rows), 1)
    max_col = max((len(row) for row in rows), default=1)
    dimension = f"A1:{excel_col_name(max_col)}{max_row}"
    columns_xml = ""
    if widths:
        columns_xml = "<cols>" + "".join(
            f'<col min="{index}" max="{index}" width="{width}" customWidth="1"'
            + (' hidden="1"' if index in (hidden_columns or set()) else "")
            + "/>"
            for index, width in sorted(widths.items())
        ) + "</cols>"

    view_attrs = 'workbookViewId="0"'
    if not show_grid_lines:
        view_attrs += ' showGridLines="0"'
    if freeze_row or freeze_col:
        top_left = f"{excel_col_name(freeze_col + 1)}{freeze_row + 1}"
        active_pane = "bottomRight" if freeze_row and freeze_col else "bottomLeft" if freeze_row else "topRight"
        pane_attrs = [
            f'topLeftCell="{top_left}"',
            f'activePane="{active_pane}"',
            'state="frozen"',
        ]
        if freeze_row:
            pane_attrs.append(f'ySplit="{freeze_row}"')
        if freeze_col:
            pane_attrs.append(f'xSplit="{freeze_col}"')
        views_xml = (
            f'<sheetViews><sheetView {view_attrs}>'
            f'<pane {" ".join(pane_attrs)}/>'
            '</sheetView></sheetViews>'
        )
    else:
        views_xml = f'<sheetViews><sheetView {view_attrs}/></sheetViews>'

    rows_xml: list[str] = []
    for row_index, row in enumerate(rows, start=1):
        height = (row_heights or {}).get(row_index)
        height_attrs = f' ht="{height}" customHeight="1"' if height else ""
        cells_xml = "".join(
            write_result_cell_xml(f"{excel_col_name(col_index)}{row_index}", value)
            for col_index, value in enumerate(row, start=1)
            if write_result_cell_xml(f"{excel_col_name(col_index)}{row_index}", value)
        )
        rows_xml.append(f'<row r="{row_index}"{height_attrs}>{cells_xml}</row>')

    merge_xml = ""
    if merges:
        merge_xml = (
            f'<mergeCells count="{len(merges)}">'
            + "".join(f'<mergeCell ref="{ref}"/>' for ref in merges)
            + "</mergeCells>"
        )
    filter_xml = f'<autoFilter ref="{auto_filter}"/>' if auto_filter else ""
    page_xml = ""
    sheet_properties = ""
    if landscape:
        sheet_properties = '<sheetPr><pageSetUpPr fitToPage="1"/></sheetPr>'
        page_xml = (
            '<pageMargins left="0.25" right="0.25" top="0.5" bottom="0.5" header="0.2" footer="0.2"/>'
            '<pageSetup orientation="landscape" fitToWidth="1" fitToHeight="0"/>'
        )

    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f"{sheet_properties}"
        f'<dimension ref="{dimension}"/>'
        f"{views_xml}"
        '<sheetFormatPr defaultRowHeight="18"/>'
        f"{columns_xml}"
        f'<sheetData>{"".join(rows_xml)}</sheetData>'
        f"{filter_xml}"
        f"{merge_xml}"
        f"{conditional_formatting}"
        f"{page_xml}"
        "</worksheet>"
    )


def shift_slots(rule: ShiftRule) -> set[int]:
    start_slot = (rule.start_hour - DAY_START) % 24
    return {(start_slot + offset) % 24 for offset in range(rule.duration_hours)}


def configuration_sheet(
    request: CalculatorWorkbookRequest,
    person_styles: dict[str, int],
) -> tuple[list[list[Any]], dict[int, float], dict[int, float], list[str]]:
    result = request.result
    status_label, status_style = result_status(request)
    people_by_key = {person.id.casefold(): person for person in result.people}
    style_by_key = {key.casefold(): style for key, style in person_styles.items()}
    rules_by_code = {rule.code: rule for rule in request.shifts}
    person_slots = {
        person.id.casefold(): shift_slots(rules_by_code[person.shift])
        for person in result.people
        if person.shift in rules_by_code
    }
    targets = request.target_demand or [hour.open_sectors for hour in result.hourly_coverage]

    rows: list[list[Any]] = [
        [cell(f"ATCConfMaker · {request.name or 'Konfiguracija'}", STYLE_TITLE)],
        [
            cell("Status", STYLE_LABEL),
            cell(status_label, status_style),
            cell("Najdeno SH", STYLE_LABEL),
            cell(result.max_sector_hours, STYLE_ACCENT),
            cell("Cilj SH", STYLE_LABEL),
            cell(result.requested_sector_hours, STYLE_VALUE),
            cell("Ljudje", STYLE_LABEL),
            cell(result.planned_people, STYLE_VALUE),
            cell("Licence", STYLE_LABEL),
            cell(
                "FL "
                + str(sum(person.license == "FL" for person in result.people))
                + " · APS "
                + str(sum(person.license == "APS" for person in result.people))
                + " · ACS "
                + str(sum(person.license == "ACS" for person in result.people)),
                STYLE_VALUE,
            ),
        ],
        [],
        [cell("URNI SEKTORSKI RAZPORED", STYLE_SECTION)],
        [
            cell("URA", STYLE_HEADER),
            cell("ŽELJA", STYLE_HEADER),
            cell("REAL.", STYLE_HEADER),
            cell("ALL", STYLE_HEADER),
            cell(" ", STYLE_HEADER),
            cell("LOWER", STYLE_HEADER),
            cell(" ", STYLE_HEADER),
            cell("UPPER", STYLE_HEADER),
            cell(" ", STYLE_HEADER),
            cell("MID", STYLE_HEADER),
            cell(" ", STYLE_HEADER),
            cell("HIGH", STYLE_HEADER),
            cell(" ", STYLE_HEADER),
            cell("TOP", STYLE_HEADER),
            cell(" ", STYLE_HEADER),
            cell("PAVZA / REZERVA", STYLE_HEADER),
        ],
        [
            cell(" ", STYLE_HEADER),
            cell(" ", STYLE_HEADER),
            cell(" ", STYLE_HEADER),
            *[cell(seat, STYLE_HEADER) for _ in SECTOR_NAMES for seat in (1, 2)],
            cell(" ", STYLE_HEADER),
        ],
    ]
    row_heights = {1: 28.0, 4: 24.0, 5: 24.0, 6: 22.0}

    for slot, hour in enumerate(result.hourly_coverage):
        target = targets[slot] if slot < len(targets) else hour.open_sectors
        coverage_style = STYLE_SUCCESS if hour.open_sectors >= target else STYLE_DANGER
        assignment_by_name = {
            assignment.sector_name.upper(): assignment
            for assignment in hour.sector_workers
            if assignment is not None
        }
        row: list[Any] = [
            cell(hour.hour, STYLE_LABEL),
            cell(target, STYLE_VALUE),
            cell(hour.open_sectors, coverage_style),
        ]
        active_keys: set[str] = set()
        for sector_name in SECTOR_NAMES:
            assignment = assignment_by_name.get(sector_name)
            if assignment is None:
                row.extend([cell("Zaprto", STYLE_CLOSED), cell(" ", STYLE_CLOSED)])
                continue
            for worker_id in (assignment.lower_worker, assignment.upper_worker):
                worker_key = worker_id.casefold()
                active_keys.add(worker_key)
                person = people_by_key.get(worker_key)
                if person is None:
                    row.append(cell(worker_id or "?", STYLE_WARNING))
                    continue
                row.append(
                    cell(
                        f"{display_person_id(person)}\n{person.license} · {person_shift_label(person)}",
                        style_by_key.get(worker_key, STYLE_VALUE),
                    )
                )
        break_people = [
            person
            for person in result.people
            if slot in person_slots.get(person.id.casefold(), set())
            and person.id.casefold() not in active_keys
        ]
        row.append(
            cell(
                ", ".join(
                    f"{display_person_id(person)} ({person.license}/{person.shift})"
                    for person in break_people
                )
                or "—",
                STYLE_BREAK,
            )
        )
        rows.append(row)
        row_heights[len(rows)] = 36.0

    widths = {
        1: 16,
        2: 8,
        3: 8,
        **{index: 12 for index in range(4, 16)},
        16: 34,
    }
    merges = [
        "A1:P1",
        "A4:P4",
        "A5:A6",
        "B5:B6",
        "C5:C6",
        "D5:E5",
        "F5:G5",
        "H5:I5",
        "J5:K5",
        "L5:M5",
        "N5:O5",
        "P5:P6",
    ]
    return rows, widths, row_heights, merges


def okzp_person_id(person: VirtualPerson) -> str:
    if person.role in {"V1", "V2", "V3"}:
        return person.role
    if person.role == "FMP":
        return "fmp"
    return person.id


def okzp_shift_label(person: VirtualPerson) -> str:
    if person.role in {"V1", "V2", "V3"}:
        return f"Vi{person.role[-1]}"
    if person.role == "FMP" and not person.shift.lower().endswith("f"):
        return f"{person.shift}f"
    return person.shift


def okzp_hour_label(value: str, slot: int) -> str:
    times = re.findall(r"\d{1,2}:\d{2}", value)
    if len(times) >= 2:
        start, end = times[:2]
        return f"{int(start[:2])}.{start[3:]} - {int(end[:2])}.{end[3:]}"
    start = (DAY_START + slot) % 24
    end = (start + 1) % 24
    return f"{start}.00 - {end}.00"


def okzp_configuration_sheet(
    request: CalculatorWorkbookRequest,
    person_styles: dict[str, int],
) -> tuple[
    list[list[Any]],
    dict[int, float],
    dict[int, float],
    list[str],
    str,
    str,
]:
    result = request.result
    styles = okzp_style_ids(len(result.people))
    people_by_key = {person.id.casefold(): person for person in result.people}
    style_by_key = {
        person.id.casefold(): person_styles[person.id]
        for person in result.people
    }
    display_by_key = {
        person.id.casefold(): okzp_person_id(person)
        for person in result.people
    }
    license_values = ",".join(f'"{person.license}"' for person in result.people)

    def license_count_formula(license_name: str) -> str:
        if not license_values:
            return "0"
        return f'SUMPRODUCT(--({{{license_values}}}="{license_name}"))'

    rows: list[list[Any]] = [
        [
            cell(request.name or "OKZP konfiguracija", styles["title"]),
            cell(),
            cell(),
            cell("LOC", styles["group"]),
            cell(),
            cell("ALL", styles["group"]),
            cell(),
            cell("LOWER", styles["group"]),
            cell(),
            cell("UPPER", styles["group"]),
            cell(),
            cell("MID", styles["group"]),
            cell(),
            cell("HIGH", styles["group"]),
            cell(),
            cell("TOP", styles["group"]),
            cell(),
        ],
        [
            cell("ID", styles["subheader"]),
            cell("IZM.", styles["subheader"]),
            cell("SH", styles["subheader"]),
            cell("URA", styles["subheader"]),
            cell("#", styles["subheader"]),
            *[
                cell(seat, styles["subheader"])
                for _ in SECTOR_NAMES
                for seat in (1, 2)
            ],
        ],
    ]
    merges = [
        "A1:C1",
        "D1:E1",
        "F1:G1",
        "H1:I1",
        "J1:K1",
        "L1:M1",
        "N1:O1",
        "P1:Q1",
    ]
    row_heights = {1: 52.0, 2: 25.0}
    body_count = max(24, len(result.people))

    for body_index in range(body_count):
        row_number = body_index + 3
        row = [cell() for _ in range(17)]
        if body_index < len(result.people):
            person = result.people[body_index]
            person_style = person_styles[person.id]
            display_id = okzp_person_id(person)
            row[0] = cell(display_id, person_style)
            row[1] = cell(okzp_shift_label(person), styles["roster"])
            row[2] = cell(
                person.sector_hours,
                styles["roster"],
                formula=f'COUNTIF($F$3:$Q$26,A{row_number})',
            )
        if body_index < len(result.hourly_coverage):
            hour = result.hourly_coverage[body_index]
            row[3] = cell(okzp_hour_label(hour.hour, body_index), styles["time"])
            row[4] = cell(
                hour.open_sectors,
                styles["count"],
                formula=f'COUNTA(F{row_number}:Q{row_number})/2',
            )
            assignments = {
                assignment.sector_name.upper(): assignment
                for assignment in hour.sector_workers
                if assignment is not None
            }
            for sector_index, sector_name in enumerate(SECTOR_NAMES):
                assignment = assignments.get(sector_name)
                for seat_index, worker_id in enumerate(
                    (
                        assignment.lower_worker if assignment else "",
                        assignment.upper_worker if assignment else "",
                    )
                ):
                    column_index = 5 + sector_index * 2 + seat_index
                    if not worker_id:
                        row[column_index] = cell(None, styles["empty"])
                        continue
                    worker_key = worker_id.casefold()
                    row[column_index] = cell(
                        display_by_key.get(worker_key, worker_id),
                        style_by_key.get(worker_key, STYLE_WARNING),
                    )
        rows.append(row)
        row_heights[row_number] = 22.0

    footer_label_row = len(rows) + 2
    rows.append([])
    rows.append(
        [
            cell("KONTROLORJI", styles["summary_label"]),
            cell(),
            cell(),
            cell("SEKTORSKE URE", styles["summary_label"]),
            cell(),
            cell("FL", styles["summary_label"]),
            cell(),
            cell("APS", styles["summary_label"]),
            cell(),
            cell("ACS", styles["summary_label"]),
            cell(),
            cell("NEUP.", styles["summary_label"]),
            cell(),
            cell("OPOMBE", styles["summary_label"]),
            cell(),
            cell(),
            cell(),
        ]
    )
    roster_end_row = len(result.people) + 2
    unused_people = sum(person.sector_hours == 0 for person in result.people)
    notes = [*result.notes, *(f"OPOZORILO: {warning}" for warning in result.warnings)]
    rows.append(
        [
            cell(
                len(result.people),
                styles["summary_value"],
                formula=f"COUNTA(A3:A{roster_end_row})",
            ),
            cell(),
            cell(),
            cell(
                result.max_sector_hours,
                styles["summary_value"],
                formula="SUM(E3:E26)",
            ),
            cell(),
            cell(
                sum(person.license == "FL" for person in result.people),
                styles["summary_value"],
                formula=license_count_formula("FL"),
            ),
            cell(),
            cell(
                sum(person.license == "APS" for person in result.people),
                styles["summary_value"],
                formula=license_count_formula("APS"),
            ),
            cell(),
            cell(
                sum(person.license == "ACS" for person in result.people),
                styles["summary_value"],
                formula=license_count_formula("ACS"),
            ),
            cell(),
            cell(
                unused_people,
                styles["summary_value"],
                formula=f'COUNTIF($C$3:$C${roster_end_row},0)',
            ),
            cell(),
            cell(" · ".join(notes) or "Brez opomb.", styles["note"]),
            cell(),
            cell(),
            cell(),
        ]
    )
    value_row = footer_label_row + 1
    merges.extend(
        [
            f"A{footer_label_row}:C{footer_label_row}",
            f"D{footer_label_row}:E{footer_label_row}",
            f"F{footer_label_row}:G{footer_label_row}",
            f"H{footer_label_row}:I{footer_label_row}",
            f"J{footer_label_row}:K{footer_label_row}",
            f"L{footer_label_row}:M{footer_label_row}",
            f"N{footer_label_row}:Q{footer_label_row}",
            f"A{value_row}:C{value_row}",
            f"D{value_row}:E{value_row}",
            f"F{value_row}:G{value_row}",
            f"H{value_row}:I{value_row}",
            f"J{value_row}:K{value_row}",
            f"L{value_row}:M{value_row}",
            f"N{value_row}:Q{value_row}",
        ]
    )
    row_heights[footer_label_row] = 25.0
    row_heights[value_row] = 48.0

    widths = {
        1: 8,
        2: 10,
        3: 7,
        4: 16,
        5: 6,
        **{column: 8.5 for column in range(6, 18)},
    }
    conditional_formatting = (
        '<conditionalFormatting sqref="E3:E26">'
        + "".join(
            f'<cfRule type="cellIs" dxfId="{count - 1}" priority="{count}" '
            f'stopIfTrue="1" operator="equal"><formula>{count}</formula></cfRule>'
            for count in range(1, 6)
        )
        + "</conditionalFormatting>"
    )
    return (
        rows,
        widths,
        row_heights,
        merges,
        conditional_formatting,
        f"A1:Q{value_row}",
    )


def summary_sheet(
    request: CalculatorWorkbookRequest,
) -> tuple[list[list[Any]], dict[int, float], dict[int, float], list[str]]:
    result = request.result
    status_label, status_style = result_status(request)
    rows: list[list[Any]] = [
        [cell(f"ATCConfMaker · {request.name or 'Konfiguracija'}", STYLE_TITLE)],
        [cell("IZID", STYLE_SECTION)],
        [cell("Status", STYLE_LABEL), cell(status_label, status_style)],
        [cell("Najdene sektorske ure", STYLE_LABEL), cell(result.max_sector_hours, STYLE_ACCENT)],
        [cell("Željene sektorske ure", STYLE_LABEL), cell(result.requested_sector_hours, STYLE_VALUE)],
        [cell("Manjkajoče sektorske ure", STYLE_LABEL), cell(result.missing_sector_hours, STYLE_DANGER if result.missing_sector_hours else STYLE_SUCCESS)],
        [cell("Splaniranih ljudi", STYLE_LABEL), cell(result.planned_people, STYLE_VALUE)],
        [cell("Aktivnih ljudi", STYLE_LABEL), cell(result.active_people, STYLE_VALUE)],
        [cell("Neuporabljenih ljudi", STYLE_LABEL), cell(result.unused_people, STYLE_VALUE)],
        [cell("Kontrolorske ure", STYLE_LABEL), cell(f"{result.scheduled_person_hours}/{result.total_person_capacity_hours}", STYLE_VALUE)],
        [cell("Izkoriščenost", STYLE_LABEL), cell(f"{result.utilization_percent} %", STYLE_SUCCESS if result.utilization_percent >= 90 else STYLE_WARNING)],
        [cell("Minimalno potrebnih FL", STYLE_LABEL), cell(result.minimum_required_fl, STYLE_FL)],
        [cell("Grobo izhodišče ljudi", STYLE_LABEL), cell(result.baseline_min_people, STYLE_VALUE)],
        [cell("Formula izhodišča", STYLE_LABEL), cell(result.baseline_min_people_formula or "—", STYLE_VALUE)],
        [],
        [cell("SOLVER IN IZJEME", STYLE_SECTION)],
        [cell("Status solverja", STYLE_LABEL), cell(result.solver_status or "—", STYLE_VALUE)],
        [cell("SH zgornja meja", STYLE_LABEL), cell(result.solver_upper_bound_sector_hours, STYLE_VALUE)],
        [cell("Razlika do meje", STYLE_LABEL), cell(result.solver_gap_to_upper_bound, STYLE_VALUE)],
        [cell("Najdenih rešitev", STYLE_LABEL), cell(result.solver_solution_count, STYLE_VALUE)],
        [cell("Optimality gap", STYLE_LABEL), cell(result.solver_optimality_gap_percent, STYLE_VALUE)],
        [cell("Razlog ustavitve", STYLE_LABEL), cell(result.solver_stop_reason or "—", STYLE_VALUE)],
        [cell("VI robne ure", STYLE_LABEL), cell(result.leader_edge_exception_hours, STYLE_WARNING if result.leader_edge_exception_hours else STYLE_SUCCESS)],
        [cell("VI/FMP prekrivanje", STYLE_LABEL), cell(result.fmp_vi_overlap_hours, STYLE_WARNING if result.fmp_vi_overlap_hours else STYLE_SUCCESS)],
        [cell("Skupaj kriznih ur", STYLE_LABEL), cell(result.crisis_exception_hours, STYLE_WARNING if result.crisis_exception_hours else STYLE_SUCCESS)],
        [],
        [cell("OPOMBE", STYLE_SECTION)],
    ]
    section_rows = [2, 16, 27]
    for note in result.notes:
        rows.append([cell("Informacija", STYLE_LABEL), cell(note, STYLE_VALUE)])
    if not result.notes:
        rows.append([cell("Informacija", STYLE_LABEL), cell("Ni opomb.", STYLE_MUTED)])
    rows.append([])
    rows.append([cell("OPOZORILA", STYLE_SECTION)])
    section_rows.append(len(rows))
    for warning in result.warnings:
        rows.append([cell("Opozorilo", STYLE_WARNING), cell(warning, STYLE_WARNING)])
    if not result.warnings:
        rows.append([cell("Opozorilo", STYLE_LABEL), cell("Ni opozoril.", STYLE_SUCCESS)])

    row_heights = {1: 28.0, **{row: 24.0 for row in section_rows}}
    widths = {1: 30, 2: 100}
    merges = ["A1:P1", *[f"A{row}:P{row}" for row in section_rows]]
    merges.extend(
        f"B{row}:P{row}"
        for row, values in enumerate(rows, start=1)
        if row not in {1, *section_rows} and values
    )
    return rows, widths, row_heights, merges


def shifts_sheet(request: CalculatorWorkbookRequest) -> tuple[list[list[Any]], dict[int, float], list[str]]:
    result = request.result
    rows: list[list[Any]] = [
        [cell("Predlagana sestava izmen", STYLE_TITLE)],
        styled_row(["IZMENA / VLOGA", "FL", "APS", "ACS", "SKUPAJ"], STYLE_HEADER),
    ]
    for item in result.shift_summary:
        rows.append([
            cell(item.shift, STYLE_LABEL),
            cell(item.fl, STYLE_FL),
            cell(item.aps, STYLE_APS),
            cell(item.acs, STYLE_ACS),
            cell(item.total, STYLE_VALUE),
        ])
    rows.append([
        cell("SKUPAJ", STYLE_HEADER),
        cell(sum(item.fl for item in result.shift_summary), STYLE_FL),
        cell(sum(item.aps for item in result.shift_summary), STYLE_APS),
        cell(sum(item.acs for item in result.shift_summary), STYLE_ACS),
        cell(sum(item.total for item in result.shift_summary), STYLE_ACCENT),
    ])
    return rows, {1: 26, 2: 10, 3: 10, 4: 10, 5: 12}, ["A1:P1"]


def people_sheet(
    request: CalculatorWorkbookRequest,
    person_styles: dict[str, int],
) -> tuple[list[list[Any]], dict[int, float], list[str]]:
    rows: list[list[Any]] = [
        [cell("Ljudje in obremenitve", STYLE_TITLE)],
        styled_row(
            ["ID", "VLOGA", "VIR", "IZMENA", "LICENCA", "SH", "MAX SH", "IZKORIŠČENOST", "NA SEKTORJU"],
            STYLE_HEADER,
        ),
    ]
    for person in request.result.people:
        rows.append([
            cell(display_person_id(person), person_styles[person.id]),
            cell(person.role or "—", STYLE_VALUE),
            cell(person_source_label(person.source), STYLE_VALUE),
            cell(person.shift, STYLE_VALUE),
            cell(person.license, license_style(person.license)),
            cell(person.sector_hours, STYLE_VALUE),
            cell(person.max_sector_hours, STYLE_VALUE),
            cell(
                f"{person.utilization_percent} %",
                STYLE_SUCCESS if person.utilization_percent >= 90 else STYLE_WARNING if person.utilization_percent >= 60 else STYLE_DANGER,
            ),
            cell("DA" if person.used_as_sector_controller else "NE", STYLE_SUCCESS if person.used_as_sector_controller else STYLE_MUTED),
        ])
    return (
        rows,
        {1: 14, 2: 12, 3: 14, 4: 12, 5: 12, 6: 10, 7: 10, 8: 18, 9: 16},
        ["A1:P1"],
    )


def pareto_sheet(request: CalculatorWorkbookRequest) -> tuple[list[list[Any]], dict[int, float], list[str]]:
    rows: list[list[Any]] = [
        [cell("Pareto analiza", STYLE_TITLE)],
        styled_row(
            [
                "LIMIT LJUDI",
                "PLANIRANI",
                "AKTIVNI",
                "SH",
                "CILJ SH",
                "POKRITOST",
                "MANJKA",
                "KONTROLORSKE URE",
                "IZKORIŠČENOST",
                "OFFICE",
                "IZVEDLJIVO",
                "STATUS",
            ],
            STYLE_HEADER,
        ),
    ]
    for point in request.result.pareto_points:
        rows.append([
            cell(point.people_limit, STYLE_VALUE),
            cell(point.planned_people, STYLE_VALUE),
            cell(point.active_people, STYLE_VALUE),
            cell(point.max_sector_hours, STYLE_ACCENT),
            cell(point.requested_sector_hours, STYLE_VALUE),
            cell(f"{point.coverage_percent} %", STYLE_SUCCESS if point.coverage_percent >= 100 else STYLE_WARNING),
            cell(point.missing_sector_hours, STYLE_DANGER if point.missing_sector_hours else STYLE_SUCCESS),
            cell(f"{point.scheduled_person_hours}/{point.total_person_capacity_hours}", STYLE_VALUE),
            cell(f"{point.utilization_percent} %", STYLE_VALUE),
            cell(point.used_officers, STYLE_VALUE),
            cell("DA" if point.feasible else "NE", STYLE_SUCCESS if point.feasible else STYLE_DANGER),
            cell(point.solver_status or "—", STYLE_VALUE),
        ])
    if not request.result.pareto_points:
        rows.append([cell("Pareto analiza pri tem izračunu ni bila izvedena.", STYLE_MUTED)])
    return (
        rows,
        {1: 14, 2: 13, 3: 12, 4: 10, 5: 10, 6: 14, 7: 10, 8: 20, 9: 18, 10: 10, 11: 14, 12: 18},
        ["A1:P1"],
    )


def offset_merge_reference(reference: str, row_offset: int) -> str:
    return re.sub(
        r"([A-Z]+)(\d+)",
        lambda match: f"{match.group(1)}{int(match.group(2)) + row_offset}",
        reference,
    )


def package_workbook(
    sheets: list[tuple[str, str]],
    styles_xml: str,
    print_areas: dict[str, str] | None = None,
) -> bytes:
    defined_names = ""
    if print_areas:
        entries: list[str] = []
        for index, (name, _) in enumerate(sheets):
            area = print_areas.get(name)
            if not area:
                continue
            absolute_area = re.sub(
                r"([A-Z]+)(\d+)",
                lambda match: f"${match.group(1)}${match.group(2)}",
                area,
            )
            quoted_name = name.replace("'", "''")
            entries.append(
                f'<definedName name="_xlnm.Print_Area" localSheetId="{index}">'
                f"'{escape(quoted_name)}'!{absolute_area}</definedName>"
            )
        if entries:
            defined_names = f'<definedNames>{"".join(entries)}</definedNames>'

    workbook_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets>'
        + "".join(
            f'<sheet name="{escape(name)}" sheetId="{index}" r:id="rId{index}"/>'
            for index, (name, _) in enumerate(sheets, start=1)
        )
        + "</sheets>"
        f"{defined_names}"
        '<calcPr calcId="124519" fullCalcOnLoad="1" forceFullCalc="1"/>'
        "</workbook>"
    )
    workbook_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        + "".join(
            f'<Relationship Id="rId{index}" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
            f'Target="worksheets/sheet{index}.xml"/>'
            for index in range(1, len(sheets) + 1)
        )
        + f'<Relationship Id="rId{len(sheets) + 1}" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
        'Target="styles.xml"/>'
        "</Relationships>"
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/styles.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
        + "".join(
            f'<Override PartName="/xl/worksheets/sheet{index}.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            for index in range(1, len(sheets) + 1)
        )
        + '<Override PartName="/docProps/core.xml" '
        'ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
        '<Override PartName="/docProps/app.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>'
        "</Types>"
    )
    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="xl/workbook.xml"/>'
        '<Relationship Id="rId2" '
        'Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" '
        'Target="docProps/core.xml"/>'
        '<Relationship Id="rId3" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" '
        'Target="docProps/app.xml"/>'
        "</Relationships>"
    )
    created = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    core_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:dcterms="http://purl.org/dc/terms/" '
        'xmlns:dcmitype="http://purl.org/dc/dcmitype/" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        '<dc:creator>ATCConfMaker</dc:creator>'
        '<cp:lastModifiedBy>ATCConfMaker</cp:lastModifiedBy>'
        f'<dcterms:created xsi:type="dcterms:W3CDTF">{created}</dcterms:created>'
        f'<dcterms:modified xsi:type="dcterms:W3CDTF">{created}</dcterms:modified>'
        "</cp:coreProperties>"
    )
    app_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" '
        'xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">'
        '<Application>ATCConfMaker</Application>'
        f'<HeadingPairs><vt:vector size="2" baseType="variant"><vt:variant><vt:lpstr>Worksheets</vt:lpstr>'
        f'</vt:variant><vt:variant><vt:i4>{len(sheets)}</vt:i4></vt:variant></vt:vector></HeadingPairs>'
        f'<TitlesOfParts><vt:vector size="{len(sheets)}" baseType="lpstr">'
        + "".join(f"<vt:lpstr>{escape(name)}</vt:lpstr>" for name, _ in sheets)
        + "</vt:vector></TitlesOfParts>"
        "</Properties>"
    )

    output = BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", root_rels)
        archive.writestr("docProps/core.xml", core_xml)
        archive.writestr("docProps/app.xml", app_xml)
        archive.writestr("xl/workbook.xml", workbook_xml)
        archive.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        archive.writestr("xl/styles.xml", styles_xml)
        for index, (_, sheet_xml) in enumerate(sheets, start=1):
            archive.writestr(f"xl/worksheets/sheet{index}.xml", sheet_xml)
    return output.getvalue()


def build_result_workbook(request: CalculatorWorkbookRequest) -> bytes:
    person_styles = {
        person.id: PERSON_STYLE_START + index
        for index, person in enumerate(request.result.people)
    }
    config_rows, config_widths, config_heights, config_merges = configuration_sheet(request, person_styles)
    summary_rows, summary_widths, summary_heights, summary_merges = summary_sheet(request)
    shift_rows, shift_widths, shift_merges = shifts_sheet(request)
    people_rows, people_widths, people_merges = people_sheet(request, person_styles)
    pareto_rows, pareto_widths, pareto_merges = pareto_sheet(request)
    (
        okzp_rows,
        okzp_widths,
        okzp_heights,
        okzp_merges,
        okzp_conditional_formatting,
        okzp_print_area,
    ) = okzp_configuration_sheet(request, person_styles)

    combined_rows = list(config_rows)
    combined_merges = list(config_merges)
    combined_heights = dict(config_heights)
    combined_widths = dict(config_widths)

    sections = [
        (summary_rows, summary_widths, summary_heights, summary_merges),
        (shift_rows, shift_widths, {}, shift_merges),
        (people_rows, people_widths, {}, people_merges),
        (pareto_rows, pareto_widths, {}, pareto_merges),
    ]
    for section_rows, section_widths, section_heights, section_merges in sections:
        combined_rows.extend([[], []])
        row_offset = len(combined_rows)
        combined_rows.extend(section_rows)
        combined_merges.extend(
            offset_merge_reference(reference, row_offset)
            for reference in section_merges
        )
        combined_heights.update(
            {
                row + row_offset: height
                for row, height in section_heights.items()
            }
        )
        for column, width in section_widths.items():
            if column == 2 and width > 20:
                continue
            combined_widths[column] = max(combined_widths.get(column, 0), width)

    sheets = [
        (
            "KONFIGURACIJA",
            worksheet_xml(
                combined_rows,
                widths=combined_widths,
                freeze_row=6,
                freeze_col=3,
                row_heights=combined_heights,
                merges=combined_merges,
                landscape=True,
            ),
        ),
        (
            "OKZP RAZPORED",
            worksheet_xml(
                okzp_rows,
                widths=okzp_widths,
                freeze_row=2,
                freeze_col=5,
                row_heights=okzp_heights,
                merges=okzp_merges,
                conditional_formatting=okzp_conditional_formatting,
                show_grid_lines=False,
                landscape=True,
            ),
        ),
    ]
    return package_workbook(
        sheets,
        workbook_styles_xml(len(request.result.people)),
        print_areas={"OKZP RAZPORED": okzp_print_area},
    )
