"""Opt-in bounded telemetry; only fixed-window, thresholded aggregates leave owner scope."""

import hashlib
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from careerpilot import growth, operations
from careerpilot.growth_models import GrowthRecord, GrowthRevision
from careerpilot.persistence import CareerGoal
from careerpilot.platform_models import Consent

EVENTS = {
    "learning_completed",
    "assessment_completed",
    "project_completed",
    "application_recorded",
    "effective_growth_week",
}
VERSION = "server-actions.v2"


def today():
    return datetime.now(timezone.utc).date()


def enabled(db, principal):
    row = db.get(
        Consent,
        (principal.user_id, principal.tenant_id, "product_analytics"),
        populate_existing=True,
    )
    return bool(row and row.granted)


def group(principal):
    return (
        "B"
        if int(
            hashlib.sha256(("local-experiment.v1:" + principal.user_id).encode()).hexdigest()[:8],
            16,
        )
        % 2
        else "A"
    )


def record(db, principal, event, trace, *, trusted=False):
    if not trusted or event not in EVENTS:
        return {"collected": False, "reason": "server_actions_only"}
    growth.lock_owner(db, principal)
    if not enabled(db, principal):
        return {"collected": False, "reason": "consent_required"}
    day = today()
    period = day - timedelta(days=day.weekday()) if event == "effective_growth_week" else day
    key = str(
        uuid5(
            NAMESPACE_URL, f"{VERSION}:{principal.tenant_id}:{principal.user_id}:{period}:{event}"
        )
    )
    if db.get(GrowthRecord, key) is None:
        growth.save(
            db,
            principal,
            "analytics_event",
            {
                "day": day.isoformat(),
                "period": period.isoformat(),
                "event": event,
                "arm": group(principal),
                "experiment": "local-experiment.v1",
                "collection_version": VERSION,
            },
            trace,
            record_id=key,
        )
    return {
        "collected": True,
        "arm": group(principal),
        "notice": "每日同类型最多一条；仅用于本地实验。",
    }


def verified_action(db, principal, skill_ids, trace):
    """Conservative EGW subset: a passed server assessment linked to an active goal plan."""
    if not enabled(db, principal):
        return
    for row in db.scalars(growth.owner_query(principal, "plan")):
        value = growth.data(row)
        goal = db.get(CareerGoal, value.get("goal_id")) if value.get("goal_id") else None
        if (
            value.get("status") == "active"
            and goal
            and goal.status == "active"
            and goal.user_id == principal.user_id
            and goal.tenant_id == principal.tenant_id
            and any(task.get("skill_id") in skill_ids for task in value.get("tasks", []))
        ):
            record(db, principal, "effective_growth_week", trace, trusted=True)
            return


def owner_summary(db, principal):
    week = today() - timedelta(days=today().weekday())
    values = [
        growth.data(row) for row in db.scalars(growth.owner_query(principal, "analytics_event"))
    ]
    return {
        "enabled": enabled(db, principal),
        "week_start": week.isoformat(),
        "timezone": "UTC",
        "effective_growth_week": any(
            v.get("collection_version") == VERSION
            and v.get("event") == "effective_growth_week"
            and v.get("period") == week.isoformat()
            for v in values
        ),
        "notice": (
            "本地保守口径：本人有当前目标及关联计划，并通过关联技能的服务端测验或代码评测；"
            "签到、自报完成、失败或系统错误不计入。分组不改变功能，不代表因果收益。"
        ),
    }


def purge(db, principal):
    ids = list(
        db.scalars(
            growth.owner_query(principal, "analytics_event").with_only_columns(GrowthRecord.id)
        )
    )
    if ids:
        db.execute(delete(GrowthRevision).where(GrowthRevision.record_id.in_(ids)))
        db.execute(delete(GrowthRecord).where(GrowthRecord.id.in_(ids)))


def purge_expired(engine):
    """Keep opt-in event rows only long enough for monthly local comparisons."""
    cutoff = (today() - timedelta(days=90)).isoformat()
    with Session(engine) as db:
        ids = [
            row.id
            for row in db.scalars(
                select(GrowthRecord).where(GrowthRecord.kind == "analytics_event")
            )
            if growth.data(row)["day"] < cutoff
        ]
        if ids:
            db.execute(delete(GrowthRevision).where(GrowthRevision.record_id.in_(ids)))
            db.execute(delete(GrowthRecord).where(GrowthRecord.id.in_(ids)))
            db.commit()
    return len(ids)


def aggregate(db, principal):
    operations.require_role(principal)
    # Fixed completed calendar month prevents arbitrary drill-down and differencing by date filters.
    end = today().replace(day=1)
    start = (end - timedelta(days=1)).replace(day=1)
    buckets = defaultdict(set)
    for row in db.scalars(
        select(GrowthRecord).where(
            GrowthRecord.kind == "analytics_event", GrowthRecord.tenant_id == principal.tenant_id
        )
    ):
        value = growth.data(row)
        consent = db.get(Consent, (row.user_id, row.tenant_id, "product_analytics"))
        if (
            consent
            and consent.granted
            and value.get("collection_version") == VERSION
            and start.isoformat() <= value["day"] < end.isoformat()
        ):
            buckets[(value["event"], value["arm"])].add(row.user_id)
    cells = []
    for event in sorted(EVENTS):
        for arm in ("A", "B"):
            count = len(buckets[(event, arm)])
            cells.append(
                {
                    "event": event,
                    "arm": arm,
                    "participants_rounded": (count // 5) * 5 if count >= 10 else None,
                    "suppressed": count < 10,
                }
            )
    return {
        "start": start.isoformat(),
        "end_exclusive": end.isoformat(),
        "cells": cells,
        "minimum_cohort": 10,
        "experiment": "local-experiment.v1",
        "notice": (
            "固定上月、每格至少 10 人、人数向下取整到 5；只展示聚合，不提供个人或自由筛选。"
            "不是严格差分隐私，仍须控制访问。"
        ),
    }
