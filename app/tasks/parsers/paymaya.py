"""
tasks/parsers/paymaya.py — PayMaya / Maya bank statement parser.

TODO: Implement this parser.
"""

from tasks.parsers.base import StubParser


class PaymayaParser(StubParser):
    def __init__(self):
        super().__init__("paymaya")

    def supported_extensions(self) -> list[str]:
        return []
