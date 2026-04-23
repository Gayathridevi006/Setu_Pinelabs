import argparse
import json
import sys
import time
from pathlib import Path

# Allow running from the project root
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.database import Base, SessionLocal, engine
from app.models import Merchant, PaymentEvent, ReconciliationRecord, Transaction  # noqa: F401 ensure models are loaded
from app.schemas import EventIngest
from app.services.events import ingest_event

BATCH_SIZE = 500


def seed(file_path: Path) -> None:
    Base.metadata.create_all(bind=engine)

    with file_path.open() as f:
        raw_events = json.load(f)

    total = len(raw_events)
    print(f"Seeding {total} events from {file_path} ...")

    created = duplicates = errors = 0
    start = time.time()

    db = SessionLocal()
    try:
        for i, raw in enumerate(raw_events, 1):
            try:
                event = EventIngest(**raw)
                result = ingest_event(db, event)
                if result.status == "created":
                    created += 1
                else:
                    duplicates += 1
            except Exception as exc:
                errors += 1
                print(f"  [ERROR] event {raw.get('event_id', '?')}: {exc}")

            if i % BATCH_SIZE == 0:
                elapsed = time.time() - start
                rate = i / elapsed
                print(f"  {i}/{total} processed ({rate:.0f} events/s) | created={created} dupes={duplicates} errors={errors}")
    finally:
        db.close()

    elapsed = time.time() - start
    print(f"\nDone in {elapsed:.1f}s — created={created} duplicates={duplicates} errors={errors}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed database with payment events")
    parser.add_argument(
        "--file",
        default=str(Path(__file__).parent.parent / "sample_events.json"),
        help="Path to events JSON file",
    )
    args = parser.parse_args()
    seed(Path(args.file))
