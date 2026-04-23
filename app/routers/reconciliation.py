from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import ReconciliationDiscrepanciesResponse, ReconciliationSummaryResponse
from app.services.reconciliation import get_discrepancies, get_summary

router = APIRouter(prefix="/reconciliation", tags=["Reconciliation"])


@router.get(
    "/summary",
    response_model=ReconciliationSummaryResponse,
    summary="Aggregated reconciliation summary grouped by a chosen dimension",
    description=(
        "Returns transaction counts and amounts grouped by `merchant`, `date`, "
        "or `status`. Optionally filter by date range or merchant."
    ),
)
def reconciliation_summary(
    group_by: Annotated[
        Literal["merchant", "date", "status"],
        Query(description="Grouping dimension"),
    ] = "merchant",
    date_from: Annotated[datetime | None, Query(description="Filter: created_at >= date_from")] = None,
    date_to: Annotated[datetime | None, Query(description="Filter: created_at <= date_to")] = None,
    merchant_id: Annotated[str | None, Query(description="Limit to a single merchant")] = None,
    db: Session = Depends(get_db),
) -> ReconciliationSummaryResponse:
    return get_summary(db, group_by=group_by, date_from=date_from, date_to=date_to, merchant_id=merchant_id)


@router.get(
    "/discrepancies",
    response_model=ReconciliationDiscrepanciesResponse,
    summary="List transactions with payment / settlement inconsistencies",
    description=(
        "Detects: (1) settlement recorded for a failed payment, "
        "(2) payment processed but not settled within 24 h, "
        "(3) settlement without processing, "
        "(4) duplicate-event-driven state conflicts."
    ),
)
def reconciliation_discrepancies(
    merchant_id: Annotated[str | None, Query(description="Filter by merchant")] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
    db: Session = Depends(get_db),
) -> ReconciliationDiscrepanciesResponse:
    return get_discrepancies(db, merchant_id=merchant_id, page=page, page_size=page_size)
