import json
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Merchant, PaymentEvent, ReconciliationRecord, Transaction
from app.schemas import EventIngest, EventIngestResponse

logger = logging.getLogger(__name__)

# Map event_type → Transaction status field to set + timestamp field
EVENT_STATUS_MAP: dict[str, tuple[str, str]] = {
    "payment_initiated": ("initiated", "initiated_at"),
    "payment_processed": ("processed", "processed_at"),
    "payment_failed": ("failed", "failed_at"),
    "settled": ("settled", "settled_at"),
}

# Valid forward transitions: current_status → set of accepted next event types
VALID_TRANSITIONS: dict[str, set[str]] = {
    "initiated": {"payment_processed", "payment_failed"},
    "processed": {"settled"},
    "failed": set(),
    "settled": set(),
}


def _upsert_merchant(db: Session, merchant_id: str, merchant_name: str) -> Merchant:
    merchant = db.get(Merchant, merchant_id)
    if merchant is None:
        merchant = Merchant(id=merchant_id, name=merchant_name)
        db.add(merchant)
    else:
        merchant.name = merchant_name  # keep name in sync
    return merchant


def _upsert_transaction(db: Session, event: EventIngest) -> Transaction:
    tx = db.get(Transaction, event.transaction_id)
    if tx is None:
        tx = Transaction(
            id=event.transaction_id,
            merchant_id=event.merchant_id,
            amount=float(event.amount),
            currency=event.currency,
            status="initiated",
        )
        db.add(tx)
    return tx


def _advance_status(tx: Transaction, event: EventIngest) -> None:
    """
    Attempt to advance the transaction status based on the event type.
    Silently no-ops if the transition is invalid (backward/duplicate).
    """
    new_status, timestamp_field = EVENT_STATUS_MAP.get(event.event_type, (None, None))
    if new_status is None:
        return

    # payment_initiated: only set if still at default
    if event.event_type == "payment_initiated":
        if tx.status == "initiated" and tx.initiated_at is None:
            tx.initiated_at = event.timestamp
        return

    if event.event_type not in VALID_TRANSITIONS.get(tx.status, set()):
        logger.debug(
            "Ignoring invalid transition %s -> %s for tx %s",
            tx.status,
            event.event_type,
            tx.id,
        )
        return

    tx.status = new_status
    setattr(tx, timestamp_field, event.timestamp)


def _update_reconciliation(db: Session, tx: Transaction, event: EventIngest) -> None:
    rec = db.execute(
        select(ReconciliationRecord).where(ReconciliationRecord.transaction_id == tx.id)
    ).scalar_one_or_none()

    if rec is None:
        rec = ReconciliationRecord(
            transaction_id=tx.id,
            payment_status=tx.status,
            settlement_status="pending",
        )
        db.add(rec)
    else:
        rec.payment_status = tx.status

    if event.event_type == "settled":
        rec.settlement_status = "settled"
        rec.settled_at = event.timestamp

    # Detect discrepancies
    rec.is_discrepancy = False
    rec.discrepancy_reason = None

    if rec.payment_status == "failed" and rec.settlement_status == "settled":
        rec.is_discrepancy = True
        rec.discrepancy_reason = "Settlement recorded for a failed payment"

    elif rec.payment_status == "processed" and rec.settlement_status == "pending":
        # Allow some leeway — only flag as discrepancy if explicitly checked
        # (will be evaluated in the summary endpoint with time window logic)
        pass

    elif rec.payment_status == "initiated" and rec.settlement_status == "settled":
        rec.is_discrepancy = True
        rec.discrepancy_reason = "Settlement recorded without payment processing"


def ingest_event(db: Session, event: EventIngest) -> EventIngestResponse:
    """
    Ingest a single payment event.  Returns status="duplicate" if the
    event_id has already been processed; status="created" otherwise.
    """
    # Check duplicate before touching anything
    existing = db.execute(
        select(PaymentEvent).where(PaymentEvent.event_id == event.event_id)
    ).scalar_one_or_none()

    if existing is not None:
        return EventIngestResponse(
            status="duplicate",
            event_id=event.event_id,
            transaction_id=event.transaction_id,
            message="Event already processed; no state change applied.",
        )

    # Upsert merchant and transaction
    _upsert_merchant(db, event.merchant_id, event.merchant_name)
    tx = _upsert_transaction(db, event)

    # Advance FSM
    _advance_status(tx, event)

    # Persist event record
    pe = PaymentEvent(
        event_id=event.event_id,
        transaction_id=event.transaction_id,
        merchant_id=event.merchant_id,
        event_type=event.event_type,
        amount=float(event.amount),
        currency=event.currency,
        timestamp=event.timestamp,
        raw_payload=event.model_dump_json(),
    )
    db.add(pe)

    # Update reconciliation
    _update_reconciliation(db, tx, event)

    try:
        db.flush()  # flush all pending objects to detect constraint violations
        db.commit()
    except IntegrityError:
        db.rollback()
        # Race condition: another request committed the same event_id first
        return EventIngestResponse(
            status="duplicate",
            event_id=event.event_id,
            transaction_id=event.transaction_id,
            message="Event already processed (concurrent submission); no state change applied.",
        )

    return EventIngestResponse(
        status="created",
        event_id=event.event_id,
        transaction_id=event.transaction_id,
        message="Event ingested successfully.",
    )
