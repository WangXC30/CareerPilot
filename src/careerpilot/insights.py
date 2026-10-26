"""Local evidence-based scenarios, trend snapshots and conservative risk signals."""

import json
import math
import re
import time
from collections import Counter
from datetime import date, datetime, timezone
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from careerpilot import assessment, catalogue, growth, learning, notifications, subscriptions
from careerpilot.domain import DomainError, Principal
from careerpilot.growth_models import GrowthRecord


def scenarios(db, principal, career_ids, weekly_minutes):
    graph = catalogue.skill_map(db)
    order = catalogue.topological(graph)
    states = {s["skill_id"]: s for s in learning.snapshots(db, principal)}
    careers = {c["id"]: c for c in catalogue.entries(db, "career")}
    result = []
    for key in dict.fromkeys(career_ids):
        if key not in careers:
            raise DomainError("UNKNOWN_CAREER", "职业方向不存在", 422)
        career = careers[key]
        needed = set(career["skills"])

        def expand(skill):
            for parent in graph[skill]["requires"]:
                if parent not in needed:
                    needed.add(parent)
                    expand(parent)

        for skill in list(needed):
            expand(skill)
        pending = [s for s in order if s in needed and states[s]["status"] != "verified"]
        minutes = sum(graph[s]["minutes"] for s in pending)
        result.append(
            {
                "career_id": key,
                "title": career["title"],
                "career_version": career["version"],
                "source_url": career["source_url"],
                "steps": [
                    {
                        "skill_id": s,
                        "name": graph[s]["name"],
                        "minutes": graph[s]["minutes"],
                        "requires": graph[s]["requires"],
                        "satisfied_prerequisites": [
                            parent
                            for parent in graph[s]["requires"]
                            if states[parent]["status"] == "verified"
                        ],
                        "source_url": graph[s]["source_url"],
                        "catalogue_version": graph[s]["version"],
                    }
                    for s in pending
                ],
                "foundation_minutes": minutes,
                "weekly_minutes": weekly_minutes,
                "estimated_weeks": math.ceil(minutes / weekly_minutes),
                "estimated_weeks_range": {
                    "lower": math.ceil(minutes / weekly_minutes),
                    "upper": math.ceil(2 * minutes / weekly_minutes),
                    "basis": "基础词库工时乘以 1～2 的敏感性示例，不是统计置信区间或完成时间预测。",
                },
                "assumptions": [
                    "按词库基础学习工时估算，不包含项目、复习与求职准备。",
                    "仅跳过有当前有效基础测验证据的技能。",
                ],
                "risks": [
                    "不预测薪资、录用概率或达到岗位胜任的时间。",
                    "路线只作比较，不会修改主目标、计划或能力等级。",
                ],
                "paths": [
                    {
                        "id": name,
                        "title": title,
                        "minutes": minutes + extra * len(pending),
                        "additional_practice_minutes": extra * len(pending),
                        "estimated_weeks": math.ceil(
                            (minutes + extra * len(pending)) / weekly_minutes
                        ),
                        "prerequisites": "两条路线都按上述先修顺序，只跳过当前有效的基础验证。",
                        "resources": resources,
                        "monetary_cost": None,
                        "cost_basis": "仅计算编辑工时，未核实课程价格/设备开销，金钱成本未知。",
                        "risk": risk,
                        "source_versions": {s: graph[s]["version"] for s in pending},
                        "assumption": "额外练习按每项 45 分钟的编辑示例估算，不是完成时间预测。",
                    }
                    for name, title, extra, resources, risk in (
                        (
                            "foundation",
                            "A：概念与基础测验",
                            0,
                            "官方资料与本地基础测验",
                            "投入较少，但基础测验不覆盖项目实践或岗位胜任。",
                        ),
                        (
                            "guided_practice",
                            "B：基础加逐项小练习",
                            45,
                            "同一资料加本地小练习；项目验收另行完成",
                            "需要额外练习时间；完成练习仍不代表项目或能力认证。",
                        ),
                    )
                ],
            }
        )
    return result


def local_jobs(db, principal):
    return [
        growth.data(r)
        for r in db.scalars(growth.owner_query(principal, "job"))
        if growth.data(r).get("status") not in {"closed", "merged"}
    ]


def trend_sample(db, principal, keyword):
    jobs = [j for j in local_jobs(db, principal) if keyword.casefold() in j["content"].casefold()]
    counts = Counter(
        s
        for j in jobs
        for s in {s["skill_id"] for s in j["skills"] if s["requirement"] != "mentioned"}
    )
    return {
        "keyword": keyword,
        "count": len(jobs),
        "skills": dict(sorted(counts.items())),
        "sources": [
            {
                "id": j["id"],
                "version": j["version"],
                "content_hash": growth.payload_hash(j["content"]),
            }
            for j in sorted(jobs, key=lambda item: item["id"])
        ],
        "basis": "本人本地导入且未关闭或合并的岗位样本；不代表全市场趋势。",
    }


