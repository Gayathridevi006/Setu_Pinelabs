"""
ORM models for the Setu payment reconciliation service.

Schema design notes:
- merchants: denormalised from events for fast joins; updated on conflict
- transactions: one row per transaction_id; status driven by event FSM
- payment_events: append-only log; unique on event_id for idempotency
- reconciliation_records: one row per transaction tracking settlement state
"""

from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Merchant(Base):
    __tablename__ = "merchants"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    transactions: Mapped[list["Transaction"]] = relationship(back_populates="merchant")

    def __repr__(self) -> str:
        return f"<Merchant id={self.id} name={self.name}>"


class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    merchant_id: Mapped[str] = mapped_column(ForeignKey("merchants.id"), nullable=False)
    amount: Mapped[float] = mapped_column(Numeric(15, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="INR")

    # Lifecycle status: initiated | processed | failed | settled
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="initiated")

    initiated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    merchant: Mapped["Merchant"] = relationship(back_populates="transactions")
    events: Mapped[list["PaymentEvent"]] = relationship(
        back_populates="transaction", order_by="PaymentEvent.timestamp"
    )
    reconciliation: Mapped["ReconciliationRecord | None"] = relationship(
        back_populates="transaction", uselist=False
    )

    __table_args__ = (
        Index("ix_transactions_merchant_id", "merchant_id"),
        Index("ix_transactions_status", "status"),
        Index("ix_transactions_created_at", "created_at"),
        Index("ix_transactions_merchant_status", "merchant_id", "status"),
    )

    def __repr__(self) -> str:
        return f"<Transaction id={self.id} status={self.status}>"


class PaymentEvent(Base):
    """
    Append-only event log.  The unique constraint on event_id is the
    primary idempotency guard — re-submitting the same event_id is a no-op.
    """

    __tablename__ = "payment_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(64), nullable=False)
    transaction_id: Mapped[str] = mapped_column(ForeignKey("transactions.id"), nullable=False)
    merchant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    amount: Mapped[float] = mapped_column(Numeric(15, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="INR")
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    raw_payload: Mapped[str | None] = mapped_column(Text)  # store original JSON for audit

    transaction: Mapped["Transaction"] = relationship(back_populates="events")

    __table_args__ = (
        UniqueConstraint("event_id", name="uq_payment_events_event_id"),
        Index("ix_payment_events_transaction_id", "transaction_id"),
        Index("ix_payment_events_merchant_id", "merchant_id"),
        Index("ix_payment_events_event_type", "event_type"),
        Index("ix_payment_events_timestamp", "timestamp"),
    )

    def __repr__(self) -> str:
        return f"<PaymentEvent event_id={self.event_id} type={self.event_type}>"


class ReconciliationRecord(Base):
    """
    One row per transaction capturing settlement state separately from
    payment state so discrepancies can be detected with a simple query.
    """

    __tablename__ = "reconciliation_records"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    transaction_id: Mapped[str] = mapped_column(
        ForeignKey("transactions.id"), nullable=False, unique=True
    )
    payment_status: Mapped[str] = mapped_column(String(32), nullable=False)
    settlement_status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    is_discrepancy: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    discrepancy_reason: Mapped[str | None] = mapped_column(String(512))
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    transaction: Mapped["Transaction"] = relationship(back_populates="reconciliation")

    __table_args__ = (
        Index("ix_reconciliation_is_discrepancy", "is_discrepancy"),
        Index("ix_reconciliation_payment_status", "payment_status"),
        Index("ix_reconciliation_settlement_status", "settlement_status"),
    )

    def __repr__(self) -> str:
        return (
            f"<ReconciliationRecord tx={self.transaction_id} "
            f"payment={self.payment_status} settlement={self.settlement_status}>"
        )
