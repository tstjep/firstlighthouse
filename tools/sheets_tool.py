"""Google Sheets append tool for nanobot agents.

Appends company rows to an immigration leads tab in the lead-tracking spreadsheet.
Only fills the Company Info columns (A-H); further columns reserved for later agents.

Sheet columns (0-based, A:S = 19 cols):
  0  A  Company Name
  1  B  Comment LawFairy   (internal, left blank by agent)
  2  C  Rating             (written by rating agent)
  3  D  Notes
  4  E  Website
  5  F  LinkedIn
  6  G  Size
  7  H  HQ Location
  8  I  Date Added
  9  J  CorporateImmigration Signal  (Yes/No — filled by signal agent)
  10 K  CorporateImmigration Source
  11 L  TechForward Signal
  12 M  TechForward Source
  13 N  MultiVisa Signal
  14 O  MultiVisa Source
  15 P  HighVolume Signal
  16 Q  HighVolume Source
  17 R  Growth Signal
  18 S  Growth Source
"""

from datetime import date
from typing import Any

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

from nanobot.agent.tools.base import Tool

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


class SheetsAppendTool(Tool):
    """Append a company row to the immigration leads Google Sheet."""

    def __init__(
        self,
        spreadsheet_id: str,
        credentials_file: str,
        sheet_name: str = "LawFirms",
        existing_names: set[str] | None = None,
        existing_domains: set[str] | None = None,
    ):
        self._spreadsheet_id = spreadsheet_id
        self._credentials_file = credentials_file
        self._sheet_name = sheet_name
        self._service = None
        self._existing_names: set[str] = set(existing_names or [])
        self._existing_domains: set[str] = set(existing_domains or [])

    def _get_service(self):
        if self._service is None:
            from pathlib import Path
            creds_path = Path(self._credentials_file)
            if not creds_path.exists():
                raise FileNotFoundError(
                    f"[sheets_append] Credentials file not found: {self._credentials_file}\n"
                    "  Check CREDENTIALS_FILE in config.py"
                )
            creds = Credentials.from_service_account_file(
                self._credentials_file, scopes=SCOPES
            )
            self._service = build("sheets", "v4", credentials=creds)
        return self._service

    @property
    def name(self) -> str:
        return "sheets_append_company"

    @property
    def description(self) -> str:
        return (
            "Append a discovered UK immigration company to the Google Sheets lead tracker. "
            "Fills company name, notes, website, LinkedIn URL, size, and HQ location. "
            "Call this once per unique company found."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "company_name": {
                    "type": "string",
                    "description": "Legal or trading name of the company",
                    "minLength": 1,
                },
                "website": {
                    "type": "string",
                    "description": "Primary website URL (e.g. https://example.com)",
                    "minLength": 4,
                },
                "linkedin": {
                    "type": "string",
                    "description": "LinkedIn company page URL, or empty string if unknown",
                },
                "size": {
                    "type": "string",
                    "description": "Employee count range, e.g. '1-10', '11-50', '51-200'",
                },
                "hq_location": {
                    "type": "string",
                    "description": "Headquarters location, e.g. 'London, UK'",
                },
                "notes": {
                    "type": "string",
                    "description": "Brief description of the company and its immigration services",
                },
            },
            "required": ["company_name", "website"],
        }

    @staticmethod
    def _domain_key(url: str) -> str:
        d = url.strip().lower()
        for prefix in ("https://", "http://", "www."):
            d = d.removeprefix(prefix)
        return d.rstrip("/").split("/")[0]

    async def execute(
        self,
        company_name: str,
        website: str,
        linkedin: str = "",
        size: str = "",
        hq_location: str = "",
        notes: str = "",
        **kwargs: Any,
    ) -> str:
        name_key = company_name.strip().lower()
        domain_key = self._domain_key(website) if website else ""
        if name_key and name_key in self._existing_names:
            return f"Already in sheet: {company_name} — skipped"
        if domain_key and domain_key in self._existing_domains:
            return f"Already in sheet: {company_name} ({website}) — skipped"

        row = [
            company_name,              # A – Company Name
            "",                        # B – Comment LawFairy
            "",                        # C – Rating
            notes,                     # D – Notes
            website,                   # E – Website
            linkedin,                  # F – LinkedIn
            size,                      # G – Size
            hq_location,               # H – HQ Location
            date.today().isoformat(),  # I – Date Added
            "", "",                    # J–K  CorporateImmigration signal / source
            "", "",                    # L–M  TechForward signal / source
            "", "",                    # N–O  MultiVisa signal / source
            "", "",                    # P–Q  HighVolume signal / source
            "", "",                    # R–S  Growth signal / source
        ]

        try:
            service = self._get_service()
            service.spreadsheets().values().append(
                spreadsheetId=self._spreadsheet_id,
                range=f"{self._sheet_name}!A:S",
                valueInputOption="USER_ENTERED",
                insertDataOption="OVERWRITE",
                body={"values": [row]},
            ).execute()
        except Exception as exc:
            return f"[sheets_append error] Failed to append {company_name!r}: {exc}"

        if name_key:
            self._existing_names.add(name_key)
        if domain_key:
            self._existing_domains.add(domain_key)

        return f"Added to sheet: {company_name} ({website})"
