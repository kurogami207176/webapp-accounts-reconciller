"""
tasks/parsers/aub.py — AUB (Asia United Bank) statement parser.

TODO: Implement this parser.
"""

from tasks.parsers.base import StubParser


class AubParser(StubParser):
    def __init__(self):
        super().__init__("aub")

    def supported_extensions(self) -> list[str]:
        return []
