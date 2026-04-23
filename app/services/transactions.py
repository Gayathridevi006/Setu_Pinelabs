from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.models import Transaction
from app.schemas import PaginatedTransactions, TransactionDetailOut, TransactionOut


def list_transactions(
    db: Session,
    merchant_id: str | None = None,
    status: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    sort_by: str = "created_at",
    sort_order: str = "desc",
    page: int = 1,
    page_size: int = 20,
) -> PaginatedTransactions:
    # Base query
    stmt = select(Transaction)

    if merchant_id:
        stmt = stmt.where(Transaction.merchant_id == merchant_id)
    if status:
        stmt = stmt.where(Transaction.status == status)
    if date_from:
        stmt = stmt.where(Transaction.created_at >= date_from)
    if date_to:
        stmt = stmt.where(Transaction.created_at <= date_to)

    # Count total (separate lightweight query)
    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = db.execute(count_stmt).scalar_one()

    # Sorting
    allowed_sort_fields = {"created_at", "updated_at", "amount", "status"}
    sort_col_name = sort_by if sort_by in allowed_sort_fields else "created_at"
    sort_col = getattr(Transaction, sort_col_name)
    if sort_order.lower() == "asc":
        stmt = stmt.order_by(sort_col.asc())
    else:
        stmt = stmt.order_by(sort_col.desc())

    # Pagination
    offset = (page - 1) * page_size
    stmt = stmt.offset(offset).limit(page_size)

    rows = db.execute(stmt).scalars().all()
    items = [TransactionOut.model_validate(r) for r in rows]

    return PaginatedTransactions(
        total=total,
        page=page,
        page_size=page_size,
        items=items,
    )


def get_transaction_detail(db: Session, transaction_id: str) -> TransactionDetailOut | None:
    stmt = (
        select(Transaction)
        .where(Transaction.id == transaction_id)
        .options(
            joinedload(Transaction.merchant),
            joinedload(Transaction.events),
        )
    )
    tx = db.execute(stmt).unique().scalar_one_or_none()
    if tx is None:
        return None
    return TransactionDetailOut.model_validate(tx)
