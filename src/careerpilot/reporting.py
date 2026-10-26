"""Explicit self-reported time and reproducible, owned period statistics."""

from datetime import date, datetime, timedelta
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo

from careerpilot import growth, job_policy, learning
from careerpilot.domain import DomainError
from careerpilot.safety import payload_hash, scan_upload

RULE = "period-report.v1"


def user_timezone(db, principal):
    row = growth.profile(db, principal)
    return growth.data(row)["timezone"] if row else "Asia/Shanghai"


def body(item):
    return {k: v for k, v in item.items() if k not in {"id", "version", "created_at"}}


def local_day(timestamp, zone):
    return datetime.fromisoformat(timestamp).astimezone(ZoneInfo(zone)).date()


def range_dates(start, end):
    a, b = date.fromisoformat(start), date.fromisoformat(end)
    if a > b or (b - a).days > 365:
        raise DomainError("INVALID_PERIOD", "请选择起止顺序正确且不超过 366 天的范围", 422)
    return a, b


def save_log(db, principal, args, key, trace):
    growth.lock_owner(db, principal)
    timezone = user_timezone(db, principal)
    if date.fromisoformat(args["day"]) > datetime.now(ZoneInfo(timezone)).date():
        raise DomainError("FUTURE_STUDY_LOG", "不能记录未来已经学习的时长", 422)
    if args["plan_id"]:
        plan = growth.data(growth.owned(db, principal, args["plan_id"], "plan"))
        if not any(t["id"] == args["task_id"] for t in plan["tasks"]):
            raise DomainError("UNKNOWN_TASK", "请选择此计划内的学习任务", 422)
    elif args["task_id"]:
        raise DomainError("PLAN_REQUIRED", "记录任务时需同时指定所属计划", 422)
    scan_upload(args["note"].encode(), "study-note.txt")
    record_id = str(
        uuid5(NAMESPACE_URL, f"study-log:{principal.tenant_id}:{principal.user_id}:{key}")
    )
    row = db.get(growth.GrowthRecord, record_id)
    request_hash = payload_hash(args)
    if row:
        old = growth.data(growth.owned(db, principal, record_id, "study_log"))
        if old["request_hash"] != request_hash:
            raise DomainError("IDEMPOTENCY_CONFLICT", "幂等键已用于不同学习记录", 409)
        return old
    daily = sum(
        growth.data(r)["minutes"]
        for r in db.scalars(growth.owner_query(principal, "study_log"))
        if growth.data(r)["day"] == args["day"] and not growth.data(r)["withdrawn"]
    )
    if daily + args["minutes"] > 1440:
        raise DomainError("DAILY_TIME_EXCEEDED", "当天本人记录的时长不能超过 1440 分钟", 422)
    return growth.save(
        db,
        principal,
        "study_log",
        {
            **args,
            "timezone": timezone,
            "basis": "user_reported_not_independently_timed",
            "withdrawn": False,
            "request_hash": request_hash,
        },
        trace,
        record_id=record_id,
    )


def withdraw_log(db, principal, record_id, expected, trace):
    growth.lock_owner(db, principal)
    row = growth.owned(db, principal, record_id, "study_log")
    item = growth.data(row)
    if item["withdrawn"]:
        raise DomainError("LOG_WITHDRAWN", "记录已经撤回", 409)
    return growth.save(
        db, principal, "study_log", {**body(item), "withdrawn": True}, trace, row, expected
    )


