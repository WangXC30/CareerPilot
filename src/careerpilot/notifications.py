"""Opt-in in-app schedules with atomic delivery, quiet hours and bounded daily volume."""

import time
from datetime import date, datetime, timedelta
from datetime import time as Time
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from careerpilot import growth, learning, reporting, subscriptions
from careerpilot.domain import DomainError, Principal

KINDS = ("daily_learning", "deadline_check", "weekly_report", "monthly_report", "spaced_review")
DEFAULTS = {
    "status": "paused",
    "timezone": "Asia/Shanghai",
    "hour": 9,
    "weekday": 0,
    "month_day": 1,
    "quiet_start": 22,
    "quiet_end": 8,
    "daily_limit": 4,
    **{k: False for k in KINDS},
    "channel": "in_app",
}


def settings(db, principal):
    row = db.scalar(growth.owner_query(principal, "notification_settings"))
    return (
        {**DEFAULTS, **growth.data(row)}
        if row
        else {"version": 0, **DEFAULTS, "next_runs": {}, "email_available": False}
    )


def next_run(now, config, kind):
    if kind in {"daily_learning", "deadline_check", "weekly_report", "spaced_review"}:
        return subscriptions.next_occurrence(
            now, {**config, "frequency": "weekly" if kind == "weekly_report" else "daily"}
        )
    local = datetime.fromtimestamp(now, ZoneInfo(config["timezone"]))
    for offset in range(3):
        month_index = local.year * 12 + local.month - 1 + offset
        candidate = datetime(
            month_index // 12,
            month_index % 12 + 1,
            config["month_day"],
            config["hour"],
            tzinfo=ZoneInfo(config["timezone"]),
        )
        roundtrip = datetime.fromtimestamp(candidate.timestamp(), ZoneInfo(config["timezone"]))
        if (
            candidate.timestamp() > now
            and roundtrip.hour == config["hour"]
            and roundtrip.date() == candidate.date()
        ):
            return int(candidate.timestamp())
    raise DomainError("INVALID_SCHEDULE", "无法计算下次月报时间", 422)


def save_settings(db, principal, args, expected, trace, now=None):
    now = int(time.time()) if now is None else now
    growth.lock_owner(db, principal)
    row = db.scalar(growth.owner_query(principal, "notification_settings"))
    return growth.save(
        db,
        principal,
        "notification_settings",
        {
            **args,
            "next_runs": {
                kind: next_run(now, args, kind)
                for kind in KINDS
                if args.get(kind, False) and args["status"] == "active"
            },
            "channel": "in_app",
            "email_available": False,
            "scope": (
                "本页日学习/间隔复习/截止检查/周月报通知；趋势订阅单独启停，沿用此处时区、静默和上限；"
                "岗位订阅和申请提醒沿用各自开关与静默配置"
            ),
        },
        trace,
        row,
        expected,
    )


def content(db, principal, kind, now, config):
    day = datetime.fromtimestamp(now, ZoneInfo(config["timezone"])).date()
    if kind == "spaced_review":
        from careerpilot.study import review_queue

        queue = review_queue(db, principal, now, config["timezone"])
        return {
            "title": "到期知识复习",
            "items": [i for i in queue["items"] if i["due"]],
            "notice": queue["notice"],
        }
    if kind in {"weekly_report", "monthly_report"}:
        start, end = reporting.previous_period(now, config["timezone"], kind)
        return {
            "title": "上周成长周报" if kind == "weekly_report" else "上月成长月报",
            "report": reporting.report(db, principal, start, end, config["timezone"]),
        }
    if kind == "daily_learning":
        plan = learning.active_plan(db, principal)
        tasks = (
            [
                t
                for t in growth.data(plan)["tasks"]
                if t["date"] == day.isoformat() and t["status"] not in {"completed", "skipped"}
            ]
            if plan
            else []
        )
        return {
            "title": "今日学习提醒",
            "tasks": tasks,
            "notice": "安排分钟仅为计划，不计作已经学习时长。",
        }
    jobs = []
    for row in db.scalars(growth.owner_query(principal, "job")):
        item = growth.data(row)
        field = item["fields"].get("deadline", {})
        deadline, evidence = field.get("value"), field.get("evidence")
        if (
            deadline
            and evidence
            and item["status"] not in {"closed", "merged"}
            and item["content"][evidence["start"] : evidence["end"]] == evidence["quote"]
            and deadline in evidence["quote"]
            and day <= date.fromisoformat(deadline) <= day + timedelta(days=7)
        ):
            jobs.append(
                {
                    "job_id": row.id,
                    "version": row.version,
                    "deadline": deadline,
                    "title": item["fields"]["title"]["value"],
                    "sources": item["sources"],
                    "status": item["status"],
                }
            )
    return {
        "title": "未来七天岗位截止核对",
        "jobs": jobs,
        "notice": "只检查本人岗位库，未访问源站；无日期岗位不会被假定为有效。",
    }


