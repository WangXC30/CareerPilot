"""Trusted operator bridge. Invoke via Compose exec; never expose to HTTP."""

import argparse
import json
import sys

from sqlalchemy.orm import Session

from careerpilot import projects
from careerpilot.persistence import database_url, make_engine


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["claim", "complete"])
    args = parser.parse_args()
    engine = make_engine(database_url())
    try:
        with Session(engine) as db, db.begin():
            if args.action == "claim":
                result = projects.claim(db)
                if result:
                    result["cases"] = projects.CASES
            else:
                body = json.loads(sys.stdin.buffer.read(65537))
                result = projects.complete(
                    db, body["assessment_id"], body["lease_token"], body["result"]
                )
            print(json.dumps(result, ensure_ascii=True))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
