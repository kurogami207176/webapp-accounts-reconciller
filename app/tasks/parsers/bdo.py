"""
tasks/parsers/bdo.py — BDO (Banco de Oro) statement parser.

TODO: Implement this parser.
"""

from tasks.parsers.base import StubParser


class BdoParser(StubParser):
    def __init__(self):
        super().__init__("bdo")

    def supported_extensions(self) -> list[str]:
        return []