def report(db, principal, start, end, timezone=None):
    a, b = range_dates(start, end)
    zone = timezone or user_timezone(db, principal)
    observation_end = min(b, datetime.now(ZoneInfo(zone)).date())

    def included(timestamp):
        return a <= local_day(timestamp, zone) <= b

    logs = [
        growth.data(r)
        for r in db.scalars(growth.owner_query(principal, "study_log"))
        if not growth.data(r)["withdrawn"] and start <= growth.data(r)["day"] <= end
    ]
    plans = [growth.data(r) for r in db.scalars(growth.owner_query(principal, "plan"))]
    current = next((p for p in plans if p["status"] == "active"), None)
    scheduled = [t for t in current["tasks"] if start <= t["date"] <= end] if current else []
    first_completions: dict[str, dict] = {}
    for plan in plans:
        seen = set()
        for revision in reversed(growth.history(db, principal, plan["id"], "plan")):
            for task in revision["payload"]["tasks"]:
                if task["status"] == "completed" and task["id"] not in seen:
                    seen.add(task["id"])
                    previous_completion = first_completions.get(task["id"])
                    if (
                        previous_completion is None
                        or revision["created_at"] < previous_completion["completed_at"]
                    ):
                        first_completions[task["id"]] = {
                            "task_id": task["id"],
                            "plan_id": plan["id"],
                            "plan_version": revision["version"],
                            "completed_at": revision["created_at"],
                        }
    completed = {
        key: item for key, item in first_completions.items() if included(item["completed_at"])
    }
    evidence = [e for e in learning.ledger(db, principal) if included(e["created_at"])]
    changes: list[dict] = []
    for row in db.scalars(growth.owner_query(principal, "job")):
        previous = None
        for revision in reversed(growth.history(db, principal, row.id, "job")):
            payload = revision["payload"]
            signature = payload_hash({k: payload.get(k) for k in ("content", "status")})
            if previous is not None and previous != signature and included(revision["created_at"]):
                changes.append(
                    {
                        "job_id": row.id,
                        "version": revision["version"],
                        "at": revision["created_at"],
                        "sources": payload.get("sources", []),
                    }
                )
            previous = signature
    applications = [
        growth.data(r)
        for r in db.scalars(growth.owner_query(principal, "job_application"))
        if included(r.created_at)
    ]
    stages: dict[str, list[str]] = {"created": [], "submitted": [], "interviewing": [], "offer": []}
    statuses: dict[str, int] = {}
    unknown = []
    for application in applications:
        revisions = [
            r
            for r in growth.history(db, principal, application["id"], "job_application")
            if local_day(r["created_at"], zone) <= observation_end
        ]
        if not revisions:
            continue
        stages["created"].append(application["id"])
        states = {r["payload"]["status"] for r in revisions}
        submitted = any(r["payload"].get("receipt") for r in revisions)
        if submitted:
            stages["submitted"].append(application["id"])
            for stage, state_set in [
                ("interviewing", {"interviewing", "offer"}),
                ("offer", {"offer"}),
            ]:
                if states & state_set:
                    stages[stage].append(application["id"])
        else:
            unknown.append(application["id"])
        status = revisions[0]["payload"]["status"]
        statuses[status] = statuses.get(status, 0) + 1
    counts = {k: len(v) for k, v in stages.items()}
    ratios = []
    for before, after in [
        ("created", "submitted"),
        ("submitted", "interviewing"),
        ("interviewing", "offer"),
    ]:
        ratios.append(
            {
                "from": before,
                "to": after,
                "numerator": counts[after],
                "denominator": counts[before],
                "ratio": counts[after] / counts[before] if counts[before] else None,
            }
        )
    return {
        "rule_version": RULE,
        "generated_at": growth.utc_now(),
        "period": {"start": start, "end": end, "timezone": zone, "inclusive": True},
        "learning": {
            "log_day_basis": "学习日志按记录时的本地日历日期归属；仅有日期的记录不跨时区换算",
            "self_reported_minutes": sum(r["minutes"] for r in logs),
            "time_basis": "本人明确记录，未经独立计时；撤回记录不计入，计划分钟不折算为学习时长",
            "log_ids": [r["id"] for r in logs],
            "current_planned_minutes": sum(t["minutes"] for t in scheduled),
            "schedule_basis": "当前已发布计划的日期安排，不代表历史预算或实际投入",
            "completed_tasks": list(completed.values()),
            "overdue_pending": [
                t["id"]
                for t in scheduled
                if t["date"] < datetime.now(ZoneInfo(zone)).date().isoformat()
                and t["status"] not in {"completed", "skipped"}
            ],
        },
        "evidence": {
            "new_records": evidence,
            "currently_verified_foundation": [
                s["skill_id"]
                for s in learning.snapshots(db, principal)
                if s["status"] == "verified"
            ],
            "basis": (
                "新增证据按评估时间筛选；撤销/有效性及当前能力状态以生成时为准，非历史状态还原"
            ),
        },
        "job_changes": changes,
        "funnel": {
            "observation_end": observation_end.isoformat(),
            "counts": counts,
            "application_ids": stages,
            "ratios": ratios,
            "statuses": statuses,
            "unsubmitted_or_unconfirmed_ids": unknown,
            "basis": (
                "时间范围内创建的申请为同一队列，观察至范围末日或生成时的较早者；"
                "按申请记录去重，重投是不同申请；回执为本人确认，未经源站独立核实"
            ),
            "current_favorites": len(job_policy.preferences(db, principal)["favorite_ids"]),
            "favorite_basis": "生成时的当前收藏，独立口径，不混作申请转化分母",
        },
        "notice": "报表只读取本人记录，不执行岗位搜索、投递或能力认证。",
    }


def previous_period(now, timezone, kind):
    today = datetime.fromtimestamp(now, ZoneInfo(timezone)).date()
    if kind == "weekly_report":
        end = today - timedelta(days=today.weekday() + 1)
        start = end - timedelta(days=6)
    else:
        end = today.replace(day=1) - timedelta(days=1)
        start = end.replace(day=1)
    return start.isoformat(), end.isoformat()
