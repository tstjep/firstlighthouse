"""Google Sheets signal-update tool for immigration lead agents.

Updates the five signal columns (Yes/No + source) for a specific company
row identified by its 1-based row index.

Immigration sheet column mapping (A:S):
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
  L  TechForward Signal           |  M  Source
  N  MultiVisa Signal             |  O  Source
  P  HighVolume Signal            |  Q  Source
  R  Growth Signal                |  S  Source
"""

from typing import Any

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

from nanobot.agent.tools.base import Tool

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

SIGNAL_COLS = {
    "corporate":  ("J", "K"),
    "tech":       ("L", "M"),
    "multivisa":  ("N", "O"),
    "highvolume": ("P", "Q"),
    "growth":     ("R", "S"),
}

VALID_SIGNALS = sorted(SIGNAL_COLS)


class SheetsUpdateSignalTool(Tool):
    """Update one immigration signal for a company row in the lead tracker."""

    def __init__(self, spreadsheet_id: str, credentials_file: str, sheet_name: str = "LawFirms"):
        self._spreadsheet_id = spreadsheet_id
        self._credentials_file = credentials_file
        self._sheet_name = sheet_name
        self._service = None

    def _get_service(self):
        if self._service is None:
            creds = Credentials.from_service_account_file(
                self._credentials_file, scopes=SCOPES
            )
            self._service = build("sheets", "v4", credentials=creds)
        return self._service

    @property
    def name(self) -> str:
        return "sheets_update_signal"

    @property
    def description(self) -> str:
        return (
            "Update one immigration buying signal for a company row in the Google Sheet. "
            "Provide the row_index (from sheets_read_companies), the signal name, "
            "whether it was detected (True/False), and a short source note — the "
            "title or snippet where the signal was found (or 'not found' if False). "
            "Today's date is written automatically. "
            f"Signal names: {', '.join(repr(s) for s in VALID_SIGNALS)}."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "row_index": {
                    "type": "integer",
                    "description": "1-based sheet row number from sheets_read_companies (e.g. 2 for first data row)",
                    "minimum": 2,
                },
                "signal": {
                    "type": "string",
                    "enum": VALID_SIGNALS,
                    "description": (
                        "Which signal to update: "
                        "'corporate' = sponsor licence / skilled worker / corporate clients; "
                        "'tech' = client portal / online application / document upload; "
                        "'multivisa' = handles 3+ visa types; "
                        "'highvolume' = large team or many clients mentioned; "
                        "'growth' = hiring / new office / expanding."
                    ),
                },
                "detected": {
                    "type": "boolean",
                    "description": "True if the signal was found in search results, False otherwise",
                },
                "source": {
                    "type": "string",
                    "description": (
                        "Short note on where the signal was found: page title, snippet excerpt, "
                        "or URL. Write 'not found' if detected=False. Max ~200 chars."
                    ),
                },
            },
            "required": ["row_index", "signal", "detected", "source"],
        }

    async def execute(
        self,
        row_index: int,
        signal: str,
        detected: bool,
        source: str = "",
        **kwargs: Any,
    ) -> str:
        if signal not in SIGNAL_COLS:
            return (
                f"[sheets_update_signal error] Unknown signal {signal!r}. "
                f"Valid signals: {VALID_SIGNALS}"
            )
        if row_index < 2:
            return f"[sheets_update_signal error] row_index must be >= 2 (got {row_index})"

        bool_col, source_col = SIGNAL_COLS[signal]
        value = "Yes" if detected else "No"
        tab = self._sheet_name

        try:
            service = self._get_service()
            service.spreadsheets().values().batchUpdate(
                spreadsheetId=self._spreadsheet_id,
                body={
                    "valueInputOption": "USER_ENTERED",
                    "data": [
                        {"range": f"{tab}!{bool_col}{row_index}",   "values": [[value]]},
                        {"range": f"{tab}!{source_col}{row_index}", "values": [[source]]},
                    ],
                },
            ).execute()
        except Exception as exc:
            return f"[sheets_update_signal error] Row {row_index} signal={signal}: {exc}"

        return f"Row {row_index}: {signal} = {value} (source: {source[:60]}…)"
