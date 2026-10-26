"""Subscriptions over authorized local references only; no background web crawling."""

import json
import time
from datetime import datetime
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from careerpilot import growth, media, notifications, source_library, subscriptions
from careerpilot.domain import DomainError, Principal
from careerpilot.growth_models import GrowthRecord


def sample(db, principal, keyword):
    query = keyword.strip().casefold()
    values = [(v["id"], v["version"]) for v in media.listing(db, principal, keyword)]
    for value in source_library.listing(db, principal):
        if query in (value.get("title", "") + " " + value.get("text", "")).casefold():
            values.append((value["id"], value["version"]))
    return sorted(values)


def create(db, principal, keyword, hours, trace):
    growth.lock_owner(db, principal)
    keyword = keyword.strip()
    if not keyword:
        raise DomainError("EMPTY_KEYWORD", "关键词不能为空", 422)
    growth.scan_upload(keyword.encode(), "keyword.txt")
    if (
        sum(
            growth.data(r)["status"] == "active"
            for r in db.scalars(growth.owner_query(principal, "content_subscription"))
        )
        >= 20
    ):
        raise DomainError("SUBSCRIPTION_LIMIT", "最多 20 项有效资料订阅", 422)
    return growth.save(
        db,
        principal,
        "content_subscription",
        {
            "keyword": keyword,
            "interval_hours": hours,
            "status": "active",
            "snapshot": sample(db, principal, keyword),
            "pending": None,
            "next_check_at": int(time.time()) + hours * 3600,
            "notice": (
                "仅核对本人有权保留且未到期的本地摘录与媒体索引；不抓取源站。"
                "沿用成长通知的时区、静默与每日上限。"
            ),
        },
        trace,
    )


def cancel(db, principal, key, expected, trace):
    growth.lock_owner(db, principal)
    row = growth.owned(db, principal, key, "content_subscription")
    return growth.save(
        db,
        principal,
        row.kind,
        {
            **json.loads(row.payload_json),
            "status": "cancelled",
            "pending": None,
            "snapshot": [],
            "next_check_at": None,
        },
        trace,
        row,
        expected,
    )


def schedule(engine, now=None):
    now = int(time.time()) if now is None else now
    with Session(engine) as db:
        keys = list(
            db.scalars(select(GrowthRecord.id).where(GrowthRecord.kind == "content_subscription"))
        )
    delivered = 0
    for key in keys:
        with Session(engine) as db, db.begin():
            row = db.get(GrowthRecord, key)
            if row is None:
                continue
            principal = Principal(row.user_id, row.tenant_id)
            growth.lock_owner(db, principal)
            db.refresh(row)
            value = json.loads(row.payload_json)
            if value["status"] != "active":
                continue
            current = sample(db, principal, value["keyword"])
            changed = current != [tuple(v) for v in value["snapshot"]]
            # Deleted/expired excerpts are never copied into notices, even while quiet.
            if value.get("pending") and not current:
                value["pending"] = None
            if value["next_check_at"] <= now:
                if changed:
                    previous = {tuple(v) for v in value["snapshot"]}
                    if any(v not in previous for v in current):
                        value["pending"] = {
                            "id": str(uuid5(NAMESPACE_URL, f"content:{key}:{row.version + 1}")),
                            "observed_at": now,
                        }
                    value["snapshot"] = current
                value["next_check_at"] = now + value["interval_hours"] * 3600
            config = notifications.settings(db, principal)
            zone = ZoneInfo(config["timezone"])
            day = datetime.fromtimestamp(now, zone).date()
            count = sum(
                datetime.fromtimestamp(growth.data(r)["delivered_at"], zone).date() == day
                for r in db.scalars(growth.owner_query(principal, "personal_notice"))
            )
            pending = value.get("pending")
            if (
                pending
                and current
                and not subscriptions.quiet(now, config)
                and count < config["daily_limit"]
            ):
                if db.get(GrowthRecord, pending["id"]) is None:
                    growth.save(
                        db,
                        principal,
                        "personal_notice",
                        {
                            "kind": "content_change",
                            "channel": "in_app",
                            "delivered_at": now,
                            "scheduled_for": pending["observed_at"],
                            "timezone": config["timezone"],
                            "read": False,
                            "content": {
                                "title": "本人授权资料索引有更新",
                                "notice": ("请到资料索引检索并核对来源；通知不复制原文。"),
                            },
                        },
                        "content-scheduler",
                        record_id=pending["id"],
                    )
                    delivered += 1
                value["pending"] = None
            if value != json.loads(row.payload_json):
                growth.save(db, principal, row.kind, value, "content-scheduler", row, row.version)
    return delivered
