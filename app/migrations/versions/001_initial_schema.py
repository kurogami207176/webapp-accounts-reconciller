"""initial_schema

Revision ID: 001
Revises:
Create Date: 2026-05-25

Creates the three core tables for the accounts reconciliation app:
  - submissions            — branch user receipt uploads
  - bank_statement_files   — admin bank statement uploads
  - bank_transactions      — parsed transactions from bank statements
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── submissions ─────────────────────────────────────────────────────────────
    # Stores receipt uploads from branch users.
    # upload_type:  'online_transaction' | 'bank_deposit'
    # payment_type: 'gcash' | 'paymaya' | 'aub' | 'bdo' | 'bpi'
    # status:       'UNMATCHED' | 'MATCHED'
    op.execute("""
        CREATE TABLE submissions (
            id                  UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
            group_id            VARCHAR(100)    NOT NULL,
            upload_type         VARCHAR(30)     NOT NULL
                                    CHECK (upload_type IN ('online_transaction', 'bank_deposit')),
            payment_type        VARCHAR(20)     NOT NULL
                                    CHECK (payment_type IN ('gcash', 'paymaya', 'aub', 'bdo', 'bpi')),
            transaction_date    DATE            NOT NULL,
            transaction_time    TIME            NOT NULL,
            amount              NUMERIC(10, 2)  NOT NULL,
            staff_name          VARCHAR(255)    NOT NULL,
            reference_number    VARCHAR(255),
            for_day             DATE,
            photo_s3_key        TEXT            NOT NULL,
            info_s3_key         TEXT            NOT NULL,
            status              VARCHAR(20)     NOT NULL DEFAULT 'UNMATCHED'
                                    CHECK (status IN ('UNMATCHED', 'MATCHED')),
            submitted_by        VARCHAR(255)    NOT NULL,
            created_at          TIMESTAMPTZ     NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX idx_submissions_group_id    ON submissions (group_id)")
    op.execute("CREATE INDEX idx_submissions_status      ON submissions (status)")
    op.execute("CREATE INDEX idx_submissions_date        ON submissions (transaction_date)")
    op.execute("CREATE INDEX idx_submissions_type_amount ON submissions (upload_type, payment_type, amount)")

    # ── bank_statement_files ─────────────────────────────────────────────────────
    # Stores admin-uploaded bank statement files.
    # statement_type: 'gcash' | 'paymaya' | 'aub' | 'bdo'
    # status:         'PENDING' | 'PARSED' | 'INSERTED' | 'FAILED'
    op.execute("""
        CREATE TABLE bank_statement_files (
            id              UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
            statement_type  VARCHAR(20)     NOT NULL
                                CHECK (statement_type IN ('gcash', 'paymaya', 'aub', 'bdo')),
            file_s3_key     TEXT            NOT NULL,
            info_s3_key     TEXT            NOT NULL,
            file_password   VARCHAR(255),
            comment         TEXT,
            status          VARCHAR(20)     NOT NULL DEFAULT 'PENDING'
                                CHECK (status IN ('PENDING', 'PARSED', 'INSERTED', 'FAILED')),
            uploaded_by     VARCHAR(255)    NOT NULL,
            created_at      TIMESTAMPTZ     NOT NULL DEFAULT now(),
            parsed_at       TIMESTAMPTZ,
            inserted_at     TIMESTAMPTZ,
            parse_error     TEXT
        )
    """)
    op.execute("CREATE INDEX idx_bsf_status         ON bank_statement_files (status)")
    op.execute("CREATE INDEX idx_bsf_statement_type ON bank_statement_files (statement_type)")
    op.execute("CREATE INDEX idx_bsf_created_at     ON bank_statement_files (created_at)")

    # ── bank_transactions ────────────────────────────────────────────────────────
    # Stores individual transactions parsed from bank statement files.
    # source: 'gcash' | 'paymaya' | 'aub' | 'bdo'
    op.execute("""
        CREATE TABLE bank_transactions (
            id                              VARCHAR(36)     PRIMARY KEY,
            transaction_id                  VARCHAR(255)    NOT NULL,
            transaction_timestamp           TIMESTAMPTZ     NOT NULL,
            transaction_type                VARCHAR(10)     NOT NULL,
            amount                          NUMERIC(10, 2)  NOT NULL,
            currency                        VARCHAR(10),
            description                     TEXT,
            balance                         NUMERIC(10, 2)  NOT NULL,
            matching_submission_id          VARCHAR(36),
            record_created_timestamp_utc    TIMESTAMPTZ,
            group_id                        VARCHAR(36),
            source                          VARCHAR(50)     NOT NULL DEFAULT 'gcash',
            matching_bank_deposit_id        VARCHAR(36),
            bank_statement_file_id          UUID            NOT NULL
                                                REFERENCES bank_statement_files (id),
            created_at                      TIMESTAMPTZ     NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX idx_bt_file_id       ON bank_transactions (bank_statement_file_id)")
    op.execute("CREATE INDEX idx_bt_timestamp     ON bank_transactions (transaction_timestamp)")
    op.execute("CREATE INDEX idx_bt_source_amount ON bank_transactions (source, amount)")
    op.execute("CREATE INDEX idx_bt_matching      ON bank_transactions (matching_submission_id)")
    op.execute("CREATE INDEX idx_bt_group_id      ON bank_transactions (group_id)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS bank_transactions")
    op.execute("DROP TABLE IF EXISTS bank_statement_files")
    op.execute("DROP TABLE IF EXISTS submissions")
