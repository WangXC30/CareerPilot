"""Idempotent, non-personal development source registry and dictionaries."""

import json
import time
from typing import Any

from sqlalchemy.orm import Session

from careerpilot.catalogue import seed_catalogue
from careerpilot.persistence import database_url, make_engine
from careerpilot.platform_models import SourceRecord

SOURCES: list[dict[str, Any]] = [
    {
        "id": "public_docs",
        "access_basis": "bounded excerpts from official public learning documentation",
        "scopes_json": '["fetch","search"]',
        "rate_per_minute": 20,
        "retention_days": 30,
        "reviewed_by": "V1 official documentation allowlist; user consent required",
        "enabled": 1,
        "status": "ready",
    },
    {
        "id": "user_import",
        "access_basis": "user-provided text; local processing only",
        "scopes_json": '["import"]',
        "rate_per_minute": 60,
        "retention_days": 30,
        "reviewed_by": "V0 local import policy v1",
        "enabled": 1,
        "status": "ready",
    },
    {"id": "github", "access_basis": "official API review pending"},
    {
        "id": "github_metadata",
        "access_basis": "GET public repository metadata only; docs/source-review-v2.md",
        "scopes_json": '["metadata"]',
        "rate_per_minute": 1,
        "retention_days": 30,
        "reviewed_by": "V2 bounded public metadata technical review 2026-10-21",
        "enabled": 1,
        "status": "ready",
        "responsibility": "source_library",
    },
    {"id": "recruitment", "access_basis": "provider authorization pending"},
    {"id": "xiaohongshu", "access_basis": "no authorized adapter; manual links only"},
    {"id": "douyin", "access_basis": "no authorized adapter; manual links only"},
]


def seed(engine):
    with Session(engine) as db, db.begin():
        seed_catalogue(db)
        for spec in SOURCES:
            if db.get(SourceRecord, spec["id"]) is None:
                extra = (
                    {"reviewed_until": int(time.time()) + 365 * 86400}
                    if spec["id"] in {"user_import", "public_docs", "github_metadata"}
                    else {}
                )
                db.add(SourceRecord(**spec, **extra))


if __name__ == "__main__":
    engine = make_engine(database_url())
    seed(engine)
    engine.dispose()
    print(json.dumps({"sources_seeded": len(SOURCES), "private_demo_data": False}))
