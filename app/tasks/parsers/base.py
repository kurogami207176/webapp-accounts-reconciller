"""
tasks/parsers/base.py — Abstract base class for bank statement parsers.

Each parser handles one statement_type (gcash, paymaya, aub, bdo).
The file may be a PDF, CSV, XLS, or any format depending on the bank.

To add a new parser:
  1. Create app/tasks/parsers/<bank>.py
  2. Subclass BaseParser and implement parse()
  3. Register it in PARSER_REGISTRY below

Usage (by tasks/parser.py):
    from tasks.parsers.base import get_parser
    parser = get_parser("gcash")
    transactions = parser.parse(file_bytes, password="optional")
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class ParsedTransaction:
    """
    A single transaction extracted from a bank statement.
    This is the raw output of a parser before DB insertion.
    """
    transaction_id: str           # Bank's own reference number
    transaction_timestamp: datetime
    transaction_type: str         # e.g. 'credit', 'debit', 'transfer'
    amount: Decimal               # Always positive; transaction_type indicates direction
    balance: Decimal              # Running balance after this transaction
    description: str

    currency: str = "PHP"
    group_id: Optional[str] = None
    record_created_timestamp_utc: Optional[datetime] = None


class BaseParser(ABC):
    """
    Abstract base class for bank statement parsers.

    Subclasses must implement:
      - parse(file_bytes, password) → list[ParsedTransaction]

    They may optionally implement:
      - supported_extensions() → list of file extensions this parser handles
    """

    @abstractmethod
    def parse(self, file_bytes: bytes, password: Optional[str] = None) -> list[ParsedTransaction]:
        """
        Parse the raw file bytes and return a list of transactions.

        Args:
            file_bytes: Raw file content (PDF, CSV, etc.)
            password:   Optional decryption password for encrypted PDFs

        Returns:
            List of ParsedTransaction instances

        Raises:
            NotImplementedError: If not implemented by subclass
            ValueError: If the file format is not recognised
            Exception: Any parsing error; will be caught and stored as parse_error
        """
        ...

    def supported_extensions(self) -> list[str]:
        """Return the file extensions this parser supports (e.g. ['pdf', 'csv'])."""
        return []

    @classmethod
    def source_name(cls) -> str:
        """Return the statement_type string this parser handles."""
        return cls.__name__.lower().replace("parser", "")


class StubParser(BaseParser):
    """
    Placeholder parser for statement types not yet implemented.
    Raises NotImplementedError so the task sets status=FAILED with a clear message.
    """

    def __init__(self, source: str):
        self._source = source

    def parse(self, file_bytes: bytes, password: Optional[str] = None) -> list[ParsedTransaction]:
        raise NotImplementedError(
            f"Parser for '{self._source}' is not yet implemented. "
            "Upload the file manually or implement the parser in "
            f"app/tasks/parsers/{self._source}.py"
        )


# ---------------------------------------------------------------------------
# Parser registry — maps statement_type → parser instance
# ---------------------------------------------------------------------------
# Import concrete parsers here as they are implemented.
# Each entry is a zero-argument callable that returns a BaseParser instance.

def _load_parsers() -> dict[str, BaseParser]:
    registry: dict[str, BaseParser] = {}

    # Attempt to import concrete parsers; fall back to StubParser if not ready.
    for source in ("gcash", "paymaya", "aub", "bdo"):
        try:
            module = __import__(
                f"tasks.parsers.{source}", fromlist=[f"{source.capitalize()}Parser"]
            )
            cls = getattr(module, f"{source.capitalize()}Parser")
            registry[source] = cls()
            logger.debug("Loaded parser: %s", source)
        except (ImportError, AttributeError):
            registry[source] = StubParser(source)
            logger.debug("Using stub parser for: %s", source)

    return registry


_PARSER_REGISTRY: dict[str, BaseParser] | None = None


def get_parser(statement_type: str) -> BaseParser:
    """Return the parser for the given statement_type."""
    global _PARSER_REGISTRY
    if _PARSER_REGISTRY is None:
        _PARSER_REGISTRY = _load_parsers()
    parser = _PARSER_REGISTRY.get(statement_type)
    if parser is None:
        raise ValueError(f"No parser registered for statement_type='{statement_type}'")
    return parser
