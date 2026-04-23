"""
Pydantic v2 schemas for request validation and response serialisation.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------

EventType = Literal[
    "payment_initiated",
    "payment_processed",
    "payment_failed",
    "settled",
]


class EventIngest(BaseModel):
    event_id: str = Field(..., min_length=1, max_length=64)
    event_type: EventType
    transaction_id: str = Field(..., min_length=1, max_length=64)
    merchant_id: str = Field(..., min_length=1, max_length=64)
    merchant_name: str = Field(..., min_length=1, max_length=255)
    amount: float = Field(..., gt=0)
    currency: str = Field(default="INR", max_length=8)
    timestamp: datetime

    @field_validator("currency")
    @classmethod
    def currency_upper(cls, v: str) -> str:
        return v.upper()


class EventIngestResponse(BaseModel):
    status: Literal["created", "duplicate"]
    event_id: str
    transaction_id: str
    message: str


class EventOut(BaseModel):
    event_id: str
    event_type: str
    amount: float
    currency: str
    timestamp: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Transactions
# ---------------------------------------------------------------------------

TransactionStatus = Literal["initiated", "processed", "failed", "settled"]


class MerchantOut(BaseModel):
    id: str
    name: str

    model_config = {"from_attributes": True}


class TransactionOut(BaseModel):
    id: str
    merchant_id: str
    amount: float
    currency: str
    status: str
    initiated_at: datetime | None
    processed_at: datetime | None
    failed_at: datetime | None
    settled_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class TransactionDetailOut(TransactionOut):
    merchant: MerchantOut
    events: list[EventOut]

    model_config = {"from_attributes": True}


class PaginatedTransactions(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[TransactionOut]


# ---------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------

class ReconciliationSummaryItem(BaseModel):
    dimension: str          # e.g. "merchant", "date", "status"
    dimension_value: str
    total_transactions: int
    total_amount: float
    payment_initiated: int
    payment_processed: int
    payment_failed: int
    settled: int


class ReconciliationSummaryResponse(BaseModel):
    group_by: str
    items: list[ReconciliationSummaryItem]


class DiscrepancyOut(BaseModel):
    transaction_id: str
    merchant_id: str
    amount: float
    currency: str
    payment_status: str
    settlement_status: str
    discrepancy_reason: str
    created_at: datetime


class ReconciliationDiscrepanciesResponse(BaseModel):
    total: int
    items: list[DiscrepancyOut]


# ---------------------------------------------------------------------------
# Generic error
# ---------------------------------------------------------------------------

class ErrorResponse(BaseModel):
    error: str
    detail: str | None = None
