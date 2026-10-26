"""Local metadata retention, without touching business/audit histories."""

import argparse
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from careerpilot.persistence import affected, database_url, make_engine
from careerpilot.platform_models import QuotaBucket, RequestMetric


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--metrics-days", type=int, default=30)
    args = parser.parse_args()
    if args.metrics_days < 1:
        raise ValueError("Retention must be at least one day")
    cutoff = (datetime.now(timezone.utc) - timedelta(days=args.metrics_days)).isoformat()
    engine = make_engine(database_url())
    try:
        with Session(engine) as db, db.begin():
            metrics = affected(
                db.execute(delete(RequestMetric).where(RequestMetric.created_at < cutoff))
            )
            # Minute source buckets are no longer needed after 24 hours.
            cutoff_minute = int(time.time()) // 60 - 1440
            buckets = db.scalars(
                select(QuotaBucket).where(QuotaBucket.scope.like("source:%"))
            ).all()
            for bucket in buckets:
                if int(bucket.scope.rsplit(":", 1)[-1]) < cutoff_minute:
                    db.delete(bucket)
        print(f"Removed {metrics} expired request metrics; private business records unchanged")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
