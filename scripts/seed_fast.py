"""
Fast bulk seed using SQLAlchemy Core (not ORM) for maximum throughput.
Uses the same idempotency logic but batches inserts and skips duplicates.

Processes ~10k events in a few seconds rather than minutes.
"""

import json
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.database import Base, engine
import app.models  # register models with Base


SETTLEMENT_OVERDUE_HOURS = 24


def parse_dt(s: str) -> datetime:
    return datetime.fromisoformat(s)


def determine_tx_status(events_for_tx: list[dict]) -> tuple[str, dict]:
    """Apply FSM to a sorted list of events and return final status + timestamps."""
    events_for_tx = sorted(events_for_tx, key=lambda e: e["timestamp"])
    status = "initiated"
    timestamps = {}
    valid_transitions = {
        "initiated": {"payment_processed", "payment_failed"},
        "processed": {"settled"},
        "failed": set(),
        "settled": set(),
    }
    status_map = {
        "payment_initiated": "initiated",
        "payment_processed": "processed",
        "payment_failed": "failed",
        "settled": "settled",
    }
    ts_fields = {
        "initiated": "initiated_at",
        "processed": "processed_at",
        "failed": "failed_at",
        "settled": "settled_at",
    }

    for ev in events_for_tx:
        etype = ev["event_type"]
        if etype == "payment_initiated":
            if "initiated_at" not in timestamps:
                timestamps["initiated_at"] = ev["timestamp"]
            continue
        if etype not in valid_transitions.get(status, set()):
            continue
        new_status = status_map[etype]
        status = new_status
        timestamps[ts_fields[new_status]] = ev["timestamp"]

    return status, timestamps


def detect_discrepancy(payment_status: str, settlement_status: str, processed_at_str: str | None) -> tuple[bool, str | None]:
    if payment_status == "failed" and settlement_status == "settled":
        return True, "Settlement recorded for a failed payment"
    if payment_status == "initiated" and settlement_status == "settled":
        return True, "Settlement recorded without payment processing"
    if payment_status == "processed" and settlement_status == "pending" and processed_at_str:
        processed_at = parse_dt(processed_at_str)
        overdue = datetime.now(timezone.utc) - timedelta(hours=SETTLEMENT_OVERDUE_HOURS)
        if processed_at < overdue:
            return True, "Payment processed but not settled within 24 hours"
    return False, None


