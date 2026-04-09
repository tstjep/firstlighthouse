"""
immigration_sheets_setup.py

Creates / re-formats the LawFairy immigration leads Google Sheet.

Sheet structure (A:S = 19 cols):
  A  Company Name
  B  Comment LawFairy
  C  Rating
  D  Notes
  E  Website
  F  LinkedIn
  G  Size
  H  HQ Location
  I  Date Added
  J  CorporateImmigration Signal  |  K  Source
  L  Specialist Signal            |  M  Source
  N  MultiVisa Signal             |  O  Source
  P  HighVolume Signal            |  Q  Source
  R  Growth Signal                |  S  Source

Usage:
    python immigration_sheets_setup.py
    python immigration_sheets_setup.py --tab LawFirms
    python immigration_sheets_setup.py --credentials lawfairy.json
"""

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

import config
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# ── Column definitions (A:S = 19 columns) ─────────────────────────────────

COLUMNS = [
    # Company Info
    ("Company Name",                "Company legal or trading name"),
    ("Comment LawFairy",            "Internal LawFairy team comments"),
    ("Rating",                      "Lead rating 1–5 (5 = prime prospect)"),
    ("Notes",                       "Free-text notes about the company"),
    ("Website",                     "Primary website URL"),
    ("LinkedIn",                    "LinkedIn company page URL"),
    ("Size",                        "Employee count range, e.g. '11-50'"),
    ("HQ Location",                 "City, e.g. 'London, UK'"),
    ("Date Added",                  "Date first added (YYYY-MM-DD)"),
    # Signal columns (signal + source, no date)
    ("Corporate Signal",            "Yes/No – handles corporate immigration / sponsor licence"),
    ("Corporate Source",            "Title/snippet/URL where Corporate signal was found"),
    ("Specialist Signal",           "Yes/No – immigration is the firm's primary or sole practice area"),
    ("Specialist Source",          "Title/snippet/URL where Specialist signal was found"),
    ("MultiVisa Signal",            "Yes/No – handles 3+ distinct visa types"),
    ("MultiVisa Source",            "Title/snippet/URL where MultiVisa signal was found"),
    ("HighVolume Signal",           "Yes/No – large team or high caseload"),
    ("HighVolume Source",           "Title/snippet/URL where HighVolume signal was found"),
    ("Growth Signal",               "Yes/No – actively hiring / expanding"),
    ("Growth Source",               "Title/snippet/URL where Growth signal was found"),
    ("Contacts",                    "Decision-maker contacts: 'First Last | Role | linkedin_url' (one per line)"),
]

# 0-based indices for signal boolean columns
SIGNAL_BOOL_COLS   = [9, 11, 13, 15, 17]   # J, L, N, P, R
SIGNAL_SOURCE_COLS = [10, 12, 14, 16, 18]  # K, M, O, Q, S
COL_CONTACTS       = 19  # T

# ── Colours ────────────────────────────────────────────────────────────────

HEADER_BG       = {"red": 0.106, "green": 0.282, "blue": 0.490}   # deep navy
SIGNAL_YES_BG   = {"red": 0.714, "green": 0.843, "blue": 0.659}   # soft green #B6D7A8
SIGNAL_NO_BG    = {"red": 0.918, "green": 0.600, "blue": 0.600}   # soft red   #EA9999
RATING_HIGH_BG  = {"red": 0.576, "green": 0.769, "blue": 0.490}   # strong green  (8-10)
RATING_MID_BG   = {"red": 0.714, "green": 0.843, "blue": 0.659}   # soft green    (6-7)
RATING_LOW_BG   = {"red": 0.988, "green": 0.898, "blue": 0.804}   # amber         (4-5)
RATING_POOR_BG  = {"red": 0.918, "green": 0.600, "blue": 0.600}   # soft red      (1-3)
ROW_LIGHT       = {"red": 0.949, "green": 0.949, "blue": 0.949}   # #F2F2F2
ROW_DARK        = {"red": 0.820, "green": 0.820, "blue": 0.820}   # #D1D1D1
WHITE           = {"red": 1.0,   "green": 1.0,   "blue": 1.0}
BLACK           = {"red": 0.0,   "green": 0.0,   "blue": 0.0}

# ── Column widths ──────────────────────────────────────────────────────────

COL_WIDTHS = {
    0:  200,   # Company Name
    1:  220,   # Comment LawFairy
    2:  80,    # Rating
    3:  260,   # Notes
    4:  200,   # Website
    5:  200,   # LinkedIn
    6:  90,    # Size
    7:  140,   # HQ Location
    8:  120,   # Date Added
    9:  100,   # Corporate Signal
    10: 380,   # Corporate Source
    11: 90,    # Specialist Signal
    12: 380,   # Specialist Source
    13: 100,   # MultiVisa Signal
    14: 380,   # MultiVisa Source
    15: 110,   # HighVolume Signal
    16: 380,   # HighVolume Source
    17: 90,    # Growth Signal
    18: 380,   # Growth Source
    19: 340,   # Contacts
}

