"""Calendar-driven local-library subscriptions using the existing leased Worker."""

import json
import time
from datetime import datetime, timedelta
from datetime import time as Time
from zoneinfo import ZoneInfo

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from careerpilot import growth, job_policy, market
from careerpilot.domain import DomainError, Principal
from careerpilot.persistence import IdempotencyRecord, User, affected, new_id
from careerpilot.platform_models import BackgroundTask
from careerpilot.safety import payload_hash

SCOPE = "仅增量检查本人已导入的本地岗位库；未搜索外部招聘平台，未实时读取源站。"


def next_occurrence(now, config):
    zone = ZoneInfo(config["timezone"])
    local = datetime.fromtimestamp(now, zone)
    for offset in range(15):
        day = local.date() + timedelta(days=offset)
        if config["frequency"] == "weekly" and day.weekday() != config["weekday"]:
            continue
        candidate = datetime.combine(day, Time(config["hour"]), zone)
        epoch = int(candidate.timestamp())
        roundtrip = datetime.fromtimestamp(epoch, zone)
        # A nonexistent DST wall time is skipped; an ambiguous hour uses its first occurrence.
        if epoch > now and roundtrip.hour == config["hour"] and roundtrip.date() == day:
            return epoch
    raise DomainError("INVALID_SCHEDULE", "无法计算下次执行时间", 422)


def quiet(now, config):
    start, end = config["quiet_start"], config["quiet_end"]
    hour = datetime.fromtimestamp(now, ZoneInfo(config["timezone"])).hour
    return (
        start <= hour < end
        if start < end
        else hour >= start or hour < end
        if start > end
        else False
    )


