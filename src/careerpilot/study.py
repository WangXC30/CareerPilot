"""Reviewable study pace and deterministic, evidence-backed review queues."""

import copy
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from careerpilot import catalogue, growth, learning, projects
from careerpilot.domain import DomainError

MODES = {"sprint": 100, "balanced": 75, "research_busy": 50, "light": 25}
INTERVALS = (1, 3, 7, 14, 30)


def mode_preview(db, principal, plan_id, mode, expected, key, trace):
    def make():
        row = growth.owned(db, principal, plan_id, "plan")
        current = growth.data(row)
        if row.version != expected:
            raise DomainError("VERSION_CONFLICT", "计划已变化，请刷新", 412)
        if current["status"] != "active":
            raise DomainError("ACTIVE_PLAN_REQUIRED", "请先发布学习计划", 409)
        profile = learning.profile_context(db, principal, current)
        budget = max(1, profile["weekly_minutes"] * MODES[mode] // 100)
        candidate = {
            k: copy.deepcopy(v)
            for k, v in current.items()
            if k not in {"id", "version", "created_at"}
        }
        candidate.update(study_mode=mode, mode_budget_minutes=budget)
        fixed = learning.protected_tasks(current)
        fixed_ids = {t["id"] for t in fixed}
        fixed += [
            copy.deepcopy(t)
            for t in current["tasks"]
            if t["id"] not in fixed_ids and t["status"] in {"running", "skipped"}
        ]
        fixed_ids = {t["id"] for t in fixed}
        pending = [copy.deepcopy(t) for t in current["tasks"] if t["id"] not in fixed_ids]
        tasks = learning.split_for_budget(pending, budget)
        ranks = {t["id"]: i for i, t in enumerate(tasks)}
        frequency = {
            s["skill_id"]: s["factors"]["job_frequency"]
            for s in learning.gap(db, principal, current["job_ids"])["skills"]
        }
        ordered, done = [], set(fixed_ids)
        while tasks:
            ready = [t for t in tasks if set(t["requires"]) <= done]
            if not ready:
                raise DomainError("INVALID_PLAN_DAG", "计划依赖无法排序", 422)

            def priority(t):
                if mode == "sprint":
                    return (-frequency.get(t["skill_id"], 0), ranks[t["id"]])
                if mode in {"research_busy", "light"}:
                    return (t["minutes"], ranks[t["id"]])
                return (ranks[t["id"]], 0)

            task = min(ready, key=priority)
            ordered.append(task)
            done.add(task["id"])
            tasks.remove(task)
        today = datetime.now(ZoneInfo(profile["timezone"])).date().isoformat()
        error = None
        try:
            candidate["tasks"] = learning.schedule(
                ordered, max(today, current["start_date"]), budget, fixed
            )
            candidate["validation"] = learning.validate_plan(
                candidate,
                profile["weekly_minutes"],
                catalogue.skill_map(db),
                learning.protected_tasks(current),
            )
        except DomainError as exc:
            error = {"code": exc.code, "message": exc.message}
        before = {t["id"]: t for t in current["tasks"]}
        return {
            "plan_id": plan_id,
            "base_version": row.version,
            "reason": "StudyModeChanged",
            "status": "pending",
            "context_hash": learning.context_hash(db, principal),
            "candidate": candidate,
            "validation_error": error,
            "mode_change": {
                "mode": mode,
                "before_budget": learning.plan_budget(current, profile["weekly_minutes"]),
                "after_budget": budget,
                "profile_budget": profile["weekly_minutes"],
                "priority": "岗位覆盖优先"
                if mode == "sprint"
                else "短任务优先"
                if mode in {"light", "research_busy"}
                else "原先修顺序",
            },
            "diff": {
                "added": [t for t in candidate["tasks"] if t["id"] not in before],
                "removed": [],
                "changed": [
                    {"before": before[t["id"]], "after": t}
                    for t in candidate["tasks"]
                    if t["id"] in before and t != before[t["id"]]
                ],
            },
        }

    growth.lock_owner(db, principal)
    return projects.once(
        db,
        principal,
        "plan_preview",
        key,
        {"action": "study.mode", "plan_id": plan_id, "mode": mode, "version": expected},
        trace,
        make,
    )


def review_queue(db, principal, now=None, timezone=None):
    profile = growth.profile(db, principal)
    zone = ZoneInfo(timezone or (growth.data(profile)["timezone"] if profile else "Asia/Shanghai"))
    today = (datetime.now(zone) if now is None else datetime.fromtimestamp(now, zone)).date()
    grouped: dict[str, list] = {}
    for e in learning.ledger(db, principal):
        if e["kind"] == "assessment_result" and e["scope"] == "foundation" and not e["revoked"]:
            grouped.setdefault(e["skill_id"], []).append(e)
    result = []
    for key, records in grouped.items():
        latest = records[-1]
        # Repeated attempts on one local day do not accelerate the review interval.
        days = set()
        for e in reversed(records):
            if e["conclusion"] != "passed":
                break
            days.add(datetime.fromisoformat(e["created_at"]).astimezone(zone).date())
        interval = (
            INTERVALS[min(max(len(days) - 1, 0), len(INTERVALS) - 1)]
            if latest["conclusion"] == "passed"
            else 1
        )
        assessed = datetime.fromisoformat(latest["created_at"]).astimezone(zone).date()
        due = assessed + timedelta(days=interval)
        attempt = growth.data(growth.owned(db, principal, latest["source_id"], "assessment"))
        questions = {q["id"]: q for q in attempt.get("questions", [])}
        wrong = [
            {**f, "question": questions.get(f["question_id"], {})}
            for f in (attempt.get("result") or {}).get("feedback", [])
            if not f["correct"]
        ]
        result.append(
            {
                "skill_id": key,
                "name": catalogue.require_skill(db, key)["name"],
                "assessment_id": attempt["id"],
                "evidence_id": latest["id"],
                "conclusion": latest["conclusion"],
                "interval_days": interval,
                "due_date": due.isoformat(),
                "due": due <= today,
                "risk": "overdue" if due < today else "due" if due == today else "scheduled",
                "wrong_questions": wrong,
                "rule_version": "spaced-review.v1",
            }
        )
    return {
        "today": today.isoformat(),
        "timezone": str(zone),
        "items": sorted(result, key=lambda x: (x["due_date"], x["skill_id"])),
        "notice": (
            "复习按未撤销测验历史以 1/3/7/14/30 天安排，同日重复通过不延长间隔；"
            "逾期是排程提示，不是遗忘概率。完成复习需重新测验，不提供能力打卡升级。"
        ),
    }
