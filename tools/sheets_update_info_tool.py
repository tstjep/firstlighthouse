"""Google Sheets company-info update tool for nanobot agents.

Updates the company info columns for an existing row identified by row_index.
Only writes cells where a non-empty value is supplied — existing data is never
overwritten with blanks.

DACH layout (no Human Comment):
  A  Company Name | D  Notes | E  Website | F  LinkedIn | G  Size | H  HQ | X  Date Added

KubeCon layout (has Human Comment at D):
  A  Company Name | E  Notes | F  Website | G  LinkedIn | H  Size | I  HQ | Y  Date Added
"""

from datetime import date
from typing import Any

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

from nanobot.agent.tools.base import Tool

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# DACH layout (no Human Comment column)
_DACH_INFO_COLS = {
    "company_name": "A",
    "notes":        "D",
    "website":      "E",
    "linkedin":     "F",
    "size":         "G",
    "hq_location":  "H",
}
_DACH_DATE_ADDED_COL = "X"

# KubeCon layout (extra Human Comment at D, everything after C shifts +1)
_KUBECON_INFO_COLS = {
    "company_name": "A",
    "notes":        "E",
    "website":      "F",
    "linkedin":     "G",
    "size":         "H",
    "hq_location":  "I",
}
_KUBECON_DATE_ADDED_COL = "Y"


class SheetsUpdateInfoTool(Tool):
    """Update company info fields for an existing row in the IT leads Google Sheet."""

    def __init__(self, spreadsheet_id: str, credentials_file: str, sheet_name: str = "CH"):
        self._spreadsheet_id = spreadsheet_id
        self._credentials_file = credentials_file
        self._sheet_name = sheet_name
        self._service = None
        # KubeCon tab has an extra Human Comment column at D
        is_kubecon = sheet_name == "KubeCon"
        self._info_cols = _KUBECON_INFO_COLS if is_kubecon else _DACH_INFO_COLS
        self._date_added_col = _KUBECON_DATE_ADDED_COL if is_kubecon else _DACH_DATE_ADDED_COL

    def _get_service(self):
        if self._service is None:
            creds = Credentials.from_service_account_file(
                self._credentials_file, scopes=SCOPES
            )
            self._service = build("sheets", "v4", credentials=creds)
        return self._service

    @property
    def name(self) -> str:
        return "sheets_update_company_info"

    @property
    def description(self) -> str:
        return (
            "Update company info fields (company_name, notes, website, LinkedIn, size, HQ location) "
            "for an existing row in the Google Sheets lead tracker. "
            "Only supply fields you want to write — empty strings are ignored so "
            "existing data is never overwritten with blanks. "
            "Date Added is set automatically if the cell is currently blank."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "row_index": {
                    "type": "integer",
                    "description": "1-based sheet row number (e.g. 2 for first data row)",
                    "minimum": 2,
                },
                "company_name": {
                    "type": "string",
                    "description": "Legal or trading name of the company. Leave empty to skip.",
                },
                "website": {
                    "type": "string",
                    "description": "Primary website URL (e.g. https://example.com). Leave empty to skip.",
                },
                "linkedin": {
                    "type": "string",
                    "description": "LinkedIn company page URL. Leave empty to skip.",
                },
                "size": {
                    "type": "string",
                    "description": "Employee count range, e.g. '10-50', '51-200'. Leave empty to skip.",
                },
                "hq_location": {
                    "type": "string",
                    "description": "HQ city and country, e.g. 'Zurich, Switzerland'. Leave empty to skip.",
                },
                "notes": {
                    "type": "string",
                    "description": "Short description of what the company does. Leave empty to skip.",
                },
            },
            "required": ["row_index"],
        }

    async def execute(
        self,
        row_index: int,
        company_name: str = "",
        website: str = "",
        linkedin: str = "",
        size: str = "",
        hq_location: str = "",
        notes: str = "",
        **kwargs: Any,
    ) -> str:
        if row_index < 2:
            return f"[sheets_update_info error] row_index must be >= 2 (got {row_index})"

        tab = self._sheet_name

        field_values = {
            "company_name": company_name,
            "notes":        notes,
            "website":      website,
            "linkedin":     linkedin,
            "size":         size,
            "hq_location":  hq_location,
        }

        # Collect all non-empty writes into a single batchUpdate
        batch_data = []
        written = []
        for field, value in field_values.items():
            if not value:
                continue
            col_letter = self._info_cols[field]
            batch_data.append({
                "range": f"{tab}!{col_letter}{row_index}",
                "values": [[value]],
            })
            written.append(field)

        try:
            service = self._get_service()

            if batch_data:
                service.spreadsheets().values().batchUpdate(
                    spreadsheetId=self._spreadsheet_id,
                    body={"valueInputOption": "USER_ENTERED", "data": batch_data},
                ).execute()

            # Set Date Added only if currently blank
            existing = service.spreadsheets().values().get(
                spreadsheetId=self._spreadsheet_id,
                range=f"{tab}!{self._date_added_col}{row_index}",
            ).execute()
            if not existing.get("values"):
                service.spreadsheets().values().update(
                    spreadsheetId=self._spreadsheet_id,
                    range=f"{tab}!{self._date_added_col}{row_index}",
                    valueInputOption="USER_ENTERED",
                    body={"values": [[date.today().isoformat()]]},
                ).execute()
                written.append("date_added")

        except Exception as exc:
            return f"[sheets_update_info error] Row {row_index}: {exc}"

        if written:
            return f"Row {row_index}: updated {', '.join(written)}"
        return f"Row {row_index}: nothing to update (all provided values were empty)"