# ── Filter views ───────────────────────────────────────────────────────────

_YES = {"condition": {"type": "TEXT_EQ", "values": [{"userEnteredValue": "Yes"}]}}
_BLANK = {"condition": {"type": "BLANK"}}

FILTER_VIEWS = [
    ("Corporate Signal",   {"9":  _YES}),
    ("Specialist Signal",  {"11": _YES}),
    ("MultiVisa Signal",   {"13": _YES}),
    ("HighVolume Signal",  {"15": _YES}),
    ("Growth Signal",      {"17": _YES}),
    ("Corporate + Specialist", {"9": _YES, "11": _YES}),
    ("Corporate + Volume", {"9":  _YES, "15": _YES}),
    ("Not yet scanned",    {"9":  _BLANK}),
]


# ── Helpers ────────────────────────────────────────────────────────────────

def build_service(credentials_path: str):
    creds = Credentials.from_service_account_file(credentials_path, scopes=SCOPES)
    print(f"Service account: {creds.service_account_email}")
    return build("sheets", "v4", credentials=creds)


def get_or_create_tab(service, spreadsheet_id: str, tab_name: str) -> int:
    meta = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    for s in meta["sheets"]:
        if s["properties"]["title"] == tab_name:
            return s["properties"]["sheetId"]
    resp = service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": [{"addSheet": {"properties": {"title": tab_name}}}]},
    ).execute()
    return resp["replies"][0]["addSheet"]["properties"]["sheetId"]


def clear_tab_content(service, spreadsheet_id: str, tab_name: str) -> None:
    service.spreadsheets().values().clear(
        spreadsheetId=spreadsheet_id,
        range=f"{tab_name}!A:Z",
    ).execute()


def clear_conditional_formats(service, spreadsheet_id: str, sheet_id: int) -> None:
    meta = service.spreadsheets().get(
        spreadsheetId=spreadsheet_id,
        fields="sheets.properties.sheetId,sheets.conditionalFormats",
    ).execute()
    count = 0
    for s in meta.get("sheets", []):
        if s["properties"]["sheetId"] == sheet_id:
            count = len(s.get("conditionalFormats", []))
            break
    if count == 0:
        return
    requests = [
        {"deleteConditionalFormatRule": {"sheetId": sheet_id, "index": i}}
        for i in range(count - 1, -1, -1)
    ]
    service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": requests},
    ).execute()


def clear_banded_ranges(service, spreadsheet_id: str, sheet_id: int) -> None:
    meta = service.spreadsheets().get(
        spreadsheetId=spreadsheet_id,
        fields="sheets.properties.sheetId,sheets.bandedRanges.bandedRangeId",
    ).execute()
    for sheet in meta.get("sheets", []):
        if sheet["properties"]["sheetId"] == sheet_id:
            ids = [br["bandedRangeId"] for br in sheet.get("bandedRanges", [])]
            if ids:
                service.spreadsheets().batchUpdate(
                    spreadsheetId=spreadsheet_id,
                    body={"requests": [{"deleteBanding": {"bandedRangeId": bid}} for bid in ids]},
                ).execute()
            return


def clear_filter_views(service, spreadsheet_id: str, sheet_id: int) -> None:
    meta = service.spreadsheets().get(
        spreadsheetId=spreadsheet_id,
        fields="sheets.properties.sheetId,sheets.filterViews.filterViewId",
    ).execute()
    for sheet in meta.get("sheets", []):
        if sheet["properties"]["sheetId"] == sheet_id:
            ids = [fv["filterViewId"] for fv in sheet.get("filterViews", [])]
            if ids:
                service.spreadsheets().batchUpdate(
                    spreadsheetId=spreadsheet_id,
                    body={"requests": [{"deleteFilterView": {"filterId": fid}} for fid in ids]},
                ).execute()
            return


