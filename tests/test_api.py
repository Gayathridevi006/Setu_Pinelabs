import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base, get_db
from app.main import app


# Fixtures


TEST_DB_URL = "sqlite:///:memory:"


@pytest.fixture(scope="session")
def db_engine():
    engine = create_engine(TEST_DB_URL, connect_args={"check_same_thread": False})
    # Register models
    import app.models  # noqa
    Base.metadata.create_all(bind=engine)
    yield engine
    Base.metadata.drop_all(bind=engine)


@pytest.fixture(scope="session")
def SessionTesting(db_engine):
    return sessionmaker(autocommit=False, autoflush=False, bind=db_engine)


@pytest.fixture()
def client(SessionTesting):
    """
    TestClient that overrides the DB dependency with a test session.
    Each test gets a clean transaction rolled back at the end.
    """
    connection = SessionTesting.kw["bind"].connect()
    transaction = connection.begin()
    session = SessionTesting(bind=connection)

    def override_get_db():
        try:
            yield session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c

    app.dependency_overrides.clear()
    session.close()
    transaction.rollback()
    connection.close()



# Helpers


def make_event(
    event_type: str = "payment_initiated",
    transaction_id: str | None = None,
    merchant_id: str = "merchant_test",
    merchant_name: str = "Test Merchant",
    amount: float = 1000.0,
    currency: str = "INR",
    event_id: str | None = None,
    timestamp: str = "2026-01-15T10:00:00+00:00",
) -> dict:
    return {
        "event_id": event_id or str(uuid.uuid4()),
        "event_type": event_type,
        "transaction_id": transaction_id or str(uuid.uuid4()),
        "merchant_id": merchant_id,
        "merchant_name": merchant_name,
        "amount": amount,
        "currency": currency,
        "timestamp": timestamp,
    }


def ingest(client, payload: dict) -> dict:
    resp = client.post("/events", json=payload)
    assert resp.status_code == 200, resp.text
    return resp.json()



# /health