def create_trend(db, principal, keyword, status, interval_hours, trace):
    growth.lock_owner(db, principal)
    growth.scan_upload(keyword.encode(), "trend.txt")
    active = sum(
        growth.data(row)["status"] == "active"
        for row in db.scalars(growth.owner_query(principal, "trend_subscription"))
    )
    if status == "active" and active >= 20:
        raise DomainError("SUBSCRIPTION_LIMIT", "最多保留 20 项有效本地趋势订阅", 422)
    now = int(time.time())
    return growth.save(
        db,
        principal,
        "trend_subscription",
        {
            "keyword": keyword,
            "status": status,
            "interval_hours": interval_hours,
            "next_check_at": now + interval_hours * 3600 if status == "active" else None,
            "sample": trend_sample(db, principal, keyword),
            "checked_at": datetime.fromtimestamp(now, timezone.utc).isoformat(),
            "changed": False,
            "skill_delta": {},
            "pending_notice": None,
            "message": "初始本地样本，不构成趋势变化。",
        },
        trace,
    )


def trend_refresh(db, principal, key, trace, now=None):
    growth.lock_owner(db, principal)
    row = growth.owned(db, principal, key, "trend_subscription")
    value = growth.data(row)
    if value["status"] != "active":
        raise DomainError("SUBSCRIPTION_PAUSED", "订阅已暂停", 409)
    sample = trend_sample(db, principal, value["keyword"])
    previous = value.get("sample")
    changed = (
        previous is not None
        and sorted(previous["sources"], key=lambda source: source["id"]) != sample["sources"]
    )
    delta = (
        {
            k: sample["skills"].get(k, 0) - previous["skills"].get(k, 0)
            for k in set(sample["skills"]) | set(previous["skills"])
        }
        if changed
        else {}
    )
    payload = {
        "keyword": value["keyword"],
        "status": value["status"],
        "interval_hours": value.get("interval_hours", 24),
        "next_check_at": (int(time.time()) if now is None else now)
        + value.get("interval_hours", 24) * 3600,
        "sample": sample,
        "checked_at": datetime.fromtimestamp(
            int(time.time()) if now is None else now, timezone.utc
        ).isoformat(),
        "changed": changed,
        "skill_delta": delta,
        "pending_notice": {
            "id": str(uuid5(NAMESPACE_URL, f"trend-change:{row.id}:{row.version + 1}")),
            "observed_at": int(time.time()) if now is None else now,
            "sample_count": sample["count"],
            "skill_delta": delta,
            "sources": sample["sources"],
        }
        if changed
        else value.get("pending_notice"),
        "message": "本地样本版本发生变化，请核对来源和分母。"
        if changed
        else "尚无新的样本变化，不生成趋势变化结论。",
    }
    return growth.save(db, principal, row.kind, payload, trace, row, row.version)


def deliver_trend_notice(db, principal, row, now):
    value = growth.data(row)
    pending = value.get("pending_notice")
    if not pending:
        return
    config = notifications.settings(db, principal)
    if subscriptions.quiet(now, config):
        return
    zone = ZoneInfo(config["timezone"])
    day = datetime.fromtimestamp(now, zone).date()
    delivered_today = sum(
        datetime.fromtimestamp(growth.data(notice)["delivered_at"], zone).date() == day
        for notice in db.scalars(growth.owner_query(principal, "personal_notice"))
    )
    if delivered_today >= config["daily_limit"]:
        return
    if db.get(GrowthRecord, pending["id"]) is None:
        growth.save(
            db,
            principal,
            "personal_notice",
            {
                "kind": "trend_change",
                "channel": "in_app",
                "delivered_at": now,
                "scheduled_for": pending["observed_at"],
                "timezone": config["timezone"],
                "read": False,
                "content": {
                    "title": "本人岗位样本变化",
                    "trend": {**pending, "keyword": value["keyword"], "subscription_id": row.id},
                    "notice": "仅本人本地样本的变化，不代表市场趋势；核对来源和样本数后再判断。",
                },
            },
            "trend-scheduler",
            record_id=pending["id"],
        )
    growth.save(
        db,
        principal,
        row.kind,
        {**json.loads(row.payload_json), "pending_notice": None},
        "trend-scheduler",
        row,
        row.version,
    )


def schedule_trends(engine, now=None):
    """Refresh only the owner's local samples, at most once per configured interval."""
    now = int(time.time()) if now is None else now
    with Session(engine) as db:
        keys = list(
            db.scalars(select(GrowthRecord.id).where(GrowthRecord.kind == "trend_subscription"))
        )
    refreshed = 0
    for key in keys:
        with Session(engine) as db, db.begin():
            row = db.get(GrowthRecord, key)
            if row is None:
                continue
            principal = Principal(row.user_id, row.tenant_id)
            growth.lock_owner(db, principal)
            db.refresh(row)
            value = growth.data(row)
            if value["status"] != "active":
                continue
            if value.get("next_check_at", 0) <= now:
                trend_refresh(db, principal, key, "trend-scheduler", now)
                refreshed += 1
            deliver_trend_notice(db, principal, row, now)
    return refreshed


