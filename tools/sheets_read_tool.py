"""Google Sheets read tool for immigration lead agents.

Returns all company rows from the lead-tracking spreadsheet so
downstream agents can process them.

Immigration sheet columns (A:X):
  0  A  Company Name
  1  B  Comment LawFairy
  2  C  Rating
  3  D  Notes
  4  E  Website
  5  F  LinkedIn
  6  G  Size
  7  H  HQ Location
  8  I  Date Added
  9  J  CorporateImmigration Signal
  ...
"""

import json
from typing import Any

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

from nanobot.agent.tools.base import Tool

SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]

_COL_NAME    = 0  # A
_COL_NOTES   = 3  # D
_COL_WEBSITE = 4  # E
_COL_LINKEDIN= 5  # F
_COL_SIZE    = 6  # G
_COL_HQ      = 7  # H


class SheetsReadTool(Tool):
    """Read all company rows from the immigration leads Google Sheet."""

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
        return "sheets_read_companies"

    @property
    def description(self) -> str:
        return (
            "Read all companies currently stored in the Google Sheets lead tracker. "
            "Returns a JSON array of objects with: row_index, company_name, notes, "
            "website, linkedin, size, hq_location. Use this to get the list of "
            "companies to process."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}, "required": []}

    async def execute(self, **kwargs: Any) -> str:
        try:
            service = self._get_service()
            result = (
                service.spreadsheets()
                .values()
                .get(
                    spreadsheetId=self._spreadsheet_id,
                    range=f"{self._sheet_name}!A:H",
                )
                .execute()
            )
        except Exception as exc:
            return f"[sheets_read error] Failed to read tab '{self._sheet_name}': {exc}"

        rows = result.get("values", [])

        if len(rows) <= 1:
            return "# 0 companies in sheet\n[]"

        def cell(row: list, idx: int) -> str:
            return row[idx].strip() if idx < len(row) else ""

        companies = []
        for i, row in enumerate(rows[1:], start=2):
            companies.append({
                "row_index":    i,
                "company_name": cell(row, _COL_NAME),
                "notes":        cell(row, _COL_NOTES),
                "website":      cell(row, _COL_WEBSITE),
                "linkedin":     cell(row, _COL_LINKEDIN),
                "size":         cell(row, _COL_SIZE),
                "hq_location":  cell(row, _COL_HQ),
            })

        needs_enrichment = sum(1 for c in companies if not c["notes"])
        missing_website  = sum(1 for c in companies if not c["website"])
        summary = (
            f"# {len(companies)} companies in sheet\n"
            f"# {needs_enrichment} missing notes, {missing_website} missing website\n"
        )
        return summary + json.dumps(companies, ensure_ascii=False, separators=(",", ":"))