def schedule_due(engine, now=None, limit=30):
    now = int(time.time()) if now is None else now
    with Session(engine) as db:
        ids = [
            (r.id, Principal(r.user_id, r.tenant_id))
            for r in db.scalars(
                select(growth.GrowthRecord).where(
                    growth.GrowthRecord.kind == "notification_settings"
                )
            )
            if (s := growth.data(r))["status"] == "active"
            and any(due <= now for due in s["next_runs"].values())
        ][:limit]
    delivered = 0
    for record_id, principal in ids:
        with Session(engine) as db, db.begin():
            growth.lock_owner(db, principal)
            row = db.scalar(
                growth.owner_query(principal, "notification_settings").where(
                    growth.GrowthRecord.id == record_id
                )
            )
            if row is None:
                continue
            config = growth.data(row)
            if config["status"] != "active":
                continue
            next_runs = dict(config["next_runs"])
            zone = ZoneInfo(config["timezone"])
            day = datetime.fromtimestamp(now, zone).date()
            delivered_today = sum(
                datetime.fromtimestamp(growth.data(r)["delivered_at"], zone).date() == day
                for r in db.scalars(growth.owner_query(principal, "personal_notice"))
            )
            changed = False
            for kind, due in config["next_runs"].items():
                if due > now:
                    continue
                changed = True
                if subscriptions.quiet(now, config):
                    next_runs[kind] = subscriptions.next_allowed(now, config)
                    continue
                if delivered_today >= config["daily_limit"]:
                    tomorrow = datetime.combine(day + timedelta(days=1), Time(config["hour"]), zone)
                    next_runs[kind] = subscriptions.next_allowed(int(tomorrow.timestamp()), config)
                    continue
                key = str(uuid5(NAMESPACE_URL, f"notice:{principal.user_id}:{kind}:{due}"))
                if (
                    kind == "spaced_review"
                    and not content(db, principal, kind, now, config)["items"]
                ) or (
                    kind == "deadline_check"
                    and not content(db, principal, kind, now, config)["jobs"]
                ):
                    next_runs[kind] = next_run(now, config, kind)
                    continue
                if db.get(growth.GrowthRecord, key) is None:
                    growth.save(
                        db,
                        principal,
                        "personal_notice",
                        {
                            "kind": kind,
                            "channel": "in_app",
                            "delivered_at": now,
                            "scheduled_for": due,
                            "read": False,
                            "timezone": config["timezone"],
                            "content": content(db, principal, kind, now, config),
                        },
                        "notification-schedule",
                        record_id=key,
                    )
                    delivered += 1
                    delivered_today += 1
                next_runs[kind] = next_run(now, config, kind)
            if changed:
                growth.save(
                    db,
                    principal,
                    "notification_settings",
                    {**reporting.body(config), "next_runs": next_runs},
                    "notification-schedule",
                    row,
                    row.version,
                )
    return delivered


def listing(db, principal):
    result = []
    for kind in ("personal_notice", "job_digest", "application_notice"):
        result += [
            {**growth.data(r), "record_kind": kind}
            for r in db.scalars(growth.owner_query(principal, kind))
        ]
    return sorted(result, key=lambda r: (r["created_at"], r["id"]), reverse=True)


def mark_read(db, principal, record_id, expected, trace):
    growth.lock_owner(db, principal)
    row = growth.owned(db, principal, record_id, "personal_notice")
    return growth.save(
        db,
        principal,
        "personal_notice",
        {**reporting.body(growth.data(row)), "read": True},
        trace,
        row,
        expected,
    )
