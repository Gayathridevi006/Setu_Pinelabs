# Setu Payment Reconciliation Service

A production-minded backend service for ingesting payment lifecycle events, maintaining transaction state, and identifying reconciliation discrepancies.

---

## Architecture Overview

'''
POST /events          → EventIngest schema → FSM + idempotency guard → DB
GET  /transactions    → SQL filter/sort/paginate → TransactionOut
GET  /transactions/id → JOIN merchant + events → TransactionDetailOut
GET  /reconciliation/summary       → SQL GROUP BY + CASE → per-dimension counts
GET  /reconciliation/discrepancies → SQL OR predicates across recon + tx tables
'''

### Stack

| Layer | Choice | Reason |
|---|---|---|
| Framework | FastAPI | Async-ready, automatic OpenAPI docs, Pydantic v2 validation |
| ORM | SQLAlchemy 2.0 | Typed mapped columns, clean query API, supports SQLite + PostgreSQL |
| Database | SQLite (dev) / PostgreSQL (prod) | SQLite for zero-config local dev; same schema runs on PostgreSQL |
| Validation | Pydantic v2 | Field-level validation, discriminated unions, clear error messages |
| Testing | pytest + httpx TestClient | Fast in-memory SQLite, per-test rollback isolation |

### Database Schema

'''
merchants
  id (PK, str)
  name
  created_at, updated_at

transactions
  id (PK, str)                         ← transaction_id from events
  merchant_id (FK → merchants)
  amount, currency
  status                               ← driven by event FSM
  initiated_at, processed_at,
  failed_at, settled_at
  created_at, updated_at
  INDEXES: merchant_id, status, created_at, (merchant_id, status)

payment_events                         ← append-only log
  id (PK, autoincrement)
  event_id (UNIQUE)                    ← idempotency key
  transaction_id (FK → transactions)
  merchant_id, event_type
  amount, currency, timestamp
  raw_payload (JSON text, full audit)
  INDEXES: transaction_id, merchant_id, event_type, timestamp

reconciliation_records                 ← one row per transaction
  id (PK, autoincrement)
  transaction_id (FK, UNIQUE)
  payment_status, settlement_status
  is_discrepancy (bool)
  discrepancy_reason
  settled_at
  INDEXES: is_discrepancy, payment_status, settlement_status
'''

**Why separate `reconciliation_records`?**
Keeping settlement state separate from the transaction's payment status makes discrepancy detection a simple, fast SQL join rather than complex application logic. It also lets settlement state evolve independently (e.g. for partial settlements in future).

### Event State Machine

'''
payment_initiated -> payment_processed -> settled  (terminal)
                  -> payment_failed               (terminal)
'''

Backward/invalid transitions are silently ignored; the event is still stored in `payment_events` for a complete audit trail.

### Idempotency

Idempotency is enforced at two levels:

1. **Pre-check**: Before inserting, the service queries `payment_events` for the incoming `event_id`. If found, it returns `status=duplicate` immediately, touching nothing.
2. **Constraint guard**: A `UNIQUE` constraint on `payment_events.event_id` catches any race condition where two concurrent requests slip through the pre-check. The `IntegrityError` is caught and returned as `status=duplicate`.

---

## Local Setup

### Option 1: Plain Python (recommended for review)

'''bash
git clone <repo-url>
cd setu-payments

python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

pip install -r requirements.txt

python scripts/generate_sample_data.py

python scripts/seed_fast.py

uvicorn app.main:app --reload --port 8000
'''


### Option 2: Docker

'''bash
docker compose up --build
'''

The container auto-generates and seeds sample data at startup.

### Environment Variables

Copy `.env.example` to `.env` and adjust as needed:

'''bash
cp .env.example .env
'''

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `sqlite:'''/./setu_payments.db` | Any SQLAlchemy-compatible connection string |
| `DEBUG` | `false` | Enable SQL query logging |
| `APP_VERSION` | `1.0.0` | Shown in `/health` response |

---

## Running Tests

'''bash
pytest tests/ -v
'''


Test coverage includes:
- Happy path event flow (initiated → processed → settled)
- Idempotency (duplicate event returns `status=duplicate`)
- FSM enforcement (failed → processed is a no-op; settled → anything is a no-op)
- All filter/sort/pagination combinations
- 404 on unknown transaction
- 422 on invalid event type or negative amount
- Discrepancy detection (failed + settled, processed + never settled)

---

## API Documentation

Full Swagger UI: `GET /docs`  
ReDoc: `GET /redoc`

### `POST /events`

Ingest a single payment event. Idempotent on `event_id`.

**Request body:**
'''json
{
  "event_id": "uuid",
  "event_type": "payment_initiated | payment_processed | payment_failed | settled",
  "transaction_id": "uuid",
  "merchant_id": "string",
  "merchant_name": "string",
  "amount": 1234.56,
  "currency": "INR",
  "timestamp": "2026-01-15T10:00:00+00:00"
}
'''

