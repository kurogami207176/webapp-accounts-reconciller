# webapp-accounts-reconciller

A web application for reconciling branch cash transactions against bank statements. Branch users upload payment receipts; admins upload bank statements that are automatically parsed and matched against those receipts.

---

## Table of Contents

- [Overview](#overview)
- [User Roles](#user-roles)
- [Branch User Features](#branch-user-features)
- [Admin Features](#admin-features)
- [Data Model](#data-model)
- [S3 Storage Layout](#s3-storage-layout)
- [Background Processing](#background-processing)
- [Matching Logic](#matching-logic)
- [Audit Reports](#audit-reports)
- [Architecture](#architecture)
- [Local Development](#local-development)
- [Deployment](#deployment)
- [Infrastructure Stacks](#infrastructure-stacks)
- [Adding a New Bank Parser](#adding-a-new-bank-parser)

---

## Overview

```
Branch User
  → Uploads receipt photo + transaction info
  → Stored in S3 (photo + JSON) and PostgreSQL (status=UNMATCHED)

Admin
  → Uploads bank statement (PDF/CSV)
  → Stored in S3, status=PENDING in DB
  → Parser ECS task triggered automatically:
      downloads file → extracts transactions → inserts to DB (status=INSERTED)
  → Matcher ECS task triggered (manually or automatically):
      matches bank transactions ↔ branch submissions by date/time/amount/type
      matched rows → status=MATCHED
  → Dashboard shows status of all statements + audit reports
```

---

## User Roles

Roles are managed via **AWS Cognito Groups** — no separate user management table is required.

| Cognito Group | Role | Access |
|---------------|------|--------|
| `admins` | Admin | Full access: upload statements, view dashboard, run reports |
| `branch-<name>` | Branch User | Upload receipts, view own submission history |

- A user's **branch group name** (e.g. `branch-manila`) doubles as their **group_id** stored on every submission.
- Users with no recognised group are denied access to feature routes.
- Assign users to groups via the AWS Cognito Console or CLI.

---

## Branch User Features

### Online Transaction Upload (`/branch/upload/online`)

For GCash, PayMaya, and AUB online payments.

| Field | Required | Notes |
|-------|----------|-------|
| Transaction Date | ✓ | Date of the payment |
| Transaction Time | ✓ | Time of the payment |
| Amount (PHP) | ✓ | Positive decimal |
| Payment Type | ✓ | `gcash`, `paymaya`, `aub` |
| Staff Name | ✓ | Name of the staff member who made the payment |
| Receipt Photo | ✓ | JPG, PNG, HEIC, or PDF — max 10 MB |

### Bank Deposit Upload (`/branch/upload/deposit`)

For AUB, BDO, and BPI cash/check deposits.

| Field | Required | Notes |
|-------|----------|-------|
| Deposit Date | ✓ | Date of the deposit |
| Deposit Time | ✓ | Time of the deposit |
| Amount (PHP) | ✓ | Positive decimal |
| Bank | ✓ | `aub`, `bdo`, `bpi` |
| Staff Name | ✓ | |
| Clearing Date (For Day) | ✓ | The value date / check clearing date |
| Reference / Slip Number | — | Optional bank slip reference |
| Deposit Slip Photo | ✓ | JPG, PNG, HEIC, or PDF — max 10 MB |

### Submission History (`/branch/history`)

Shows the last 100 submissions for the user's branch with status badges (`UNMATCHED` / `MATCHED`).

---

## Admin Features

### Upload Bank Statement (`/admin/upload/statement`)

| Field | Required | Notes |
|-------|----------|-------|
| Statement Type | ✓ | `gcash`, `paymaya`, `aub`, `bdo` |
| Statement File | ✓ | PDF, CSV, XLSX, XLS, or ZIP — max 50 MB |
| File Password | — | For password-protected PDFs |
| Comment | — | e.g. "GCash April 2026" |

Uploading a statement:
1. Saves the file to S3 and a metadata JSON to S3
2. Inserts a `bank_statement_files` row with `status=PENDING`
3. Automatically triggers the **parser ECS task**

### Dashboard (`/admin/dashboard`)

Lists all uploaded statements with:
- Status badge (`PENDING` → `PARSED` → `INSERTED` / `FAILED`)
- **Parse** button (available when status is `PENDING` or `FAILED`)
- **Match** button (available when status is `INSERTED`)
- Live status polling via JavaScript (updates every 10 s while a task is running)

### Statement Detail (`/admin/statement/<id>`)

Shows:
- File metadata and parse error (if any)
- Download link (pre-signed S3 URL)
- Full table of parsed transactions with match status
- Parse / Match trigger buttons

### Reports (`/admin/reports`)

Seven audit report sections — see [Audit Reports](#audit-reports).

---

## Data Model

### `submissions`

Branch user receipt uploads.

| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID PK | Auto-generated |
| `group_id` | VARCHAR(100) | Cognito group name (e.g. `branch-manila`) |
| `upload_type` | VARCHAR(30) | `online_transaction` or `bank_deposit` |
| `payment_type` | VARCHAR(20) | `gcash`, `paymaya`, `aub`, `bdo`, `bpi` |
| `transaction_date` | DATE | |
| `transaction_time` | TIME | |
| `amount` | NUMERIC(10,2) | |
| `staff_name` | VARCHAR(255) | |
| `reference_number` | VARCHAR(255) | Bank deposit only |
| `for_day` | DATE | Bank deposit only — clearing date |
| `photo_s3_key` | TEXT | S3 key of the uploaded photo |
| `info_s3_key` | TEXT | S3 key of the metadata JSON |
| `status` | VARCHAR(20) | `UNMATCHED` or `MATCHED` |
| `submitted_by` | VARCHAR(255) | Cognito `sub` (user ID) |
| `created_at` | TIMESTAMPTZ | |

### `bank_statement_files`

Admin-uploaded bank statement files.

| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID PK | |
| `statement_type` | VARCHAR(20) | `gcash`, `paymaya`, `aub`, `bdo` |
| `file_s3_key` | TEXT | S3 key of the statement file |
| `info_s3_key` | TEXT | S3 key of the metadata JSON |
| `file_password` | VARCHAR(255) | Optional PDF decryption password |
| `comment` | TEXT | |
| `status` | VARCHAR(20) | `PENDING` → `PARSED` → `INSERTED` / `FAILED` |
| `uploaded_by` | VARCHAR(255) | Cognito `sub` |
| `created_at` | TIMESTAMPTZ | |
| `parsed_at` | TIMESTAMPTZ | Set when parsing completes |
| `inserted_at` | TIMESTAMPTZ | Set when DB insertion completes |
| `parse_error` | TEXT | Error message if status is `FAILED` |

### `bank_transactions`

Individual transactions parsed from a bank statement.

| Column | Type | Notes |
|--------|------|-------|
| `id` | VARCHAR(36) PK | UUID |
| `transaction_id` | VARCHAR(255) | Bank's own reference number |
| `transaction_timestamp` | TIMESTAMPTZ | |
| `transaction_type` | VARCHAR(10) | e.g. `credit`, `debit` |
| `amount` | NUMERIC(10,2) | Always positive |
| `currency` | VARCHAR(10) | Default `PHP` |
| `description` | TEXT | |
| `balance` | NUMERIC(10,2) | Running balance |
| `matching_submission_id` | VARCHAR(36) | FK → `submissions.id` when matched |
| `record_created_timestamp_utc` | TIMESTAMPTZ | |
| `group_id` | VARCHAR(36) | Populated during matching |
| `source` | VARCHAR(50) | `gcash`, `paymaya`, `aub`, `bdo` |
| `matching_bank_deposit_id` | VARCHAR(36) | FK for deposit matching |
| `bank_statement_file_id` | UUID | FK → `bank_statement_files.id` |
| `created_at` | TIMESTAMPTZ | |

---

## S3 Storage Layout

All objects are private (IAM-only access). Pre-signed URLs are used for downloads.

```
s3://<bucket>/
│
├── uploads/                          ← raw files (photos, statement files)
│   └── group=<group_id>/
│       └── upload_type=<type>/
│           └── payment_type=<ptype>/
│               └── year=<YYYY>/month=<MM>/
│                   └── <uuid>.<ext>
│
├── info/                             ← metadata JSONs (Glue-crawled)
│   └── group=<group_id>/
│       └── upload_type=<type>/
│           └── payment_type=<ptype>/
│               └── year=<YYYY>/month=<MM>/
│                   └── <uuid>.json   (contains uploads/ S3 key)
│
├── transactions/                     ← parsed transactions per statement
│   └── group=<group_id>/
│       └── year=<YYYY>/month=<MM>/
│           └── <statement_file_id>.json
│
└── matches/                          ← match run results
    └── year=<YYYY>/month=<MM>/
        └── <run_id>.json
```

- `info/` is crawled by AWS Glue (daily + on-demand) → queryable via Athena
- `group_id` for admin uploads is `_admin`
- Partitions follow Hive convention for Glue/Athena compatibility

---

## Background Processing

Parser and matcher run as **ECS Fargate one-off tasks** using the same Docker image as the web service, with a `CMD` override. This keeps the container image unified and avoids a separate Lambda deployment.

### Status transitions

```
Upload
  └─ PENDING
       └─ Parser task starts
            ├─ [success] → PARSED → DB insert → INSERTED
            └─ [failure] → FAILED (parse_error stored in DB)

INSERTED
  └─ Matcher task
       └─ Updates matching_submission_id on transactions
          Updates submission status to MATCHED
```

### Triggering tasks

**Automatically:** Parser is triggered immediately after a statement upload via the admin blueprint.

**Manually from the UI:** Use the Parse / Match buttons on the dashboard or statement detail page.

**From the CLI (local or CI):**
```bash
# Parser
./bin/run-task.sh --task parser --file-id <uuid> --env staging

# Matcher
./bin/run-task.sh --task matcher --file-id <uuid> --env staging

# Locally (no ECS required)
cd app
DATABASE_URL=postgresql+psycopg://... S3_BUCKET_NAME=... \
  python -m tasks.parser --file-id <uuid>
```

**Local fallback:** When `ECS_CLUSTER_NAME` / `ECS_TASK_DEFINITION` env vars are not set, the API falls back to running the task in a background thread. This is safe for local development but not production.

---

## Matching Logic

The matcher runs an exact match on the following fields:

| Criteria | Match condition |
|----------|----------------|
| Date | `submission.transaction_date == transaction.transaction_timestamp.date()` |
| Time | `submission.transaction_time` within **±5 minutes** of `transaction.transaction_timestamp.time()` |
| Amount | `submission.amount == transaction.amount` (exact) |
| Type | `submission.payment_type == transaction.source` |
| Status | `submission.status == 'UNMATCHED'` |

Matching is **1:1, first-match-wins**, ordered by `submission.created_at ASC`.

Unmatched transactions and submissions remain in the database for audit.

---

## Audit Reports

Available at `/admin/reports`:

| # | Report | What it shows |
|---|--------|---------------|
| 1 | **Statement Upload Status** | Count of files per status (PENDING/PARSED/INSERTED/FAILED) |
| 2 | **Matched Transactions per Branch** | Count and total amount of matched submissions per branch |
| 3 | **Unmatched Transactions per Branch** | Count and total amount of unmatched submissions per branch |
| 4 | **Parse Failures** | Statements with status=FAILED, error messages, links to detail view |
| 5 | **Duplicate Submissions** | Same branch + date + amount + type appearing more than once (possible double-entry) |
| 6 | **Late Submissions** | Submissions uploaded more than 2 days after the transaction date |
| 7 | **Statement Coverage** | Date range of parsed transactions per statement (detect gaps in coverage) |

Additional audit angles to consider implementing:
- **Cross-branch duplicates** — same amount/date/type submitted by different branches
- **Amount clustering** — unusually round amounts that may indicate estimated entries
- **High-frequency staff** — one staff name appearing on a disproportionate number of receipts
- **Submission velocity** — branches that upload in large bursts (bulk backdating)

---

## Architecture

```
                        ┌─────────────────────────────────────┐
                        │  AWS ECS Fargate (Express Mode)     │
                        │                                     │
  Branch User ─── HTTPS ─→  Flask app (Jinja2 UI + JSON API)  │
  Admin User  ─── HTTPS ─→                                   │
                        │  Auth: AWS Cognito (JWT / PKCE)     │
                        └────────────┬──────────┬────────────┘
                                     │          │
                          ┌──────────┘          └───────────┐
                          ▼                                 ▼
                   Aurora PostgreSQL              S3 Bucket
                   (Serverless v2)          (uploads/ info/ txns/)
                                                     │
                                              Glue Crawler
                                                     │
                                              Athena / QuickSight

  On statement upload:
    Flask ──ECS RunTask──→ Parser task (same image, CMD override)
                              └─→ DB insert → Matcher task
```

**Key AWS services:**
- **ECS Fargate** (Express Mode) — web service + one-off parser/matcher tasks
- **Aurora PostgreSQL Serverless v2** — shared with `webapp-environment-setup`
- **S3** — file and metadata storage (own stack: `cf/s3.yml`)
- **Glue** — daily crawl of `info/` for Athena querying (`cf/glue.yml`)
- **Cognito** — authentication and group-based authorisation
- **Secrets Manager** — DB credentials, Flask secret key
- **CloudWatch** — logs (auto-managed by ECS Express)

---

## Local Development

### Prerequisites
- Docker + Docker Compose
- Python 3.12
- AWS CLI (for deployment only)

### Start the stack

```bash
# 1. Copy env template
cp .env.example .env

# 2. Start Postgres + LocalStack S3
docker compose up -d db localstack

# 3. Run database migrations
docker compose run --rm migrate

# 4. Start the Flask dev server (hot reload)
docker compose up app
```

App runs at **http://localhost:3000**

LocalStack S3 bucket `local-reconciler` is created automatically on startup.

### Without Docker

```bash
cd app
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt

# Set env vars (or source .env)
export DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/appdb
export S3_BUCKET_NAME=local-reconciler
export AWS_ENDPOINT_URL=http://localhost:4566
export AWS_ACCESS_KEY_ID=test
export AWS_SECRET_ACCESS_KEY=test

# Run migrations
alembic upgrade head

# Run app
flask --app app:app run --port 3000 --debug
```

### Running tasks locally

```bash
cd app
python -m tasks.parser --file-id <uuid>
python -m tasks.matcher --file-id <uuid>
```

---

## Deployment

### First-time setup

```bash
# 1. Deploy all infrastructure stacks
./bin/deploy-infra.sh --env staging \
  --db-stack webapp-staging-database \
  --cognito-stack webapp-accounts-reconciller-cognito-staging

# 2. Run database migrations
./bin/migrate.sh --ecs --env staging

# 3. Push Docker image
./bin/push-image.sh --env staging --tag latest

# 4. Smoke test
./bin/smoke-test.sh --env staging
```

### Subsequent deploys

Push to the `staging` branch → `deploy.yml` GitHub Actions workflow runs automatically:
1. Lint + tests + Docker build
2. Deploy ECR, S3, Glue stacks (idempotent)
3. Build + push Docker image (tagged with git SHA)
4. Deploy ECS stack with new image tag (rolling update)
5. Deploy DNS stack
6. Smoke test

Push a `v*.*.*` git tag → deploys to production.

### Manual deployment

```bash
# Build and push image
./bin/push-image.sh --env production --tag v1.2.3

# Deploy app with new image
./bin/deploy-app.sh --env production --tag v1.2.3

# Run migrations (if schema changed)
./bin/migrate.sh --ecs --env production
```

### Triggering parser/matcher manually

```bash
# Get the file ID from the dashboard or DB
./bin/run-task.sh --task parser  --file-id <uuid> --env production
./bin/run-task.sh --task matcher --file-id <uuid> --env production
```

---

## Infrastructure Stacks

| Stack | Template | Description |
|-------|----------|-------------|
| `webapp-accounts-reconciller-ecr` | `cf/ecr.yml` | ECR repository |
| `webapp-accounts-reconciller-s3-<env>` | `cf/s3.yml` | S3 bucket + Glue IAM role |
| `webapp-accounts-reconciller-glue-<env>` | `cf/glue.yml` | Glue DB + crawlers |
| `webapp-accounts-reconciller-ecs-<env>` | `cf/ecs.yml` | ECS Express service |
| `webapp-accounts-reconciller-cognito-<env>` | `cf/cognito.yml` | Cognito User Pool |
| `webapp-accounts-reconciller-dns-<env>` | `cf/dns.yml` | Route53 CNAME |
| `webapp-staging-network` *(shared)* | `webapp-environment-setup/cf/network.yml` | VPC, subnets, SGs |
| `webapp-staging-database` *(shared)* | `webapp-environment-setup/cf/database.yml` | Aurora PostgreSQL |

---

## Adding a New Bank Parser

1. **Create the parser file:**

```python
# app/tasks/parsers/newbank.py
from tasks.parsers.base import BaseParser, ParsedTransaction

class NewbankParser(BaseParser):
    def supported_extensions(self) -> list[str]:
        return ["pdf", "csv"]

    def parse(self, file_bytes: bytes, password=None) -> list[ParsedTransaction]:
        # 1. Detect format (PDF vs CSV) from magic bytes
        # 2. Decrypt if password provided
        # 3. Extract rows and map to ParsedTransaction
        ...
        return transactions
```

2. **Add to `models.py`** — add `"newbank"` to `STATEMENT_TYPES` and `ALL_PAYMENT_TYPES` as needed.

3. **Add to DB check constraints** in a new Alembic migration:

```bash
cd app && alembic revision -m "add_newbank_statement_type"
```

4. **Test locally:**

```bash
cd app
python -m tasks.parser --file-id <uuid-of-a-pending-newbank-file>
```

The parser registry (`tasks/parsers/base.py`) auto-discovers parsers by importing `tasks.parsers.<source>` and looking for `<Source>Parser`. No registration step required.
