"""Present data without a chat connector: CSV, XLSX, HTML, and optional Sheets."""
import csv
from datetime import datetime
from html import escape
import json
from pathlib import Path
import urllib.parse
import urllib.request

from .common import RocError, write_json
from .docket import TEAM_VALUES
from .review import case_issues, issue_text

HEADERS = ["Case number", "Case title", "Case Type", "Team", "Court", "District", "Date filed", "Nature of Case", "Status (PACER)", "PACER link"]
FIELDS = ["caseNumber", "caseTitle", "caseType", "team", "court", "district", "dateFiled", "nature", "status", "pacerLink"]
WIDTHS = [20, 38, 14, 21, 24, 30, 15, 40, 18, 22]


def values(case):
    return [case.get(field, "") for field in FIELDS]


def csv_safe(value):
    value = str(value)
    return "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) else value


def source_note(case):
    evidence = case.get("enrichment")
    if not evidence:
        return "Source: official PCL index. " + "; ".join(case["warnings"])
    coverage = evidence.get("courtCoverage")
    validation = (" Court retrieval validation at run: " + coverage["validationStatus"] +
                  "; live-verified samples: " + str(coverage["verifiedSamples"]) + "." if coverage else "")
    return ("Docket: " + evidence["sourceFile"] + "; SHA256: " + evidence["sourceSha256"] +
            ". Represented parties: " + ", ".join(evidence["representedParties"]) +
            ". Source roles: " + "; ".join(p["party"] + ": " + p["role"] for p in evidence.get("representedRoles", [])) +
            ". Counts copy all listed source rows, including original, superseding and terminated entries. " +
            "Defendant associations, source sections and dispositions are retained in evidence.json. " +
            "; ".join(issue_text(i) for i in case_issues(case)) + validation)


def export_local(cases, folder, title, metadata):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / "case-index.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(HEADERS)
        writer.writerows([csv_safe(x) for x in values(c)] for c in cases)
    write_json(folder / "evidence.json", {"run": metadata, "cases": cases})
    write_json(folder / "review.json", [{"caseNumber": c["caseNumber"], "court": c["courtId"],
               "issues": case_issues(c), "items": [issue_text(i) for i in case_issues(c)]}
               for c in cases if case_issues(c)])
    try:
        from openpyxl import Workbook
        from openpyxl.comments import Comment
        from openpyxl.styles import Alignment, Font, PatternFill
        from openpyxl.utils import get_column_letter
        from openpyxl.worksheet.datavalidation import DataValidation
    except ImportError:
        raise RocError("Install ROC's openpyxl dependency to export the workbook. CSV and evidence were saved.") from None
    wb = Workbook()
    sheet = wb.active
    sheet.title = "Case index"
    sheet.sheet_view.showGridLines = False
    for row in [[], [title], [f"{len(cases)} cases. Generated {metadata['generatedUtc'][:10]}."],
                ["Official PCL API index; court-web docket enrichment where available. See cell notes and evidence.json."],
                ["Nature of Case copies listed counts, including original and superseding entries. Source gaps and review items are categorized in review.json."], HEADERS]:
        sheet.append(row)
    for column, width in enumerate(WIDTHS, 1):
        sheet.column_dimensions[get_column_letter(column)].width = width
    sheet["A2"].font = Font(name="Arial", size=15, bold=True)
    for row_number, case in enumerate(cases, 7):
        for col, val in enumerate(values(case), 1):
            cell = sheet.cell(row_number, col, val)
            cell.data_type = "s"  # Never interpret case text as an Excel formula.
            cell.font = Font(name="Arial", size=10)
            cell.alignment = Alignment(vertical="center", wrap_text=col == 2)
        try:
            sheet.cell(row_number, 7, datetime.strptime(case["dateFiled"], "%Y-%m-%d"))
            sheet.cell(row_number, 7).number_format = "mm/dd/yy"
        except ValueError:
            pass
        sheet.row_dimensions[row_number].height = 28
        if case["pacerLink"].startswith("https://ecf."):
            sheet.cell(row_number, 10).hyperlink = case["pacerLink"]
            sheet.cell(row_number, 10).font = Font(name="Arial", size=10, color="1155CC")
        for col in (4, 8):
            sheet.cell(row_number, col).comment = Comment(source_note(case), "ROC")
    for cell in sheet[6]:
        cell.font = Font(name="Arial", size=10, bold=True)
        cell.fill = PatternFill("solid", fgColor="F1F3F4")
    end = max(7, len(cases) + 6)
    sheet.freeze_panes = "C7"
    sheet.auto_filter.ref = f"A6:J{end}"
    dv = DataValidation(type="list", formula1='"' + ','.join(TEAM_VALUES) + '"', allow_blank=True)
    sheet.add_data_validation(dv)
    dv.add(f"D7:D{end}")
    wb.save(folder / "case-index.xlsx")
    headings = "".join(f"<th>{escape(h)}</th>" for h in HEADERS)
    rows = "".join("<tr>" + "".join(f'<td title="{escape(str(v), quote=True)}">{escape(str(v))}</td>' for v in values(c)) + "</tr>" for c in cases)
    (folder / "case-index.html").write_text("<!doctype html><meta charset=utf-8><title>ROC case index</title><style>body{font:14px Arial;margin:28px;color:#202124}table{border-collapse:collapse;table-layout:fixed;width:1900px}th,td{border-bottom:1px solid #ddd;text-align:left;padding:9px;overflow:hidden;white-space:nowrap}th{background:#f1f3f4;position:sticky;top:0}th:nth-child(8){width:300px}h1{font-size:24px}</style><h1>" + escape(title) + "</h1><p>" + str(len(cases)) + " cases. Hover a clipped cell to read its contents.</p><table><thead><tr>" + headings + "</tr></thead><tbody>" + rows + "</tbody></table>", encoding="utf-8")
    return folder / "case-index.xlsx"


