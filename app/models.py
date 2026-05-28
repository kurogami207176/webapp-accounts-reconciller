"""
models.py — Data classes for the reconciler domain objects.

These are plain Python dataclasses used for passing data between layers.
They are NOT SQLAlchemy models — the app uses raw psycopg3 SQL.

Schema mirrors production (Neon/Replit) as of 2026-05-28.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal
from typing import Optional
import uuid


@dataclass
class User:
    """An application user."""
    email: str
    role: str                   # e.g. 'admin', 'branch'
    group_id: str

    # Set by DB
    id: str = field(default_factory=lambda: str(uuid.uuid4()))


@dataclass
class Submission:
    """A branch user receipt upload (GCash online transaction)."""
    branch_email: str
    date: date
    time: time
    amount: Decimal
    staff_name: str
    image_url: str
    source: str = "gcash"       # 'gcash' | 'paymaya' | ...

    # Optional / set after creation
    status: str = "UNMATCHED"
    group_id: Optional[str] = None
    invalid_reason: Optional[str] = None

    # Set by DB
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    record_created_timestamp_utc: Optional[datetime] = None
    record_updated_timestamp_utc: Optional[datetime] = None


@dataclass
class BankDeposit:
    """A bank deposit slip upload from a branch."""
    branch_email: str
    date: date
    time: time
    amount: Decimal
    staff_name: str
    image_url: str
    bank_type: str              # e.g. 'BDO', 'BPI', 'AUB'
    group_id: str

    status: str = "UNMATCHED"
    reference_number: Optional[str] = None
    for_day: Optional[date] = None

    # Set by DB
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    record_created_timestamp_utc: Optional[datetime] = None
    record_updated_timestamp_utc: Optional[datetime] = None


@dataclass
class StatementUpload:
    """An admin-uploaded bank statement file."""
    group_id: str
    uploaded_by_email: str
    file_type: str              # 'gcash' | 'paymaya' | 'aub' | 'bdo'
    file_path: str
    original_filename: str

    password: Optional[str] = None
    status: str = "PENDING"    # 'PENDING' | 'PARSED' | 'INSERTED' | 'FAILED'
    error_message: Optional[str] = None
    transactions_count: Optional[int] = None
    matching_result: Optional[str] = None
    parsed_count: Optional[int] = None
    skipped_count: Optional[int] = None
    min_date: Optional[date] = None
    max_date: Optional[date] = None
    comment: Optional[str] = None
    raw_file_path: Optional[str] = None
    text_file_path: Optional[str] = None

    # Set by DB
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


@dataclass
class Transaction:
    """A single transaction parsed from a bank statement file."""
    transaction_id: str
    transaction_timestamp: datetime
    transaction_type: str
    amount: Decimal
    balance: Decimal
    source: str                 # 'gcash' | 'paymaya' | 'aub' | 'bdo'

    currency: Optional[str] = None
    description: Optional[str] = None
    group_id: Optional[str] = None
    matching_submission_id: Optional[str] = None
    matching_bank_deposit_id: Optional[str] = None
    record_created_timestamp_utc: Optional[datetime] = None

    # Set by DB
    id: str = field(default_factory=lambda: str(uuid.uuid4()))


@dataclass
class TransactionSource:
    """Maps a transaction back to its source statement file and line range."""
    transaction_id: str
    statement_upload_id: str
    group_id: str

    raw_file_path: Optional[str] = None
    text_file_path: Optional[str] = None
    start_line: Optional[int] = None
    end_line: Optional[int] = None

    # Set by DB
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: Optional[datetime] = None


@dataclass
class Summary:
    """A daily reconciliation summary file record."""
    branch_email: str
    summary_date: date
    summary_type: str           # e.g. 'daily', 'weekly'
    file_url: str

    group_id: Optional[str] = None

    # Set by DB
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    record_created_timestamp_utc: Optional[datetime] = None


# ── Allowed values ──────────────────────────────────────────────────────────

ONLINE_SOURCES = ("gcash", "paymaya")
BANK_DEPOSIT_TYPES = ("aub", "bdo", "bpi")
ALL_SOURCES = ("gcash", "paymaya", "aub", "bdo", "bpi")

STATEMENT_TYPES = ("gcash", "paymaya", "aub", "bdo")

SUBMISSION_STATUSES = ("UNMATCHED", "MATCHED", "INVALID")
BANK_DEPOSIT_STATUSES = ("UNMATCHED", "MATCHED")
STATEMENT_UPLOAD_STATUSES = ("PENDING", "PARSED", "INSERTED", "FAILED")