def build_requests(sheet_id: int, num_cols: int) -> list:
    requests = []

    # 0. Reset ALL cell formatting to defaults first (prevents stale navy from previous runs)
    requests.append({
        "updateCells": {
            "range": {"sheetId": sheet_id},
            "fields": "userEnteredFormat",
        }
    })

    # 1. Freeze header row + first column
    requests.append({
        "updateSheetProperties": {
            "properties": {
                "sheetId": sheet_id,
                "gridProperties": {"frozenRowCount": 1, "frozenColumnCount": 1},
            },
            "fields": "gridProperties.frozenRowCount,gridProperties.frozenColumnCount",
        }
    })

    # 2. Header row: navy background, bold white text, centre-aligned
    requests.append({
        "repeatCell": {
            "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1,
                      "startColumnIndex": 0, "endColumnIndex": num_cols},
            "cell": {
                "userEnteredFormat": {
                    "backgroundColor": HEADER_BG,
                    "textFormat": {"bold": True, "foregroundColor": WHITE, "fontSize": 10},
                    "horizontalAlignment": "CENTER",
                    "verticalAlignment": "MIDDLE",
                    "wrapStrategy": "WRAP",
                }
            },
            "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment,"
                      "verticalAlignment,wrapStrategy)",
        }
    })

    # 3. Data rows: dark text only — do NOT set backgroundColor here as it overrides banding
    requests.append({
        "repeatCell": {
            "range": {"sheetId": sheet_id, "startRowIndex": 1,
                      "startColumnIndex": 0, "endColumnIndex": num_cols},
            "cell": {"userEnteredFormat": {"textFormat": {"foregroundColor": BLACK}}},
            "fields": "userEnteredFormat.textFormat.foregroundColor",
        }
    })

    # 4. Centre-align signal boolean columns
    for col in SIGNAL_BOOL_COLS:
        requests.append({
            "repeatCell": {
                "range": {"sheetId": sheet_id, "startRowIndex": 1,
                          "startColumnIndex": col, "endColumnIndex": col + 1},
                "cell": {"userEnteredFormat": {"horizontalAlignment": "CENTER"}},
                "fields": "userEnteredFormat.horizontalAlignment",
            }
        })

    # 5. Wrap text in source columns
    for col in SIGNAL_SOURCE_COLS:
        requests.append({
            "repeatCell": {
                "range": {"sheetId": sheet_id, "startRowIndex": 1,
                          "startColumnIndex": col, "endColumnIndex": col + 1},
                "cell": {"userEnteredFormat": {"wrapStrategy": "WRAP"}},
                "fields": "userEnteredFormat.wrapStrategy",
            }
        })

    # 6. Date Added (col 8 = I): date number format
    requests.append({
        "repeatCell": {
            "range": {"sheetId": sheet_id, "startRowIndex": 1,
                      "startColumnIndex": 8, "endColumnIndex": 9},
            "cell": {"userEnteredFormat": {
                "numberFormat": {"type": "DATE", "pattern": "yyyy-mm-dd"}
            }},
            "fields": "userEnteredFormat.numberFormat",
        }
    })

    # 7. Data validation: signal boolean cols → Yes / No dropdown
    for col in SIGNAL_BOOL_COLS:
        requests.append({
            "setDataValidation": {
                "range": {"sheetId": sheet_id, "startRowIndex": 1,
                          "startColumnIndex": col, "endColumnIndex": col + 1},
                "rule": {
                    "condition": {
                        "type": "ONE_OF_LIST",
                        "values": [
                            {"userEnteredValue": "Yes"},
                            {"userEnteredValue": "No"},
                        ],
                    },
                    "inputMessage": "Select Yes or No",
                    "strict": True,
                    "showCustomUi": True,
                },
            }
        })

    # 8. Conditional formatting: Yes → green, No → red
    for col in SIGNAL_BOOL_COLS:
        col_range = {"sheetId": sheet_id, "startRowIndex": 1,
                     "startColumnIndex": col, "endColumnIndex": col + 1}
        requests.append({
            "addConditionalFormatRule": {
                "rule": {
                    "ranges": [col_range],
                    "booleanRule": {
                        "condition": {"type": "TEXT_EQ", "values": [{"userEnteredValue": "Yes"}]},
                        "format": {"backgroundColor": SIGNAL_YES_BG},
                    },
                },
                "index": 0,
            }
        })
        requests.append({
            "addConditionalFormatRule": {
                "rule": {
                    "ranges": [col_range],
                    "booleanRule": {
                        "condition": {"type": "TEXT_EQ", "values": [{"userEnteredValue": "No"}]},
                        "format": {"backgroundColor": SIGNAL_NO_BG},
                    },
                },
                "index": 1,
            }
        })

    # 9. Rating column (col 2 = C): colour by value using NUMBER_BETWEEN
    #    Bands: 8-10 = strong green, 6-7 = soft green, 4-5 = amber, 1-3 = red
    #    Provisional ratings (~N) are text so NUMBER_BETWEEN won't match — left uncoloured.
    rating_col = {"sheetId": sheet_id, "startRowIndex": 1,
                  "startColumnIndex": 2, "endColumnIndex": 3}
    for lo, hi, bg in [
        ("8",  "10", RATING_HIGH_BG),
        ("6",  "7",  RATING_MID_BG),
        ("4",  "5",  RATING_LOW_BG),
        ("1",  "3",  RATING_POOR_BG),
    ]:
        requests.append({
            "addConditionalFormatRule": {
                "rule": {
                    "ranges": [rating_col],
                    "booleanRule": {
                        "condition": {
                            "type": "NUMBER_BETWEEN",
                            "values": [
                                {"userEnteredValue": lo},
                                {"userEnteredValue": hi},
                            ],
                        },
                        "format": {"backgroundColor": bg},
                    },
                },
                "index": 0,
            }
        })

    # 10. Column widths
    for col_index, width_px in COL_WIDTHS.items():
        requests.append({
            "updateDimensionProperties": {
                "range": {"sheetId": sheet_id, "dimension": "COLUMNS",
                          "startIndex": col_index, "endIndex": col_index + 1},
                "properties": {"pixelSize": width_px},
                "fields": "pixelSize",
            }
        })

    # 11. Header row height
    requests.append({
        "updateDimensionProperties": {
            "range": {"sheetId": sheet_id, "dimension": "ROWS",
                      "startIndex": 0, "endIndex": 1},
            "properties": {"pixelSize": 48},
            "fields": "pixelSize",
        }
    })

    # 12. Auto-filter on header row
    requests.append({
        "setBasicFilter": {
            "filter": {
                "range": {"sheetId": sheet_id, "startRowIndex": 0,
                          "startColumnIndex": 0, "endColumnIndex": num_cols},
            }
        }
    })

    # 13. Alternating row banding
    # Use colorStyle (not the deprecated color field) to avoid the blue-row bug
    requests.append({
        "addBanding": {
            "bandedRange": {
                "range": {"sheetId": sheet_id, "startRowIndex": 1,
                          "startColumnIndex": 0, "endColumnIndex": num_cols},
                "rowProperties": {
                    "headerColorStyle":      {"rgbColor": WHITE},
                    "firstBandColorStyle":   {"rgbColor": ROW_LIGHT},
                    "secondBandColorStyle":  {"rgbColor": ROW_DARK},
                },
            }
        }
    })

    return requests