def seed(file_path: Path) -> None:
    Base.metadata.create_all(bind=engine)

    with file_path.open() as f:
        raw_events = json.load(f)

    print(f"Processing {len(raw_events)} events ...")
    start = time.time()

    # De-duplicate events by event_id (keep first seen)
    seen_event_ids: set[str] = set()
    unique_events: list[dict] = []
    duplicate_count = 0
    for ev in raw_events:
        if ev["event_id"] in seen_event_ids:
            duplicate_count += 1
            # Still add as a row with is_duplicate flag for audit purposes
            unique_events.append({**ev, "_is_duplicate": True})
        else:
            seen_event_ids.add(ev["event_id"])
            unique_events.append({**ev, "_is_duplicate": False})

    print(f"  {len(seen_event_ids)} unique event IDs ({duplicate_count} duplicates in source data)")

    # Group events by transaction
    tx_events: dict[str, list[dict]] = defaultdict(list)
    merchants: dict[str, str] = {}

    for ev in unique_events:
        tx_events[ev["transaction_id"]].append(ev)
        merchants[ev["merchant_id"]] = ev["merchant_name"]

    print(f"  {len(tx_events)} unique transactions across {len(merchants)} merchants")

    now = datetime.now(timezone.utc).isoformat()

    # Build bulk insert data
    merchant_rows = [
        {"id": mid, "name": mname, "created_at": now, "updated_at": now}
        for mid, mname in merchants.items()
    ]

    event_rows = []
    for ev in unique_events:
        if ev["_is_duplicate"]:
            continue  # skip duplicate event_ids
        event_rows.append({
            "event_id": ev["event_id"],
            "transaction_id": ev["transaction_id"],
            "merchant_id": ev["merchant_id"],
            "event_type": ev["event_type"],
            "amount": ev["amount"],
            "currency": ev.get("currency", "INR"),
            "timestamp": ev["timestamp"],
            "raw_payload": json.dumps({k: v for k, v in ev.items() if not k.startswith("_")}),
        })

    transaction_rows = []
    reconciliation_rows = []

    for tx_id, events_list in tx_events.items():
        first_ev = events_list[0]
        amount = first_ev["amount"]
        currency = first_ev.get("currency", "INR")
        merchant_id = first_ev["merchant_id"]

        status, timestamps = determine_tx_status(events_list)

        settlement_status = "settled" if status == "settled" else "pending"
        settled_at = timestamps.get("settled_at")
        is_disc, disc_reason = detect_discrepancy(status, settlement_status, timestamps.get("processed_at"))

        transaction_rows.append({
            "id": tx_id,
            "merchant_id": merchant_id,
            "amount": amount,
            "currency": currency,
            "status": status,
            "initiated_at": timestamps.get("initiated_at"),
            "processed_at": timestamps.get("processed_at"),
            "failed_at": timestamps.get("failed_at"),
            "settled_at": settled_at,
            "created_at": now,
            "updated_at": now,
        })

        reconciliation_rows.append({
            "transaction_id": tx_id,
            "payment_status": status,
            "settlement_status": settlement_status,
            "is_discrepancy": is_disc,
            "discrepancy_reason": disc_reason,
            "settled_at": settled_at,
            "created_at": now,
            "updated_at": now,
        })

    # Bulk insert
    CHUNK = 1000
    with engine.begin() as conn:
        # Merchants
        for i in range(0, len(merchant_rows), CHUNK):
            conn.execute(
                engine.dialect.statement_compiler(
                    dialect=engine.dialect,
                    statement=None,
                ).__class__ if False else
                __import__("sqlalchemy").text(
                    "INSERT OR IGNORE INTO merchants (id, name, created_at, updated_at) "
                    "VALUES (:id, :name, :created_at, :updated_at)"
                ),
                merchant_rows[i:i+CHUNK],
            )

        # Transactions
        for i in range(0, len(transaction_rows), CHUNK):
            conn.execute(
                __import__("sqlalchemy").text(
                    "INSERT OR IGNORE INTO transactions "
                    "(id, merchant_id, amount, currency, status, initiated_at, processed_at, failed_at, settled_at, created_at, updated_at) "
                    "VALUES (:id, :merchant_id, :amount, :currency, :status, :initiated_at, :processed_at, :failed_at, :settled_at, :created_at, :updated_at)"
                ),
                transaction_rows[i:i+CHUNK],
            )

        # Events
        for i in range(0, len(event_rows), CHUNK):
            conn.execute(
                __import__("sqlalchemy").text(
                    "INSERT OR IGNORE INTO payment_events "
                    "(event_id, transaction_id, merchant_id, event_type, amount, currency, timestamp, raw_payload) "
                    "VALUES (:event_id, :transaction_id, :merchant_id, :event_type, :amount, :currency, :timestamp, :raw_payload)"
                ),
                event_rows[i:i+CHUNK],
            )

        # Reconciliation
        for i in range(0, len(reconciliation_rows), CHUNK):
            conn.execute(
                __import__("sqlalchemy").text(
                    "INSERT OR IGNORE INTO reconciliation_records "
                    "(transaction_id, payment_status, settlement_status, is_discrepancy, discrepancy_reason, settled_at, created_at, updated_at) "
                    "VALUES (:transaction_id, :payment_status, :settlement_status, :is_discrepancy, :discrepancy_reason, :settled_at, :created_at, :updated_at)"
                ),
                reconciliation_rows[i:i+CHUNK],
            )

    elapsed = time.time() - start
    print(f"Done in {elapsed:.2f}s")
    print(f"  Merchants: {len(merchant_rows)}")
    print(f"  Transactions: {len(transaction_rows)}")
    print(f"  Events: {len(event_rows)}")
    print(f"  Reconciliation records: {len(reconciliation_rows)}")
    disc_count = sum(1 for r in reconciliation_rows if r["is_discrepancy"])
    print(f"  Discrepancies: {disc_count}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", default=str(Path(__file__).parent.parent / "sample_events.json"))
    args = parser.parse_args()
    seed(Path(args.file))