def next_allowed(now, config):
    if not quiet(now, config):
        return now
    minute = (now // 60 + 1) * 60
    for step in range(26 * 60):
        candidate = minute + step * 60
        if not quiet(candidate, config):
            return candidate
    raise DomainError("INVALID_SCHEDULE", "静默时段无法解析", 422)


def create(db, principal, payload, key, trace, now=None):
    now = int(time.time()) if now is None else now
    growth.lock_owner(db, principal)
    digest = payload_hash({"action": "subscription.create", "payload": payload})
    previous = db.get(IdempotencyRecord, (principal.user_id, principal.tenant_id, key))
    if previous:
        if previous.request_hash != digest:
            raise DomainError("IDEMPOTENCY_CONFLICT", "幂等键已用于不同订阅", 409)
        return growth.data(
            growth.owned(
                db, principal, json.loads(previous.response_json)["id"], "job_subscription"
            )
        )
    result = growth.save(
        db,
        principal,
        "job_subscription",
        {
            **payload,
            "status": "active",
            "configuration_version": 1,
            "next_run_at": next_occurrence(now, payload),
            "last_run_at": None,
            "pending_task_id": None,
            "seen": {},
            "scope": SCOPE,
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


def public(db, principal, row):
    result = growth.data(row)
    result["seen_count"] = len(result.pop("seen"))
    task = db.get(BackgroundTask, result["pending_task_id"]) if result["pending_task_id"] else None
    if task:
        from careerpilot.runtime import task_data

        result["pending_task"] = task_data(task)
    return result


def update_config(db, principal, subscription_id, payload, version, trace, now=None):
    from careerpilot.runtime import cancel

    now = int(time.time()) if now is None else now
    growth.lock_owner(db, principal)
    row = growth.owned(db, principal, subscription_id, "job_subscription")
    before = growth.data(row)
    pending = (
        db.get(BackgroundTask, before["pending_task_id"]) if before["pending_task_id"] else None
    )
    if pending and pending.status in {"queued", "running"}:
        cancel(db, principal, pending.id, pending.version)
    current = {k: v for k, v in before.items() if k not in {"id", "version", "created_at"}}
    changed_scope = any(
        payload.get(k, before[k]) != before[k] for k in ("filters", "favorites_only")
    )
    current.update(payload)
    current.update(
        configuration_version=before["configuration_version"] + 1,
        next_run_at=next_occurrence(now, current),
        pending_task_id=None,
    )
    if changed_scope:
        current.update(seen={}, last_run_at=None)
    return growth.save(db, principal, "job_subscription", current, trace, row, version)


def enqueue_run(db, principal, row, key, trace, now):
    from careerpilot.runtime import enqueue

    config = growth.data(row)
    if config["status"] != "active":
        raise DomainError("SUBSCRIPTION_INACTIVE", "请先恢复订阅", 409)
    request_payload = {
        "subscription_id": row.id,
        "configuration_version": config["configuration_version"],
    }
    previous = db.get(IdempotencyRecord, (principal.user_id, principal.tenant_id, key))
    pending = (
        db.get(BackgroundTask, config["pending_task_id"]) if config["pending_task_id"] else None
    )
    if not previous and pending and pending.status in {"queued", "running"}:
        raise DomainError("RUN_PENDING", "已有待处理的订阅检查", 409)
    task = enqueue(
        db, principal, "market.subscription", key, trace, request_payload=request_payload
    )
    existing = db.get(growth.GrowthRecord, task["id"])
    if existing:
        return growth.data(growth.owned(db, principal, task["id"], "subscription_run"))
    ready = next_allowed(now, config)
    task_row = db.get(BackgroundTask, task["id"])
    task_row.available_at = ready
    growth.save(
        db,
        principal,
        "job_subscription",
        {
            **{k: v for k, v in config.items() if k not in {"id", "version", "created_at"}},
            "pending_task_id": task["id"],
            "next_run_at": next_occurrence(ready, config),
        },
        trace,
        row,
        row.version,
    )
    return growth.save(
        db,
        principal,
        "subscription_run",
        {
            "subscription_id": row.id,
            "configuration_version": config["configuration_version"],
            "status": "queued",
            "available_at": ready,
            "result": None,
            "scope": SCOPE,
        },
        trace,
        record_id=task["id"],
    )


def schedule_due(engine, now=None, limit=10):
    now = int(time.time()) if now is None else now
    with Session(engine) as db:
        candidates = [
            (r.id, r.user_id, r.tenant_id)
            for r in db.scalars(
                select(growth.GrowthRecord)
                .where(growth.GrowthRecord.kind == "job_subscription")
                .order_by(growth.GrowthRecord.created_at, growth.GrowthRecord.id)
            )
            if (config := json.loads(r.payload_json))["status"] == "active"
            and config["next_run_at"] <= now
        ]
    count = 0
    for subscription_id, user_id, tenant_id in candidates:
        with Session(engine) as db, db.begin():
            principal = Principal(user_id, tenant_id)
            if db.get(User, user_id) is None:
                continue
            growth.lock_owner(db, principal)
            row = db.scalar(
                growth.owner_query(principal, "job_subscription").where(
                    growth.GrowthRecord.id == subscription_id
                )
            )
            if row is None:
                continue
            config = growth.data(row)
            if config["status"] != "active" or config["next_run_at"] > now:
                continue
            task = (
                db.get(BackgroundTask, config["pending_task_id"])
                if config["pending_task_id"]
                else None
            )
            if task and task.status in {"queued", "running"}:
                continue
            key = (
                f"subscription:scheduled:{row.id}:"
                f"{config['configuration_version']}:{config['next_run_at']}"
            )
            enqueue_run(db, principal, row, key, new_id(), now)
            count += 1
        if count >= limit:
            break
    return count


def collect(db, principal, config, now):
    policy = job_policy.preferences(db, principal)
    seen = dict(config["seen"])
    changes = []
    today = datetime.fromtimestamp(now, ZoneInfo(config["timezone"])).date().isoformat()
    for row in db.scalars(growth.owner_query(principal, "job").order_by(growth.GrowthRecord.id)):
        job = growth.data(row)
        if job_policy.blocked(job, policy):
            continue
        deadline = job["fields"]["deadline"]["value"]
        state = "deadline_passed" if deadline and deadline < today else job["status"]
        eligible = state not in {"closed", "merged", "possibly_expired", "deadline_passed"}
        matched = (
            eligible
            and market.matches_filter(job, config["filters"])
            and (not config["favorites_only"] or job["id"] in policy["favorite_ids"])
        )
        fingerprint = payload_hash({"text": " ".join(job["content"].split()), "state": state})
        previous = seen.get(job["id"])
        if not previous and not matched:
            continue
        if previous and previous["fingerprint"] == fingerprint:
            # Reversible favorites/policy choices are not changes to a job's content.
            seen[job["id"]] = {"fingerprint": fingerprint, "matched": matched}
            continue
        if not previous or previous["fingerprint"] != fingerprint or previous["matched"] != matched:
            if matched or previous and previous["matched"]:
                change = (
                    ("initial" if config["last_run_at"] is None else "new")
                    if not previous
                    else ("updated" if matched else state if not eligible else "no_longer_matches")
                )
                changes.append(
                    {
                        "job_id": job["id"],
                        "job_version": job["version"],
                        "title": job["fields"]["title"]["value"] or "待核实岗位",
                        "change": change,
                        "status": state,
                        "sources": job["sources"],
                        "fields": job["fields"],
                        "fingerprint": fingerprint,
                    }
                )
            seen[job["id"]] = {"fingerprint": fingerprint, "matched": matched}
    return seen, changes


def handler(engine, task_id, token, now):
    started = time.monotonic()
    with Session(engine) as db, db.begin():
        task = db.get(BackgroundTask, task_id)
        if task is None:
            return
        principal = Principal(task.user_id, task.tenant_id)
        growth.lock_owner(db, principal)
        effective_now = now + int(time.monotonic() - started)
        fence = db.execute(
            update(BackgroundTask)
            .where(
                BackgroundTask.id == task_id,
                BackgroundTask.kind == "market.subscription",
                BackgroundTask.status == "running",
                BackgroundTask.lease_token == token,
                BackgroundTask.lease_until > effective_now,
            )
            .values(checkpoint="local_library", version=BackgroundTask.version + 1)
        )
        if affected(fence) != 1:
            return
        row = growth.owned(db, principal, task_id, "subscription_run")
        run = growth.data(row)
        if run["status"] == "success":
            return
        subscription = growth.owned(db, principal, run["subscription_id"], "job_subscription")
        config = growth.data(subscription)
        if (
            config["status"] != "active"
            or config["configuration_version"] != run["configuration_version"]
        ):
            return
        if quiet(effective_now, config):
            from careerpilot.runtime import emit, record_change

            task.status, task.available_at = "queued", next_allowed(effective_now, config)
            task.lease_token, task.lease_until = "", 0
            task.attempts = max(0, task.attempts - 1)
            record_change(db, task, "running", "quiet_window_deferred")
            emit(db, task, "AgentTaskQueued.v1")
            return
        seen, changes = collect(db, principal, config, effective_now)
        notification = None
        if changes:
            notification = growth.save(
                db,
                principal,
                "job_digest",
                {
                    "subscription_id": subscription.id,
                    "subscription_name": config["name"],
                    "entries": changes,
                    "read": False,
                    "scope": SCOPE,
                    "channel": "in_app",
                    "task_id": task_id,
                    "checked_at": effective_now,
                    "timezone": config["timezone"],
                },
                task.trace_id,
            )
        growth.save(
            db,
            principal,
            "job_subscription",
            {
                **{k: v for k, v in config.items() if k not in {"id", "version", "created_at"}},
                "seen": seen,
                "last_run_at": effective_now,
            },
            task.trace_id,
            subscription,
            subscription.version,
        )
        growth.save(
            db,
            principal,
            "subscription_run",
            {
                **{k: v for k, v in run.items() if k not in {"id", "version", "created_at"}},
                "status": "success",
                "result": {
                    "changes": len(changes),
                    "notification_id": notification["id"] if notification else None,
                },
            },
            task.trace_id,
            row,
            row.version,
        )
        final_fence = db.execute(
            update(BackgroundTask)
            .where(
                BackgroundTask.id == task_id,
                BackgroundTask.lease_token == token,
                BackgroundTask.status == "running",
                BackgroundTask.lease_until > now + int(time.monotonic() - started),
            )
            .values(checkpoint="subscription_committed")
        )
        if affected(final_fence) != 1:
            raise DomainError("LEASE_EXPIRED", "执行租约已过期", 409)