def build_filter_view_requests(sheet_id: int, num_cols: int, existing_ids: list[int]) -> list:
    requests = [{"deleteFilterView": {"filterId": fid}} for fid in existing_ids]
    for title, criteria in FILTER_VIEWS:
        requests.append({
            "addFilterView": {
                "filter": {
                    "title": title,
                    "range": {"sheetId": sheet_id, "startRowIndex": 0,
                              "startColumnIndex": 0, "endColumnIndex": num_cols},
                    "criteria": criteria,
                }
            }
        })
    return requests


def setup_tab(spreadsheet_id: str, tab_name: str, credentials_path: str,
              clear_data: bool = False) -> None:
    service = build_service(credentials_path)
    num_cols = len(COLUMNS)

    sheet_id = get_or_create_tab(service, spreadsheet_id, tab_name)
    print(f"  Tab '{tab_name}' ready (sheetId={sheet_id})")

    if clear_data:
        print(f"  Clearing existing content…")
        clear_tab_content(service, spreadsheet_id, tab_name)

    # Always clear formatting before reapplying
    clear_conditional_formats(service, spreadsheet_id, sheet_id)
    clear_banded_ranges(service, spreadsheet_id, sheet_id)

    # Write headers (row 1 only — never touches data rows)
    headers = [[col[0] for col in COLUMNS]]
    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=f"{tab_name}!A1",
        valueInputOption="RAW",
        body={"values": headers},
    ).execute()
    print(f"  Headers written ({num_cols} columns, A:T)")

    # Apply formatting
    requests = build_requests(sheet_id=sheet_id, num_cols=num_cols)
    service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": requests},
    ).execute()
    print(f"  Formatting applied")

    # Filter views
    clear_filter_views(service, spreadsheet_id, sheet_id)
    fv_requests = build_filter_view_requests(sheet_id, num_cols, [])
    service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": fv_requests},
    ).execute()
    print(f"  {len(FILTER_VIEWS)} filter views applied")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Set up the LawFairy immigration leads Google Sheet."
    )
    parser.add_argument(
        "--tab",
        choices=config.ALL_TABS,
        default=None,
        help="Single tab to set up. Omit to set up all immigration tabs.",
    )
    parser.add_argument(
        "--credentials",
        default=config.CREDENTIALS_FILE,
        help=f"Service account JSON key (default: {config.CREDENTIALS_FILE})",
    )
    args = parser.parse_args()

    tabs = [args.tab] if args.tab else config.IMMIGRATION_TABS
    credentials_path = str(PROJECT_ROOT / args.credentials)

    print(f"Spreadsheet: {config.SPREADSHEET_ID}")
    print(f"Tabs:        {', '.join(tabs)}\n")

    for tab in tabs:
        print(f"[{tab}]")
        setup_tab(config.SPREADSHEET_ID, tab_name=tab, credentials_path=credentials_path,
                  clear_data=False)
        print()

    url = f"https://docs.google.com/spreadsheets/d/{config.SPREADSHEET_ID}"
    print(f"Done! {url}")
