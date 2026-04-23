from datetime import datetime, timedelta, timezone
from typing import Literal

from sqlalchemy import String, case, cast, func, literal_column, select, text
from sqlalchemy.orm import Session

from app.models import ReconciliationRecord, Transaction
from app.schemas import (
    DiscrepancyOut,
    ReconciliationDiscrepanciesResponse,
    ReconciliationSummaryItem,
    ReconciliationSummaryResponse,
)

GroupBy = Literal["merchant", "date", "status"]


def get_summary(
    db: Session,
    group_by: GroupBy = "merchant",
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    merchant_id: str | None = None,
) -> ReconciliationSummaryResponse:
    """
    Return aggregate counts/amounts grouped by merchant, date, or status.
    Uses CASE expressions so the whole thing is a single SQL pass.
    """
    # Choose the grouping expression
    if group_by == "merchant":
        dim_expr = Transaction.merchant_id
    elif group_by == "date":
        dim_expr = func.date(Transaction.created_at)
    else:  # status
        dim_expr = Transaction.status

    stmt = (
        select(
            dim_expr.label("dimension_value"),
            func.count(Transaction.id).label("total_transactions"),
            func.sum(Transaction.amount).label("total_amount"),
            func.sum(
                case((Transaction.status == "initiated", 1), else_=0)
            ).label("payment_initiated"),
            func.sum(
                case((Transaction.status == "processed", 1), else_=0)
            ).label("payment_processed"),
            func.sum(
                case((Transaction.status == "failed", 1), else_=0)
            ).label("payment_failed"),
            func.sum(
                case((Transaction.status == "settled", 1), else_=0)
            ).label("settled"),
        )
        .select_from(Transaction)
        .group_by(dim_expr)
        .order_by(dim_expr)
    )

    if date_from:
        stmt = stmt.where(Transaction.created_at >= date_from)
    if date_to:
        stmt = stmt.where(Transaction.created_at <= date_to)
    if merchant_id:
        stmt = stmt.where(Transaction.merchant_id == merchant_id)

    rows = db.execute(stmt).all()

    items = [
        ReconciliationSummaryItem(
            dimension=group_by,
            dimension_value=str(r.dimension_value),
            total_transactions=r.total_transactions,
            total_amount=float(r.total_amount or 0),
            payment_initiated=r.payment_initiated,
            payment_processed=r.payment_processed,
            payment_failed=r.payment_failed,
            settled=r.settled,
        )
        for r in rows
    ]

    return ReconciliationSummaryResponse(group_by=group_by, items=items)


# How long before a "processed" transaction is considered overdue for settlement
SETTLEMENT_OVERDUE_HOURS = 24


def get_discrepancies(
    db: Session,
    merchant_id: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> ReconciliationDiscrepanciesResponse:
    """
    Identify transactions where payment and settlement states are inconsistent.

    Discrepancy types detected:
    1. payment_status=failed AND settlement_status=settled
       → Settlement recorded for a failed payment.
    2. payment_status=processed AND settlement_status=pending AND processed >24h ago
       → Payment processed but never settled (overdue).
    3. payment_status=initiated AND settlement_status=settled
       → Settlement recorded without payment processing.
    4. Transactions with multiple conflicting events (duplicate-driven state conflicts)
       already flagged is_discrepancy=True by the ingestion service.

    All of this is a single SQL query with OR predicates.
    """
    overdue_cutoff = datetime.now(timezone.utc) - timedelta(hours=SETTLEMENT_OVERDUE_HOURS)

    stmt = (
        select(
            Transaction.id.label("transaction_id"),
            Transaction.merchant_id,
            Transaction.amount,
            Transaction.currency,
            ReconciliationRecord.payment_status,
            ReconciliationRecord.settlement_status,
            case(
                (
                    ReconciliationRecord.is_discrepancy == True,
                    ReconciliationRecord.discrepancy_reason,
                ),
                (
                    (ReconciliationRecord.payment_status == "failed")
                    & (ReconciliationRecord.settlement_status == "settled"),
                    literal_column("'Settlement recorded for a failed payment'"),
                ),
                (
                    (ReconciliationRecord.payment_status == "processed")
                    & (ReconciliationRecord.settlement_status == "pending")
                    & (Transaction.processed_at < overdue_cutoff),
                    literal_column("'Payment processed but not settled within 24 hours'"),
                ),
                (
                    (ReconciliationRecord.payment_status == "initiated")
                    & (ReconciliationRecord.settlement_status == "settled"),
                    literal_column("'Settlement recorded without payment being processed'"),
                ),
                else_=literal_column("'Unknown discrepancy'"),
            ).label("discrepancy_reason"),
            Transaction.created_at,
        )
        .join(ReconciliationRecord, ReconciliationRecord.transaction_id == Transaction.id)
        .where(
            (ReconciliationRecord.is_discrepancy == True)
            | (
                (ReconciliationRecord.payment_status == "failed")
                & (ReconciliationRecord.settlement_status == "settled")
            )
            | (
                (ReconciliationRecord.payment_status == "processed")
                & (ReconciliationRecord.settlement_status == "pending")
                & (Transaction.processed_at < overdue_cutoff)
            )
            | (
                (ReconciliationRecord.payment_status == "initiated")
                & (ReconciliationRecord.settlement_status == "settled")
            )
        )
    )

    if merchant_id:
        stmt = stmt.where(Transaction.merchant_id == merchant_id)

    stmt = stmt.order_by(Transaction.created_at.desc())

    # Count
    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = db.execute(count_stmt).scalar_one()

    # Paginate
    offset = (page - 1) * page_size
    stmt = stmt.offset(offset).limit(page_size)

    rows = db.execute(stmt).all()

    items = [
        DiscrepancyOut(
            transaction_id=r.transaction_id,
            merchant_id=r.merchant_id,
            amount=float(r.amount),
            currency=r.currency,
            payment_status=r.payment_status,
            settlement_status=r.settlement_status,
            discrepancy_reason=r.discrepancy_reason or "Unknown",
            created_at=r.created_at,
        )
        for r in rows
    ]

    return ReconciliationDiscrepanciesResponse(total=total, items=items)