**Response:**
'''json
{
  "status": "created | duplicate",
  "event_id": "uuid",
  "transaction_id": "uuid",
  "message": "Event ingested successfully."
}
'''

---

### `GET /transactions`

**Query parameters:**

| Param | Type | Description |
|---|---|---|
| `merchant_id` | string | Filter by merchant |
| `status` | enum | `initiated \| processed \| failed \| settled` |
| `date_from` | datetime | Filter `created_at >=` (ISO 8601) |
| `date_to` | datetime | Filter `created_at <=` (ISO 8601) |
| `sort_by` | enum | `created_at \| updated_at \| amount \| status` (default: `created_at`) |
| `sort_order` | enum | `asc \| desc` (default: `desc`) |
| `page` | int ≥ 1 | Page number (default: 1) |
| `page_size` | int 1–200 | Items per page (default: 20) |

**Response:**
'''json
{
  "total": 3821,
  "page": 1,
  "page_size": 20,
  "items": [{ "id": "...", "merchant_id": "...", "amount": 4999.0, "status": "settled", ... }]
}
'''

---

### `GET /transactions/{transaction_id}`

Returns full transaction detail including merchant info and event history.

**Response:**
'''json
{
  "id": "txn-demo-001",
  "merchant_id": "merchant_1",
  "amount": 4999.0,
  "currency": "INR",
  "status": "settled",
  "initiated_at": "2026-01-15T10:00:00Z",
  "processed_at": "2026-01-15T10:03:00Z",
  "failed_at": null,
  "settled_at": "2026-01-15T14:00:00Z",
  "merchant": { "id": "merchant_1", "name": "QuickMart" },
  "events": [
    { "event_id": "...", "event_type": "payment_initiated", "amount": 4999.0, "timestamp": "..." },
    ...
  ]
}
'''

---

### `GET /reconciliation/summary`

**Query parameters:**

| Param | Type | Description |
|---|---|---|
| `group_by` | enum | `merchant \| date \| status` (default: `merchant`) |
| `date_from` | datetime | Optional range filter |
| `date_to` | datetime | Optional range filter |
| `merchant_id` | string | Limit to one merchant |

**Response:**
'''json
{
  "group_by": "merchant",
  "items": [
    {
      "dimension": "merchant",
      "dimension_value": "merchant_1",
      "total_transactions": 749,
      "total_amount": 18006571.38,
      "payment_initiated": 0,
      "payment_processed": 116,
      "payment_failed": 190,
      "settled": 443
    }
  ]
}
'''

---

### `GET /reconciliation/discrepancies`

**Query parameters:**

| Param | Type | Description |
|---|---|---|
| `merchant_id` | string | Optional filter |
| `page` | int ≥ 1 | Default: 1 |
| `page_size` | int 1–200 | Default: 50 |

Detects three classes of discrepancy:
1. **Settlement for failed payment** — `payment_status=failed` AND `settlement_status=settled`
2. **Overdue settlement** — `payment_status=processed` AND `settlement_status=pending` AND processed > 24 hours ago
3. **Settlement without processing** — `payment_status=initiated` AND `settlement_status=settled`

**Response:**
'''json
{
  "total": 517,
  "items": [
    {
      "transaction_id": "...",
      "merchant_id": "merchant_2",
      "amount": 22515.53,
      "currency": "INR",
      "payment_status": "processed",
      "settlement_status": "pending",
      "discrepancy_reason": "Payment processed but not settled within 24 hours",
      "created_at": "2026-04-23T08:13:38Z"
    }
  ]
}
'''

---

## Sample Data

`sample_events.json` is generated by `scripts/generate_sample_data.py` and contains ~10,500 events across 5 merchants.

| Scenario | Approximate share |
|---|---|
| Successful (initiated → processed → settled) | 55% |
| Failed payments | 20% |
| Pending settlement (processed, never settled) | 15% |
| Discrepant (failed + settled anomaly) | 5% |
| Duplicates (same event_id submitted 1–3 extra times) | 5% |

After seeding: 3,821 transactions, 10,120 unique events, 517 discrepancies.

---

## Deployment

### Render (recommended for demo)

1. Push to GitHub
2. Create a new **Web Service** on [render.com](https:'''render.com)
3. Set build command: `pip install -r requirements.txt && python scripts/generate_sample_data.py && python scripts/seed_fast.py`
4. Set start command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
5. Add environment variable: `DATABASE_URL=sqlite:'''/./setu_payments.db`

### Fly.io

'''bash
fly launch --no-deploy
fly secrets set DATABASE_URL=sqlite:'''/./setu_payments.db
fly deploy
'''

### PostgreSQL (production)

Set `DATABASE_URL=postgresql:'''user:password@host:5432/setu_payments`. No code changes required — SQLAlchemy handles dialect differences. For production, also add `psycopg2-binary` (already in requirements.txt).






