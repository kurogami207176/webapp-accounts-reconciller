"""initial_schema

Revision ID: 001
Revises:
Create Date: 2026-05-28

Creates all tables matching the production schema (Neon/Replit):
  - users               — application users with role + group
  - submissions         — branch user receipt uploads (GCash / bank deposit)
  - bank_deposits       — bank deposit records
  - statement_uploads   — admin bank statement file uploads
  - transactions        — parsed bank statement transactions
  - transaction_sources — maps transactions back to their source file + line range
  - summaries           — summary file records per branch per day
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── users ────────────────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE users (
            id          VARCHAR(36)     PRIMARY KEY,
            email       VARCHAR(255)    NOT NULL UNIQUE,
            role        VARCHAR(50)     NOT NULL,
            group_id    VARCHAR(36)     NOT NULL
        )
    """)

    # ── submissions ───────────────────────────────────────────────────────────
    # Branch user receipt uploads (GCash online transactions).
    op.execute("""
        CREATE TABLE submissions (
            id                              VARCHAR(36)     PRIMARY KEY,
            branch_email                    VARCHAR(255)    NOT NULL,
            date                            DATE            NOT NULL,
            time                            TIME            NOT NULL,
            amount                          NUMERIC         NOT NULL,
            staff_name                      VARCHAR(255)    NOT NULL,
            image_url                       VARCHAR(500)    NOT NULL,
            status                          VARCHAR(20)     NOT NULL,
            record_created_timestamp_utc    TIMESTAMPTZ,
            record_updated_timestamp_utc    TIMESTAMPTZ,
            invalid_reason                  TEXT,
            group_id                        VARCHAR(36),
            source                          VARCHAR(50)     NOT NULL DEFAULT 'gcash'
        )
    """)
    op.execute("CREATE INDEX idx_submissions_matching ON submissions (group_id, status, source, date)")

    # ── bank_deposits ─────────────────────────────────────────────────────────
    # Bank deposit slip uploads from branches.
    op.execute("""
        CREATE TABLE bank_deposits (
            id                              VARCHAR(36)     PRIMARY KEY,
            branch_email                    VARCHAR(255)    NOT NULL,
            date                            DATE            NOT NULL,
            time                            TIME            NOT NULL,
            amount                          NUMERIC         NOT NULL,
            staff_name                      VARCHAR(255)    NOT NULL,
            image_url                       VARCHAR(500)    NOT NULL,
            bank_type                       VARCHAR(20)     NOT NULL,
            status                          VARCHAR(20)     NOT NULL,
            group_id                        VARCHAR(36)     NOT NULL,
            record_created_timestamp_utc    TIMESTAMPTZ,
            record_updated_timestamp_utc    TIMESTAMPTZ,
            reference_number                VARCHAR(100),
            for_day                         DATE
        )
    """)
    op.execute("CREATE INDEX idx_bank_deposits_branch_email ON bank_deposits (branch_email)")
    op.execute("CREATE INDEX idx_bank_deposits_date         ON bank_deposits (date)")
    op.execute("CREATE INDEX idx_bank_deposits_for_day      ON bank_deposits (for_day)")
    op.execute("CREATE INDEX idx_bank_deposits_group_date   ON bank_deposits (group_id, date)")
    op.execute("CREATE INDEX idx_bank_deposits_group_forday ON bank_deposits (group_id, for_day)")
    op.execute("CREATE INDEX idx_bank_deposits_group_id     ON bank_deposits (group_id)")
    op.execute("CREATE INDEX idx_bank_deposits_status       ON bank_deposits (status)")

    # ── statement_uploads ─────────────────────────────────────────────────────
    # Admin-uploaded bank statement files (PDF/CSV/XLS).
    op.execute("""
        CREATE TABLE statement_uploads (
            id                  VARCHAR(36)     PRIMARY KEY,
            group_id            VARCHAR(36)     NOT NULL,
            uploaded_by_email   VARCHAR(255)    NOT NULL,
            file_type           VARCHAR(20)     NOT NULL,
            file_path           VARCHAR(500)    NOT NULL,
            original_filename   VARCHAR(255)    NOT NULL,
            password            VARCHAR(255),
            status              VARCHAR(20)     NOT NULL,
            error_message       TEXT,
            transactions_count  INTEGER,
            matching_result     TEXT,
            created_at          TIMESTAMPTZ,
            updated_at          TIMESTAMPTZ,
            parsed_count        INTEGER,
            skipped_count       INTEGER,
            min_date            DATE,
            max_date            DATE,
            comment             TEXT,
            raw_file_path       VARCHAR(500),
            text_file_path      VARCHAR(500)
        )
    """)

    # ── transactions ──────────────────────────────────────────────────────────
    # Individual transactions parsed from bank statement files.
    op.execute("""
        CREATE TABLE transactions (
            id                              VARCHAR(36)     PRIMARY KEY,
            transaction_id                  VARCHAR(255)    NOT NULL,
            transaction_timestamp           TIMESTAMPTZ     NOT NULL,
            transaction_type                VARCHAR(10)     NOT NULL,
            amount                          NUMERIC         NOT NULL,
            currency                        VARCHAR(10),
            description                     TEXT,
            balance                         NUMERIC         NOT NULL,
            matching_submission_id          VARCHAR(36)     REFERENCES submissions (id),
            record_created_timestamp_utc    TIMESTAMPTZ,
            group_id                        VARCHAR(36),
            source                          VARCHAR(50)     NOT NULL DEFAULT 'gcash',
            matching_bank_deposit_id        VARCHAR(36)     REFERENCES bank_deposits (id),
            UNIQUE (source, transaction_id)
        )
    """)
    op.execute("CREATE INDEX idx_transactions_group_id                  ON transactions (group_id)")
    op.execute("CREATE INDEX idx_transactions_group_matching_deposit     ON transactions (group_id, matching_bank_deposit_id)")
    op.execute("CREATE INDEX idx_transactions_group_matching_submission  ON transactions (group_id, matching_submission_id)")
    op.execute("CREATE INDEX idx_transactions_group_source_ts            ON transactions (group_id, source, transaction_timestamp)")
    op.execute("CREATE INDEX idx_transactions_matching                   ON transactions (group_id, transaction_type, matching_submission_id)")

    # ── transaction_sources ───────────────────────────────────────────────────
    # Maps each transaction back to its source statement file + line range.
    op.execute("""
        CREATE TABLE transaction_sources (
            id                      VARCHAR(36)     PRIMARY KEY,
            transaction_id          VARCHAR(36)     NOT NULL REFERENCES transactions (id),
            statement_upload_id     VARCHAR(36)     NOT NULL REFERENCES statement_uploads (id),
            raw_file_path           VARCHAR(500),
            text_file_path          VARCHAR(500),
            start_line              INTEGER,
            end_line                INTEGER,
            group_id                VARCHAR(36)     NOT NULL,
            created_at              TIMESTAMPTZ
        )
    """)

    # ── summaries ─────────────────────────────────────────────────────────────
    # Summary file records (daily reconciliation reports per branch).
    op.execute("""
        CREATE TABLE summaries (
            id                              VARCHAR(36)     PRIMARY KEY,
            branch_email                    VARCHAR(255)    NOT NULL,
            summary_date                    DATE            NOT NULL,
            summary_type                    VARCHAR(20)     NOT NULL,
            file_url                        VARCHAR(500)    NOT NULL,
            record_created_timestamp_utc    TIMESTAMPTZ,
            group_id                        VARCHAR(36)
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS transaction_sources")
    op.execute("DROP TABLE IF EXISTS transactions")
    op.execute("DROP TABLE IF EXISTS statement_uploads")
    op.execute("DROP TABLE IF EXISTS bank_deposits")
    op.execute("DROP TABLE IF EXISTS summaries")
    op.execute("DROP TABLE IF EXISTS submissions")
    op.execute("DROP TABLE IF EXISTS users")
