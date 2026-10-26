import argparse
import logging
import signal
import time
from pathlib import Path

from sqlalchemy.exc import InterfaceError, OperationalError

from careerpilot.accounts import process_deletion
from careerpilot.analytics import purge_expired as purge_analytics
from careerpilot.application_lab import recover_uncertain
from careerpilot.content_subscriptions import schedule as schedule_content
from careerpilot.hiring_reminders import schedule_due as schedule_reminders
from careerpilot.insights import schedule_trends
from careerpilot.media import purge_expired as purge_media
from careerpilot.notifications import schedule_due as schedule_notifications
from careerpilot.persistence import database_url, make_engine
from careerpilot.runtime import run_once
from careerpilot.source_library import purge_expired
from careerpilot.subscriptions import schedule_due

HEARTBEAT = Path("/tmp/careerpilot-worker-heartbeat")


def cycle(engine, last_source_cleanup):
    if time.monotonic() - last_source_cleanup > 60:
        recover_uncertain(engine)
        purge_analytics(engine)
        purge_expired(engine)
        purge_media(engine)
        schedule_trends(engine)
        schedule_content(engine)
        last_source_cleanup = time.monotonic()
    process_deletion(engine)
    schedule_due(engine)
    schedule_reminders(engine)
    schedule_notifications(engine)
    run_once(engine)
    return last_source_cleanup


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    engine = make_engine(database_url())
    stopped = False
    last_source_cleanup = 0.0

    def stop(*_):
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        HEARTBEAT.unlink(missing_ok=True)
        while not stopped:
            try:
                last_source_cleanup = cycle(engine, last_source_cleanup)
            except (OperationalError, InterfaceError):
                HEARTBEAT.unlink(missing_ok=True)
                logging.getLogger("careerpilot.worker").warning("worker_database_unavailable")
                if args.once:
                    return 1
                time.sleep(1)
                continue
            except Exception:
                HEARTBEAT.unlink(missing_ok=True)
                logging.getLogger("careerpilot.worker").error("worker_iteration_failed")
                return 1
            HEARTBEAT.touch()
            if args.once:
                break
            time.sleep(1)
        return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
