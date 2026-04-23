import json
import random
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

random.seed(42)

MERCHANTS = [
    ("merchant_1", "QuickMart"),
    ("merchant_2", "FreshBasket"),
    ("merchant_3", "TechZone"),
    ("merchant_4", "StyleHub"),
    ("merchant_5", "FoodExpress"),
]

CURRENCIES = ["INR"] * 9 + ["USD"]  # 90% INR


def ts(base: datetime, delta_minutes: int) -> str:
    return (base + timedelta(minutes=delta_minutes)).isoformat()


def make_event(
    event_id: str,
    event_type: str,
    transaction_id: str,
    merchant_id: str,
    merchant_name: str,
    amount: float,
    currency: str,
    timestamp: str,
) -> dict:
    return {
        "event_id": event_id,
        "event_type": event_type,
        "transaction_id": transaction_id,
        "merchant_id": merchant_id,
        "merchant_name": merchant_name,
        "amount": amount,
        "currency": currency,
        "timestamp": timestamp,
    }


def generate_events(target: int = 10_500) -> list[dict]:
    events: list[dict] = []
    base_date = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def add_days(n: int) -> datetime:
        return base_date + timedelta(days=n)

    txn_index = 0
    day_offset = 0

    while len(events) < target:
        merchant_id, merchant_name = random.choice(MERCHANTS)
        amount = round(random.uniform(100, 50_000), 2)
        currency = random.choice(CURRENCIES)
        tx_id = str(uuid.uuid4())
        tx_base = add_days(day_offset % 120) + timedelta(
            hours=random.randint(0, 23), minutes=random.randint(0, 59)
        )

        scenario = random.choices(
            ["success", "failed", "pending", "discrepant", "duplicate"],
            weights=[55, 20, 15, 5, 5],
        )[0]

        init_eid = str(uuid.uuid4())
        init_event = make_event(
            init_eid, "payment_initiated", tx_id,
            merchant_id, merchant_name, amount, currency,
            ts(tx_base, 0),
        )
        events.append(init_event)

        if scenario == "success":
            events.append(make_event(
                str(uuid.uuid4()), "payment_processed", tx_id,
                merchant_id, merchant_name, amount, currency,
                ts(tx_base, random.randint(1, 10)),
            ))
            events.append(make_event(
                str(uuid.uuid4()), "settled", tx_id,
                merchant_id, merchant_name, amount, currency,
                ts(tx_base, random.randint(60, 480)),
            ))

        elif scenario == "failed":
            events.append(make_event(
                str(uuid.uuid4()), "payment_failed", tx_id,
                merchant_id, merchant_name, amount, currency,
                ts(tx_base, random.randint(1, 5)),
            ))

        elif scenario == "pending":
            # processed but never settled
            events.append(make_event(
                str(uuid.uuid4()), "payment_processed", tx_id,
                merchant_id, merchant_name, amount, currency,
                ts(tx_base, random.randint(1, 10)),
            ))

        elif scenario == "discrepant":
            # failed then settled — data anomaly
            events.append(make_event(
                str(uuid.uuid4()), "payment_failed", tx_id,
                merchant_id, merchant_name, amount, currency,
                ts(tx_base, 2),
            ))
            events.append(make_event(
                str(uuid.uuid4()), "settled", tx_id,
                merchant_id, merchant_name, amount, currency,
                ts(tx_base, 30),
            ))

        elif scenario == "duplicate":
            # Re-submit the same initiated event 1-3 times
            events.append(make_event(
                str(uuid.uuid4()), "payment_processed", tx_id,
                merchant_id, merchant_name, amount, currency,
                ts(tx_base, 5),
            ))
            events.append(make_event(
                str(uuid.uuid4()), "settled", tx_id,
                merchant_id, merchant_name, amount, currency,
                ts(tx_base, 120),
            ))
            # Duplicate initiated event
            for _ in range(random.randint(1, 3)):
                events.append(make_event(
                    init_eid, "payment_initiated", tx_id,
                    merchant_id, merchant_name, amount, currency,
                    ts(tx_base, 0),
                ))

        txn_index += 1
        day_offset += 1

    return events


if __name__ == "__main__":
    out_path = Path(__file__).parent.parent / "sample_events.json"
    events = generate_events()
    out_path.write_text(json.dumps(events, indent=2))
    print(f"Generated {len(events)} events → {out_path}")
