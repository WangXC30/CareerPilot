"""Atomic, once-only in-app reminders; no outbound transport or external submission."""

import json
import time
from datetime import datetime
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from careerpilot import applications, growth, subscriptions
from careerpilot.domain import DomainError, Principal
from careerpilot.persistence import IdempotencyRecord, User
from careerpilot.safety import payload_hash


def due_at(body):
    wall = datetime.fromisoformat(body["local_time"])
    zone = ZoneInfo(body["timezone"])
    local = wall.replace(tzinfo=zone, fold=0)
    epoch = int(local.timestamp())
    if datetime.fromtimestamp(epoch, zone).replace(tzinfo=None) != wall:
        raise DomainError("INVALID_LOCAL_TIME", "夏令时跳过的本地时间不存在，请重新选择", 422)
    return epoch


def validate(db, principal, application, body, now):
    if application["status"] in applications.TERMINAL:
        raise DomainError("APPLICATION_CLOSED", "已结束的申请不能配置提醒", 409)
    due = due_at(body)
    if due <= now:
        raise DomainError("INVALID_SCHEDULE", "提醒时间必须晚于当前时间", 422)
    if body["kind"] == "deadline":
        job = growth.data(growth.owned(db, principal, application["job"]["id"], "job"))
        deadline = job["fields"]["deadline"]["value"]
        if not deadline or body["local_time"][:10] > deadline:
            raise DomainError("DEADLINE_REQUIRED", "截止提醒需有明确截止日期且不晚于该日期", 422)
        ready = subscriptions.next_allowed(due, body)
        if datetime.fromtimestamp(ready, ZoneInfo(body["timezone"])).date().isoformat() > deadline:
            raise DomainError(
                "QUIET_AFTER_DEADLINE", "静默调整后的提醒已晚于截止日期，请选择更早时间", 422
            )
    return due


def create(db, principal, application_id, body, key, trace, now=None):
    now = int(time.time()) if now is None else now
    growth.lock_owner(db, principal)
    digest = payload_hash(
        {"action": "reminder.create", "application": application_id, "body": body}
    )
    previous = db.get(IdempotencyRecord, (principal.user_id, principal.tenant_id, key))
    if previous:
        if previous.request_hash != digest:
            raise DomainError("IDEMPOTENCY_CONFLICT", "幂等键已用于不同提醒", 409)
        return growth.data(
            growth.owned(
                db, principal, json.loads(previous.response_json)["id"], "application_reminder"
            )
        )
    application = growth.data(growth.owned(db, principal, application_id, "job_application"))
    due = validate(db, principal, application, body, now)
    result = growth.save(
        db,
        principal,
        "application_reminder",
        {
            **body,
            "application_id": application_id,
            "due_at": due,
            "status": "active",
            "configuration_version": 1,
            "delivered_at": None,
            "channel": "in_app",
            "next_run_at": subscriptions.next_allowed(due, body),
        },
        trace,
    )
    db.add(
        IdempotencyRecord(
            user_id=principal.user_id,
            tenant_id=principal.tenant_id,
            key=key,
            request_hash=digest,
            response_json=json.dumps({"id": result["id"]}),
        )
    )
    return result


def configure(db, principal, reminder_id, body, expected, trace, now=None):
    now = int(time.time()) if now is None else now
    growth.lock_owner(db, principal)
    row = growth.owned(db, principal, reminder_id, "application_reminder")
    old = growth.data(row)
    if row.version != expected:
        raise DomainError("VERSION_CONFLICT", "提醒配置已改变，请刷新", 412)
    if old["status"] in {"cancelled", "delivered", "suppressed"}:
        raise DomainError("REMINDER_FINISHED", "此提醒已结束，请另建提醒", 409)
    status = body.pop("status")
    result = {
        **applications.payload(old),
        **body,
        "status": status,
        "configuration_version": old["configuration_version"] + 1,
    }
    if status == "active":
        application = growth.data(
            growth.owned(db, principal, old["application_id"], "job_application")
        )
        result["due_at"] = validate(db, principal, application, result, now)
        result["next_run_at"] = subscriptions.next_allowed(result["due_at"], result)
    return growth.save(db, principal, "application_reminder", result, trace, row, expected)


def schedule_due(engine, now=None, limit=20):
    """Delivery and configuration update commit together; crashes roll both back."""
    now = int(time.time()) if now is None else now
    with Session(engine) as db:
        candidates = [
            (r.id, r.user_id, r.tenant_id)
            for r in db.scalars(
                select(growth.GrowthRecord)
                .where(growth.GrowthRecord.kind == "application_reminder")
                .order_by(growth.GrowthRecord.created_at, growth.GrowthRecord.id)
            )
            if (item := growth.data(r))["status"] == "active" and item["next_run_at"] <= now
        ][:limit]
    count = 0
    for reminder_id, user_id, tenant_id in candidates:
        with Session(engine) as db, db.begin():
            principal = Principal(user_id, tenant_id)
            if db.get(User, user_id) is None:
                continue
            growth.lock_owner(db, principal)
            row = db.scalar(
                growth.owner_query(principal, "application_reminder").where(
                    growth.GrowthRecord.id == reminder_id
                )
            )
            if row is None:
                continue
            item = growth.data(row)
            if item["status"] != "active" or item["next_run_at"] > now:
                continue
            result = applications.payload(item)
            if subscriptions.quiet(now, item):
                result["next_run_at"] = subscriptions.next_allowed(now, item)
            else:
                application = growth.data(
                    growth.owned(db, principal, item["application_id"], "job_application")
                )
                if application["status"] in applications.TERMINAL:
                    result["status"] = "suppressed"
                else:
                    job = growth.data(growth.owned(db, principal, application["job"]["id"], "job"))
                    key = str(
                        uuid5(NAMESPACE_URL, f"reminder:{row.id}:{item['configuration_version']}")
                    )
                    growth.save(
                        db,
                        principal,
                        "application_notice",
                        {
                            "reminder_id": row.id,
                            "application_id": application["id"],
                            "kind": item["kind"],
                            "job": application["job"],
                            "job_current_status": job["status"],
                            "job_changed": job["version"] != application["job"]["version"],
                            "message": "请核对当前岗位状态和申请安排；源站操作由本人完成。",
                            "due_at": item["due_at"],
                            "delivered_at": now,
                            "timezone": item["timezone"],
                            "read": False,
                            "channel": "in_app",
                        },
                        "reminder.delivery",
                        record_id=key,
                    )
                    result.update(status="delivered", delivered_at=now)
                    count += 1
            growth.save(
                db, principal, "application_reminder", result, "reminder.schedule", row, row.version
            )
    return count