def risk_signals(job):
    signals = []
    patterns = [
        ("advance_payment", r"押金|保证金|培训贷|先交费|先缴费"),
        ("identity_material", r"身份证原件|扣押证件|银行卡密码|验证码"),
        ("unverifiable_promise", r"保录用|保证录用|零门槛高薪|无需面试"),
    ]
    for key, pattern in patterns:
        for match in re.finditer(pattern, job["content"]):
            start, end = max(0, match.start() - 15), min(len(job["content"]), match.end() + 25)
            signals.append(
                {
                    "signal": key,
                    "quote": job["content"][start:end],
                    "start": start,
                    "end": end,
                    "job_version": job["version"],
                }
            )
    return {
        "job_id": job["id"],
        "signals": signals,
        "notice": (
            "规则命中仅提示核查，可能包含否定或引用；不是诈骗认定或法律结论。无命中不证明安全。"
        ),
    }


def timeline(db, principal):
    result = []
    for job in local_jobs(db, principal):
        field = job["fields"].get("deadline", {})
        raw = field.get("value")
        try:
            due = date.fromisoformat(raw) if raw and field.get("evidence") else None
        except ValueError:
            due = None
        result.append(
            {
                "job_id": job["id"],
                "title": job["fields"]["title"]["value"],
                "deadline": due.isoformat() if due else None,
                "deadline_evidence": field.get("evidence") if due else None,
                "cohort": job["fields"].get("graduation_cohort"),
                "risk": risk_signals(job),
            }
        )
    return sorted(result, key=lambda r: (r["deadline"] is None, r["deadline"] or "", r["job_id"]))


def multi_target(db, principal, career_ids):
    careers = {c["id"]: c for c in catalogue.entries(db, "career")}
    jobs = local_jobs(db, principal)
    result = []
    profile = growth.profile(db, principal)
    zone = growth.data(profile).get("timezone", "Asia/Shanghai") if profile else "Asia/Shanghai"
    today = datetime.now(ZoneInfo(zone)).date()
    for key in dict.fromkeys(career_ids):
        if key not in careers:
            raise DomainError("UNKNOWN_CAREER", "职业方向不存在", 422)
        rows = []
        for job in jobs:
            skills = {s["skill_id"] for s in job["skills"] if s["requirement"] != "mentioned"}
            overlap = sorted(skills & set(careers[key]["skills"]))
            deadline = job["fields"].get("deadline", {})
            try:
                days = (
                    (date.fromisoformat(deadline["value"]) - today).days
                    if deadline.get("value") and deadline.get("evidence")
                    else None
                )
            except ValueError:
                days = None
            rows.append(
                {
                    "job_id": job["id"],
                    "job_version": job["version"],
                    "title": job["fields"]["title"]["value"],
                    "overlap": overlap,
                    "coverage": len(overlap) / len(careers[key]["skills"]),
                    "deadline_days": days,
                    "deadline_evidence": deadline.get("evidence"),
                    "cold_start": not bool(profile),
                    "eligibility": "UNKNOWN",
                    "notice": "方向覆盖仅作排序，不替代硬约束匹配；缺资料不自动排除。",
                }
            )
        result.append(
            {
                "career_id": key,
                "title": careers[key]["title"],
                "jobs": sorted(rows, key=lambda r: (-r["coverage"], r["job_id"])),
            }
        )
    return result


def teach(db, principal, body, trace):
    from careerpilot import tutoring

    growth.lock_owner(db, principal)
    skill = catalogue.require_skill(db, body["skill_id"])
    bank = assessment.BANK.get(body["skill_id"])
    if not bank or body["question_index"] >= len(bank):
        raise DomainError("QUESTION_UNAVAILABLE", "当前词库没有这道题", 422)
    tutoring.quota(db, principal)
    question, _, _, explanation = bank[body["question_index"]]
    hints = [
        "先描述这道题需要区分的概念，并举一个你熟悉的例子。",
        f"对照 {skill['name']} 官方资料，列出两个可能答案的差异，再判断题目条件。",
        explanation + " 请用自己的话说明原因，并构造一个反例。",
    ]
    return growth.save(
        db,
        principal,
        "teaching_turn",
        {
            "skill_id": body["skill_id"],
            "question_index": body["question_index"],
            "question": question,
            "level": body["level"],
            "hint": hints[body["level"] - 1],
            "source_url": skill["source_url"],
            "catalogue_version": skill["version"],
            "bank_version": assessment.BANK_VERSION,
            "student_attempt": body["attempt"],
            "notice": "本地分层教学提示；不自动评分或改变能力等级。",
        },
        trace,
    )
