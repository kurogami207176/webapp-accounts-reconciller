"""
tasks/parsers/gcash.py — GCash bank statement parser.

TODO: Implement this parser.

GCash statements are typically exported as PDF or CSV from the GCash app
or GCash for Business portal.

Implementation checklist:
  1. Detect file type (PDF vs CSV) from magic bytes
  2. If PDF and password provided, decrypt first
  3. Extract rows: date, ref_no, description, type (credit/debit), amount, balance
  4. Map to ParsedTransaction fields and return
"""

from tasks.parsers.base import StubParser


class GcashParser(StubParser):
    """
    GCash statement parser — not yet implemented.
    Remove StubParser base and implement parse() when ready.
    """

    def __init__(self):
        super().__init__("gcash")

    def supported_extensions(self) -> list[str]:
        # Update when implemented: e.g. ["pdf", "csv"]
        return []