def google_request(url, payload, token):
    request = urllib.request.Request(url, json.dumps(payload).encode(), headers={"Content-Type": "application/json", "Authorization": "Bearer " + token}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.load(response)
    except Exception:
        raise RocError("Google Sheets request failed; no automatic retry. Check Drive for a partially created workbook.") from None


def google_token(credentials):
    """Use the operator's own OAuth grant; never read Codex connector credentials."""
    from .common import read_json
    creds = read_json(credentials)
    required = ("client_id", "client_secret", "refresh_token")
    if not all(creds.get(k) for k in required):
        raise RocError("Google OAuth file must contain client_id, client_secret, refresh_token.")
    body = urllib.parse.urlencode({k: creds[k] for k in required} | {"grant_type": "refresh_token"}).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request("https://oauth2.googleapis.com/token", body), timeout=30) as response:
            return json.load(response)["access_token"]
    except Exception:
        raise RocError("Google OAuth refresh failed. Reauthorize the standalone application.") from None


def publish_google(cases, title, credentials):
    # Fresh workbook per publication. Never overwrite a user's live edits.
    token = google_token(credentials)
    workbook = google_request("https://sheets.googleapis.com/v4/spreadsheets", {"properties": {"title": title}, "sheets": [{"properties": {"sheetId": 0, "title": "Case index", "gridProperties": {"rowCount": max(1000, len(cases) + 7), "columnCount": 10, "frozenRowCount": 1, "frozenColumnCount": 2}}}]}, token)
    identifier = workbook["spreadsheetId"]
    rows = [{"values": [{"userEnteredValue": {"stringValue": v}} for v in HEADERS]}]
    for case in cases:
        cells = [{"userEnteredValue": {"stringValue": str(v)}} for v in values(case)]
        for col in (3, 7):
            cells[col]["note"] = source_note(case)
        rows.append({"values": cells})
    requests = [{"updateCells": {"start": {"sheetId": 0, "rowIndex": 0, "columnIndex": 0}, "rows": rows, "fields": "userEnteredValue,note"}},
                {"repeatCell": {"range": {"sheetId": 0, "startRowIndex": 1, "endRowIndex": len(cases) + 1, "startColumnIndex": 7, "endColumnIndex": 8}, "cell": {"userEnteredFormat": {"wrapStrategy": "CLIP"}}, "fields": "userEnteredFormat.wrapStrategy"}},
                {"setBasicFilter": {"filter": {"range": {"sheetId": 0, "startRowIndex": 0, "endRowIndex": len(cases) + 1, "startColumnIndex": 0, "endColumnIndex": 10}}}}]
    for i, width in enumerate(WIDTHS):
        requests.append({"updateDimensionProperties": {"range": {"sheetId": 0, "dimension": "COLUMNS", "startIndex": i, "endIndex": i + 1}, "properties": {"pixelSize": width * 7}, "fields": "pixelSize"}})
    google_request(f"https://sheets.googleapis.com/v4/spreadsheets/{identifier}:batchUpdate", {"requests": requests}, token)
    return workbook["spreadsheetUrl"]
