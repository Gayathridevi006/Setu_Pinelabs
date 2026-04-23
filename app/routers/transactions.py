from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import PaginatedTransactions, TransactionDetailOut
from app.services.transactions import get_transaction_detail, list_transactions

router = APIRouter(prefix="/transactions", tags=["Transactions"])


@router.get(
    "",
    response_model=PaginatedTransactions,
    summary="List transactions with filtering, sorting, and pagination",
)
def get_transactions(
    merchant_id: Annotated[str | None, Query(description="Filter by merchant ID")] = None,
    status: Annotated[
        Literal["initiated", "processed", "failed", "settled"] | None,
        Query(description="Filter by transaction status"),
    ] = None,
    date_from: Annotated[datetime | None, Query(description="Filter: created_at >= date_from (ISO 8601)")] = None,
    date_to: Annotated[datetime | None, Query(description="Filter: created_at <= date_to (ISO 8601)")] = None,
    sort_by: Annotated[
        Literal["created_at", "updated_at", "amount", "status"],
        Query(description="Field to sort by"),
    ] = "created_at",
    sort_order: Annotated[Literal["asc", "desc"], Query(description="Sort direction")] = "desc",
    page: Annotated[int, Query(ge=1, description="Page number (1-indexed)")] = 1,
    page_size: Annotated[int, Query(ge=1, le=200, description="Items per page")] = 20,
    db: Session = Depends(get_db),
) -> PaginatedTransactions:
    return list_transactions(
        db,
        merchant_id=merchant_id,
        status=status,
        date_from=date_from,
        date_to=date_to,
        sort_by=sort_by,
        sort_order=sort_order,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/{transaction_id}",
    response_model=TransactionDetailOut,
    summary="Fetch full transaction details including event history",
    responses={404: {"description": "Transaction not found"}},
)
def get_transaction(
    transaction_id: str,
    db: Session = Depends(get_db),
) -> TransactionDetailOut:
    detail = get_transaction_detail(db, transaction_id)
    if detail is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Transaction '{transaction_id}' not found.",
        )
    return detail
