"""
models.py — Data classes for the reconciler domain objects.

These are plain Python dataclasses used for passing data between layers.
They are NOT SQLAlchemy models — the app uses raw psycopg3 SQL.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal
from typing import Optional
import uuid


@dataclass
class Submission:
    """A receipt upload from a branch user."""
    group_id: str
    upload_type: str          # 'online_transaction' | 'bank_deposit'
    payment_type: str         # 'gcash' | 'paymaya' | 'aub' | 'bdo' | 'bpi'
    transaction_date: date
    transaction_time: time
    amount: Decimal
    staff_name: str
    photo_s3_key: str
    info_s3_key: str
    submitted_by: str         # Cognito sub

    # Bank deposit only
    reference_number: Optional[str] = None
    for_day: Optional[date] = None

    # Set by DB
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    status: str = "UNMATCHED"
    created_at: Optional[datetime] = None


@dataclass
class BankStatementFile:
    """An admin-uploaded bank statement file."""
    statement_type: str       # 'gcash' | 'paymaya' | 'aub' | 'bdo'
    file_s3_key: str
    info_s3_key: str
    uploaded_by: str          # Cognito sub

    file_password: Optional[str] = None
    comment: Optional[str] = None

    # Set by DB
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    status: str = "PENDING"
    created_at: Optional[datetime] = None
    parsed_at: Optional[datetime] = None
    inserted_at: Optional[datetime] = None
    parse_error: Optional[str] = None


@dataclass
class BankTransaction:
    """A single transaction parsed from a bank statement file."""
    transaction_id: str
    transaction_timestamp: datetime
    transaction_type: str
    amount: Decimal
    balance: Decimal
    source: str               # 'gcash' | 'paymaya' | 'aub' | 'bdo'
    bank_statement_file_id: str

    currency: Optional[str] = None
    description: Optional[str] = None
    group_id: Optional[str] = None
    record_created_timestamp_utc: Optional[datetime] = None
    matching_submission_id: Optional[str] = None
    matching_bank_deposit_id: Optional[str] = None

    # Set by DB
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: Optional[datetime] = None


# ── Allowed values ──────────────────────────────────────────────────────────

ONLINE_PAYMENT_TYPES = ("gcash", "paymaya", "aub")
BANK_DEPOSIT_TYPES = ("aub", "bdo", "bpi")
ALL_PAYMENT_TYPES = tuple(dict.fromkeys(ONLINE_PAYMENT_TYPES + BANK_DEPOSIT_TYPES))

STATEMENT_TYPES = ("gcash", "paymaya", "aub", "bdo")

UPLOAD_TYPES = ("online_transaction", "bank_deposit")
STATEMENT_UPLOAD_TYPE = "bank_statement"
