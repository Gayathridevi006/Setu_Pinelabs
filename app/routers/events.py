from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import ErrorResponse, EventIngest, EventIngestResponse
from app.services.events import ingest_event

router = APIRouter(prefix="/events", tags=["Events"])


@router.post(
    "",
    response_model=EventIngestResponse,
    status_code=status.HTTP_200_OK,
    responses={
        200: {"description": "Event created or duplicate detected"},
        422: {"model": ErrorResponse, "description": "Validation error"},
    },
    summary="Ingest a payment lifecycle event",
    description=(
        "Accepts a single payment event. Idempotent: re-submitting the same "
        "`event_id` returns `status=duplicate` without mutating state."
    ),
)
def post_event(payload: EventIngest, db: Session = Depends(get_db)) -> EventIngestResponse:
    return ingest_event(db, payload)