class TestHealth:
    def test_health_ok(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"




class TestEventIngestion:
    def test_ingest_payment_initiated(self, client):
        ev = make_event("payment_initiated")
        result = ingest(client, ev)
        assert result["status"] == "created"
        assert result["event_id"] == ev["event_id"]

    def test_ingest_duplicate_returns_duplicate_status(self, client):
        ev = make_event("payment_initiated")
        r1 = ingest(client, ev)
        r2 = ingest(client, ev)  # exact same payload
        assert r1["status"] == "created"
        assert r2["status"] == "duplicate"

    def test_duplicate_does_not_corrupt_state(self, client):
        tx_id = str(uuid.uuid4())
        init = make_event("payment_initiated", transaction_id=tx_id)
        proc = make_event("payment_processed", transaction_id=tx_id)
        ingest(client, init)
        ingest(client, proc)

        # Re-submit initiated — should not revert status
        ingest(client, init)

        resp = client.get(f"/transactions/{tx_id}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "processed"

    def test_full_happy_path_status_progression(self, client):
        tx_id = str(uuid.uuid4())
        ingest(client, make_event("payment_initiated", transaction_id=tx_id, timestamp="2026-01-15T10:00:00+00:00"))
        ingest(client, make_event("payment_processed", transaction_id=tx_id, timestamp="2026-01-15T10:05:00+00:00"))
        ingest(client, make_event("settled", transaction_id=tx_id, timestamp="2026-01-15T14:00:00+00:00"))

        resp = client.get(f"/transactions/{tx_id}")
        data = resp.json()
        assert data["status"] == "settled"
        assert data["settled_at"] is not None

    def test_payment_failed_is_terminal(self, client):
        tx_id = str(uuid.uuid4())
        ingest(client, make_event("payment_initiated", transaction_id=tx_id))
        ingest(client, make_event("payment_failed", transaction_id=tx_id))
        # Try to process after failure — should be ignored
        ingest(client, make_event("payment_processed", transaction_id=tx_id))

        resp = client.get(f"/transactions/{tx_id}")
        assert resp.json()["status"] == "failed"

    def test_settled_is_terminal(self, client):
        tx_id = str(uuid.uuid4())
        ingest(client, make_event("payment_initiated", transaction_id=tx_id))
        ingest(client, make_event("payment_processed", transaction_id=tx_id))
        ingest(client, make_event("settled", transaction_id=tx_id))
        # Try to fail after settlement — should be ignored
        ingest(client, make_event("payment_failed", transaction_id=tx_id))

        resp = client.get(f"/transactions/{tx_id}")
        assert resp.json()["status"] == "settled"

    def test_invalid_event_type_rejected(self, client):
        ev = make_event()
        ev["event_type"] = "totally_invalid"
        resp = client.post("/events", json=ev)
        assert resp.status_code == 422

    def test_negative_amount_rejected(self, client):
        ev = make_event()
        ev["amount"] = -100
        resp = client.post("/events", json=ev)
        assert resp.status_code == 422

    def test_currency_normalised_to_uppercase(self, client):
        ev = make_event(currency="inr")
        ingest(client, ev)
        resp = client.get(f"/transactions/{ev['transaction_id']}")
        assert resp.json()["currency"] == "INR"

    def test_event_history_preserved(self, client):
        tx_id = str(uuid.uuid4())
        ingest(client, make_event("payment_initiated", transaction_id=tx_id))
        ingest(client, make_event("payment_processed", transaction_id=tx_id))

        resp = client.get(f"/transactions/{tx_id}")
        events = resp.json()["events"]
        event_types = [e["event_type"] for e in events]
        assert "payment_initiated" in event_types
        assert "payment_processed" in event_types



# GET /transactions


class TestListTransactions:
    def _seed_transactions(self, client, merchant_id: str, count: int = 3) -> list[str]:
        tx_ids = []
        for i in range(count):
            tx_id = str(uuid.uuid4())
            ingest(client, make_event("payment_initiated", transaction_id=tx_id, merchant_id=merchant_id))
            tx_ids.append(tx_id)
        return tx_ids

    def test_list_returns_paginated_results(self, client):
        # Seed some transactions
        mid = f"m_{uuid.uuid4().hex[:8]}"
        self._seed_transactions(client, mid, 5)

        resp = client.get(f"/transactions?merchant_id={mid}&page=1&page_size=2")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 5
        assert len(data["items"]) == 2
        assert data["page"] == 1

    def test_filter_by_merchant(self, client):
        mid = f"m_{uuid.uuid4().hex[:8]}"
        self._seed_transactions(client, mid, 3)

        resp = client.get(f"/transactions?merchant_id={mid}")
        data = resp.json()
        for item in data["items"]:
            assert item["merchant_id"] == mid

    def test_filter_by_status(self, client):
        tx_id = str(uuid.uuid4())
        ingest(client, make_event("payment_initiated", transaction_id=tx_id))
        ingest(client, make_event("payment_failed", transaction_id=tx_id))

        resp = client.get("/transactions?status=failed")
        data = resp.json()
        for item in data["items"]:
            assert item["status"] == "failed"

    def test_sort_by_amount_asc(self, client):
        mid = f"m_{uuid.uuid4().hex[:8]}"
        for amt in [500.0, 100.0, 9999.0]:
            ingest(client, make_event("payment_initiated", merchant_id=mid, amount=amt))

        resp = client.get(f"/transactions?merchant_id={mid}&sort_by=amount&sort_order=asc")
        amounts = [i["amount"] for i in resp.json()["items"]]
        assert amounts == sorted(amounts)

    def test_invalid_page_size_rejected(self, client):
        resp = client.get("/transactions?page_size=999")
        assert resp.status_code == 422

    def test_pagination_page_two(self, client):
        mid = f"m_{uuid.uuid4().hex[:8]}"
        self._seed_transactions(client, mid, 4)

        p1 = client.get(f"/transactions?merchant_id={mid}&page=1&page_size=2").json()
        p2 = client.get(f"/transactions?merchant_id={mid}&page=2&page_size=2").json()
        ids_p1 = {i["id"] for i in p1["items"]}
        ids_p2 = {i["id"] for i in p2["items"]}
        assert ids_p1.isdisjoint(ids_p2), "Pages should not overlap"



# GET /transactions/{id}


class TestTransactionDetail:
    def test_returns_404_for_unknown(self, client):
        resp = client.get("/transactions/nonexistent-id")
        assert resp.status_code == 404

    def test_returns_merchant_info(self, client):
        tx_id = str(uuid.uuid4())
        ingest(client, make_event("payment_initiated", transaction_id=tx_id, merchant_id="mtest", merchant_name="TestCo"))
        resp = client.get(f"/transactions/{tx_id}")
        data = resp.json()
        assert data["merchant"]["id"] == "mtest"
        assert data["merchant"]["name"] == "TestCo"

    def test_returns_full_event_history(self, client):
        tx_id = str(uuid.uuid4())
        ingest(client, make_event("payment_initiated", transaction_id=tx_id))
        ingest(client, make_event("payment_processed", transaction_id=tx_id))
        ingest(client, make_event("settled", transaction_id=tx_id))

        resp = client.get(f"/transactions/{tx_id}")
        events = resp.json()["events"]
        assert len(events) == 3
        types = {e["event_type"] for e in events}
        assert types == {"payment_initiated", "payment_processed", "settled"}

    def test_duplicate_events_not_in_history(self, client):
        tx_id = str(uuid.uuid4())
        ev = make_event("payment_initiated", transaction_id=tx_id)
        ingest(client, ev)
        ingest(client, ev)  # duplicate

        resp = client.get(f"/transactions/{tx_id}")
        events = resp.json()["events"]
        event_ids = [e["event_id"] for e in events]
        assert len(event_ids) == len(set(event_ids)), "Duplicate events must not appear twice"



# GET /reconciliation/summary


class TestReconciliationSummary:
    def test_group_by_merchant(self, client):
        resp = client.get("/reconciliation/summary?group_by=merchant")
        assert resp.status_code == 200
        data = resp.json()
        assert data["group_by"] == "merchant"
        assert isinstance(data["items"], list)

    def test_group_by_status(self, client):
        resp = client.get("/reconciliation/summary?group_by=status")
        assert resp.status_code == 200
        assert resp.json()["group_by"] == "status"

    def test_group_by_date(self, client):
        resp = client.get("/reconciliation/summary?group_by=date")
        assert resp.status_code == 200

    def test_invalid_group_by_rejected(self, client):
        resp = client.get("/reconciliation/summary?group_by=banana")
        assert resp.status_code == 422

    def test_summary_counts_match_settled_transactions(self, client):
        mid = f"sm_{uuid.uuid4().hex[:6]}"
        tx1 = str(uuid.uuid4())
        tx2 = str(uuid.uuid4())

        ingest(client, make_event("payment_initiated", transaction_id=tx1, merchant_id=mid))
        ingest(client, make_event("payment_processed", transaction_id=tx1, merchant_id=mid))
        ingest(client, make_event("settled", transaction_id=tx1, merchant_id=mid))

        ingest(client, make_event("payment_initiated", transaction_id=tx2, merchant_id=mid))
        ingest(client, make_event("payment_failed", transaction_id=tx2, merchant_id=mid))

        resp = client.get(f"/reconciliation/summary?group_by=merchant&merchant_id={mid}")
        data = resp.json()
        assert len(data["items"]) == 1
        item = data["items"][0]
        assert item["settled"] == 1
        assert item["payment_failed"] == 1
        assert item["total_transactions"] == 2



# GET /reconciliation/discrepancies


class TestReconciliationDiscrepancies:
    def test_discrepancies_endpoint_returns_200(self, client):
        resp = client.get("/reconciliation/discrepancies")
        assert resp.status_code == 200
        data = resp.json()
        assert "total" in data
        assert "items" in data

    def test_failed_then_settled_flagged(self, client):
        """Settlement for a failed payment should show up as a discrepancy."""
        tx_id = str(uuid.uuid4())
        mid = f"disc_{uuid.uuid4().hex[:6]}"

        ingest(client, make_event("payment_initiated", transaction_id=tx_id, merchant_id=mid))
        ingest(client, make_event("payment_failed", transaction_id=tx_id, merchant_id=mid))
        # FSM won't advance status to settled, but the reconciliation
        # record will have payment_status=failed and settlement_status=settled
        # after we ingest the settled event (which is stored but ignored by FSM)
        ingest(client, make_event("settled", transaction_id=tx_id, merchant_id=mid))

        resp = client.get(f"/reconciliation/discrepancies?merchant_id={mid}")
        data = resp.json()
        # Because payment_failed + settled event = discrepancy
        assert data["total"] >= 1
        reasons = [i["discrepancy_reason"] for i in data["items"]]
        assert any("failed" in r.lower() for r in reasons)

    def test_filter_by_merchant(self, client):
        mid = f"disc_m_{uuid.uuid4().hex[:6]}"
        resp = client.get(f"/reconciliation/discrepancies?merchant_id={mid}")
        data = resp.json()
        for item in data["items"]:
            assert item["merchant_id"] == mid

    def test_pagination(self, client):
        resp = client.get("/reconciliation/discrepancies?page=1&page_size=5")
        assert resp.status_code == 200
        assert len(resp.json()["items"]) <= 5
