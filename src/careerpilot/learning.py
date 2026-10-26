"""Deterministic skill evidence, constrained plans and reviewable replanning."""

import copy
import json
from collections import Counter
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select

from careerpilot import catalogue, growth
from careerpilot.application import audit
from careerpilot.domain import DomainError
from careerpilot.persistence import new_id, utc_now
from careerpilot.v1_models import EvidenceRevocation, SkillEvidence


def rows(db, principal, model):
    return select(model).where(
        model.user_id == principal.user_id, model.tenant_id == principal.tenant_id
    )


def evidence_data(row):
    return {
        column.name: getattr(row, column.name)
        for column in SkillEvidence.__table__.columns
        if column.name not in {"user_id", "tenant_id"}
    }


def ledger(db, principal):
    revoked = {r.evidence_id: r for r in db.scalars(rows(db, principal, EvidenceRevocation))}
    return [
        {
            **evidence_data(r),
            "revoked": r.id in revoked,
            "revocation_reason": revoked[r.id].reason if r.id in revoked else None,
        }
        for r in db.scalars(
            rows(db, principal, SkillEvidence).order_by(SkillEvidence.created_at, SkillEvidence.id)
        )
    ]


def snapshots(db, principal):
    evidence = ledger(db, principal)
    memories = [
        growth.data(r)
        for r in db.scalars(growth.owner_query(principal, "memory"))
        if not json.loads(r.payload_json).get("superseded_by")
    ]
    now = utc_now()
    result = []
    for skill in catalogue.entries(db, "skill"):
        records = [
            e for e in evidence if e["skill_id"] == skill["id"] and e["scope"] == "foundation"
        ]
        valid = [e for e in records if not e["revoked"] and e["expires_at"] > now]
        exams = [
            e for e in valid if e["kind"] == "assessment_result" and e["scope"] == "foundation"
        ]
        status = "unassessed"
        if exams:
            status = "verified" if exams[-1]["conclusion"] == "passed" else "needs_reassessment"
        elif records:
            if any(e["kind"] == "assessment_result" for e in records):
                status = "needs_reassessment"
            else:
                status = (
                    "supported"
                    if any(e["kind"] == "learning_completion" for e in valid)
                    else "self_report"
                )
        memory_ids = [
            m["id"]
            for m in memories
            if any(a.casefold() in m["text"].casefold() for a in skill["aliases"])
        ]
        if status == "unassessed" and memory_ids:
            status = "self_report"
        result.append(
            {
                "skill_id": skill["id"],
                "name": skill["name"],
                "status": status,
                "scope": "foundation",
                "as_of": now,
                "state_rule_version": "evidence-state.v1",
                "evidence_ids": [e["id"] for e in records],
                "memory_ids": memory_ids,
                "assessed_at": exams[-1]["created_at"] if exams else None,
                "expires_at": exams[-1]["expires_at"] if exams else None,
            }
        )
    return result


def add_evidence(
    db, principal, skill_id, kind, source_id, source_version, conclusion, trace, rubric_version=""
):
    catalogue.require_skill(db, skill_id)
    row = SkillEvidence(
        user_id=principal.user_id,
        tenant_id=principal.tenant_id,
        skill_id=skill_id,
        kind=kind,
        source_id=source_id,
        source_version=source_version,
        conclusion=conclusion,
        rubric_version=rubric_version,
        expires_at=(datetime.now(timezone.utc) + timedelta(days=90)).isoformat(),
    )
    db.add(row)
    db.flush()
    audit(db, principal, row.id, "evidence.appended", 1, trace)
    return evidence_data(row)


def gap(db, principal, job_ids):
    graph = catalogue.skill_map(db)
    source_map: dict[str, list] = {}
    for job_id in dict.fromkeys(job_ids):
        job = growth.data(growth.owned(db, principal, job_id, "job"))
        if job.get("status") in {"closed", "merged"}:
            continue
        for requirement in job["skills"]:
            key = requirement["skill_id"]
            if key not in graph:
                continue
            source_map.setdefault(key, []).append(
                {
                    "job_id": job_id,
                    "job_version": job["version"],
                    "requirement": requirement["requirement"],
                    "evidence": requirement["evidence"],
                }
            )
    goals = growth.goal_snapshot(db, principal)
    active = next((g for g in goals if g["status"] == "active"), None)
    career = next(
        (c for c in catalogue.entries(db, "career") if active and c["title"] == active["title"]),
        None,
    )
    if career:
        for skill_id in career["skills"]:
            source_map.setdefault(skill_id, []).append(
                {
                    "career_id": career["id"],
                    "career_version": career["version"],
                    "requirement": "career_path",
                }
            )
    if not source_map:
        raise DomainError("REQUIREMENTS_REQUIRED", "请选择含已收录技能的 JD 或职业目标", 422)

    def prerequisites(key):
        for parent in graph[key]["requires"]:
            if parent not in source_map:
                source_map[parent] = [{"prerequisite_of": key, "requirement": "prerequisite"}]
                prerequisites(parent)

    for key in list(source_map):
        prerequisites(key)
    states = {s["skill_id"]: s for s in snapshots(db, principal)}
    result = []
    for key, sources in source_map.items():
        factors = {
            "job_frequency": len({s["job_id"] for s in sources if "job_id" in s}),
            "required": int(any(s["requirement"] == "required" for s in sources)),
            "prerequisite_count": len(graph[key]["requires"]),
            "estimated_minutes": graph[key]["minutes"],
            "goal_alignment": int(any("career_id" in s for s in sources)),
        }
        score = (
            factors["job_frequency"] * 3 + factors["required"] * 4 + factors["goal_alignment"] * 2
        )
        score += 120 / graph[key]["minutes"]
        result.append(
            {
                **states[key],
                "sources": sources,
                "factors": factors,
                "priority": round(score, 3),
                "requires": graph[key]["requires"],
            }
        )
    return {
        "weight_version": "priority.v1",
        "catalogue_versions": {k: v["version"] for k, v in graph.items()},
        "goal_id": active["id"] if active else None,
        "skills": sorted(result, key=lambda s: (-s["priority"], s["skill_id"])),
    }


def profile_context(db, principal):
    profile = growth.profile(db, principal)
    if profile is None:
        raise DomainError("PROFILE_REQUIRED", "请先确认学生档案", 422)
    payload = growth.data(profile)
    if payload["weekly_minutes"] <= 0:
        raise DomainError("BUDGET_REQUIRED", "每周学习预算必须大于零", 422)
    return payload


def week(day):
    value = date.fromisoformat(day)
    return (value - timedelta(days=value.weekday())).isoformat()


def plan_budget(payload, maximum):
    return min(maximum, payload.get("mode_budget_minutes", maximum))


def split_for_budget(tasks, budget):
    result = []
    for original in tasks:
        task = copy.deepcopy(original)
        while task["minutes"] > budget:
            segment = {
                **copy.deepcopy(task),
                "id": new_id(),
                "minutes": budget,
                "verification": "learning_record",
            }
            result.append(segment)
            task["minutes"] -= budget
            task["requires"] = [segment["id"]]
        result.append(task)
    return result


def validate_plan(payload, budget, graph, protected=None):
    budget = plan_budget(payload, budget)
    by_id = {t["id"]: t for t in payload["tasks"]}
    if len(by_id) != len(payload["tasks"]):
        raise DomainError("INVALID_PLAN", "任务 ID 重复", 422)
    totals: Counter = Counter()
    visiting, seen = set(), set()

    def visit(task_id):
        if task_id in visiting or task_id not in by_id:
            raise DomainError("INVALID_PLAN_DAG", "任务依赖存在环或缺失", 422)
        if task_id in seen:
            return
        visiting.add(task_id)
        task = by_id[task_id]
        if (
            task["skill_id"] not in graph
            or type(task["minutes"]) is not int
            or task["minutes"] <= 0
        ):
            raise DomainError("INVALID_PLAN", "任务技能或耗时无效", 422)
        for dependency in task["requires"]:
            visit(dependency)
            if by_id[dependency]["date"] > task["date"]:
                raise DomainError("DEPENDENCY_CONFLICT", "先修任务不能排在后续任务之后", 422)
        visiting.remove(task_id)
        seen.add(task_id)

    for task in payload["tasks"]:
        visit(task["id"])
        if task["date"] < payload["start_date"]:
            raise DomainError("DATE_CONFLICT", "任务不能安排在计划开始之前", 422)
        if (
            not task.get("resources")
            or not task.get("definition_of_done")
            or not task.get("verification")
        ):
            raise DomainError("INCOMPLETE_TASK", "任务必须包含资源和验收方式", 422)
        if task["status"] != "skipped":
            totals[week(task["date"])] += task["minutes"]
        if payload.get("deadline") and task["date"] > payload["deadline"]:
            raise DomainError("DEADLINE_CONFLICT", "计划无法在截止日期前完成", 422)
    if any(total > budget for total in totals.values()):
        raise DomainError("BUDGET_EXCEEDED", "周计划超出学习预算，请调整任务或档案预算", 422)
    # Each prerequisite must have a verified snapshot or a preceding task in the DAG.
    for task in payload["tasks"]:
        ancestors = set()

        def collect(task_id):
            for parent in by_id[task_id]["requires"]:
                if parent not in ancestors:
                    ancestors.add(parent)
                    collect(parent)

        collect(task["id"])
        covered = {by_id[k]["skill_id"] for k in ancestors} | set(
            payload.get("verified_skills", [])
        )
        if not set(graph[task["skill_id"]]["requires"]) <= covered:
            raise DomainError("PREREQUISITE_REQUIRED", "计划缺少先修学习或有效能力证明", 422)
    for task in protected or []:
        if by_id.get(task["id"]) != task:
            raise DomainError("LOCKED_TASK", "调整不能覆盖锁定或已完成任务", 422)
    return {"weekly_minutes": dict(totals), "budget_minutes": budget, "valid": True}


def schedule(tasks, start_date, budget, fixed=None):
    fixed = fixed or []
    totals: Counter = Counter()
    dates = {t["id"]: t["date"] for t in fixed}
    for task in fixed:
        if task["status"] != "skipped":
            totals[week(task["date"])] += task["minutes"]
    day = date.fromisoformat(start_date)
    for task in tasks:
        minimum = max([start_date] + [dates[r] for r in task["requires"] if r in dates])
        day = max(day, date.fromisoformat(minimum))
        for _ in range(730):
            if totals[week(day.isoformat())] + task["minutes"] <= budget:
                break
            day += timedelta(days=1)
        else:
            raise DomainError("PLAN_HORIZON", "预算不足以生成两年内可执行计划", 422)
        task["date"] = day.isoformat()
        totals[week(task["date"])] += task["minutes"]
        dates[task["id"]] = task["date"]
        day += timedelta(days=1)
    return fixed + tasks


def build_plan(db, principal, job_ids, start_date, deadline=None):
    profile = profile_context(db, principal)
    diagnosis = gap(db, principal, job_ids)
    graph = catalogue.skill_map(db)
    required = {s["skill_id"] for s in diagnosis["skills"] if s["status"] != "verified"}
    verified = [s["skill_id"] for s in snapshots(db, principal) if s["status"] == "verified"]
    previous: dict[str, str] = {}
    tasks = []
    for key in catalogue.topological(graph):
        if key not in required:
            continue
        minutes = graph[key]["minutes"]
        dependencies = [previous[p] for p in graph[key]["requires"] if p in previous]
        segment = 0
        while minutes:
            duration = min(90, profile["weekly_minutes"], minutes)
            segment += 1
            task = {
                "id": new_id(),
                "skill_id": key,
                "title": f"{graph[key]['name']} 基础学习 · {segment}",
                "minutes": duration,
                "requires": list(dependencies),
                "locked": False,
                "status": "pending",
                "resources": ["resource-" + key],
                "outcome": "",
                "definition_of_done": "完成官方材料中的练习并记录过程与结果",
                "verification": "objective_quiz:foundation.v1"
                if minutes == duration
                else "learning_record",
            }
            tasks.append(task)
            previous[key] = task["id"]
            dependencies = [task["id"]]
            minutes -= duration
    payload = {
        "status": "draft",
        "goal_id": diagnosis["goal_id"],
        "job_ids": list(dict.fromkeys(job_ids)),
        "profile_version": profile["version"],
        "start_date": start_date,
        "deadline": deadline,
        "tasks": schedule(tasks, start_date, profile["weekly_minutes"]),
        "verified_skills": verified,
        "catalogue_versions": diagnosis["catalogue_versions"],
        "weight_version": diagnosis["weight_version"],
        "milestones": ["完成先修基础", "完成目标技能学习与练习", "通过对应基础客观测验"],
    }
    payload["validation"] = validate_plan(payload, profile["weekly_minutes"], graph)
    payload["source_context"] = context_hash(db, principal)
    return payload


def create_plan(db, principal, job_ids, start_date, deadline, trace):
    payload = build_plan(db, principal, job_ids, start_date, deadline)
    return growth.save(db, principal, "plan", payload, trace)


def active_plan(db, principal):
    return next(
        (
            r
            for r in db.scalars(growth.owner_query(principal, "plan"))
            if json.loads(r.payload_json)["status"] == "active"
        ),
        None,
    )


def publish_plan(db, principal, plan_id, version, trace):
    growth.lock_owner(db, principal)
    row = growth.owned(db, principal, plan_id, "plan")
    payload = json.loads(row.payload_json)
    if payload["status"] != "draft":
        raise DomainError("INVALID_STATE", "只有草案可以发布")
    profile = profile_context(db, principal)
    active_goal = next(
        (g["id"] for g in growth.goal_snapshot(db, principal) if g["status"] == "active"), None
    )
    if profile["version"] != payload["profile_version"] or active_goal != payload["goal_id"]:
        raise DomainError("STALE_PLAN", "档案或目标已变化，请重新生成计划", 412)
    if payload.get("source_context") != context_hash(db, principal):
        raise DomainError("STALE_PLAN", "岗位、词库或能力证据已变化，请重新生成计划", 412)
    payload["validation"] = validate_plan(
        payload, profile["weekly_minutes"], catalogue.skill_map(db)
    )
    existing = active_plan(db, principal)
    if existing:
        old = json.loads(existing.payload_json)
        if any(t["locked"] or t["status"] == "completed" for t in old["tasks"]):
            raise DomainError("USE_REPLAN", "已有受保护任务，请使用重排预览")
        growth.save(
            db, principal, "plan", {**old, "status": "archived"}, trace, existing, existing.version
        )
    payload["status"] = "active"
    return growth.save(db, principal, "plan", payload, trace, row, version)


def task_action(db, principal, plan_id, task_id, body, version, trace):
    row = growth.owned(db, principal, plan_id, "plan")
    payload = json.loads(row.payload_json)
    if payload["status"] != "active":
        raise DomainError("INVALID_STATE", "请先发布学习计划")
    task = next((t for t in payload["tasks"] if t["id"] == task_id), None)
    if task is None:
        raise DomainError("NOT_FOUND", "任务不存在", 404)
    action, before = body["action"], copy.deepcopy(task)
    transitions = {
        "start": ({"pending", "paused"}, "running"),
        "pause": ({"running"}, "paused"),
        "complete": ({"running"}, "completed"),
        "skip": ({"pending", "paused"}, "skipped"),
    }
    if action in transitions:
        allowed, target = transitions[action]
        if task["status"] not in allowed:
            raise DomainError("INVALID_STATE", "任务状态不支持此操作")
        completed = {t["id"] for t in payload["tasks"] if t["status"] == "completed"}
        if action == "start" and not set(task["requires"]) <= completed:
            raise DomainError("PREREQUISITE_REQUIRED", "请先完成先修任务", 422)
        if action == "complete" and not body.get("outcome", "").strip():
            raise DomainError("OUTCOME_REQUIRED", "请记录学习成果", 422)
        task["status"] = target
        if action == "complete":
            task["outcome"] = body["outcome"]
    elif action in {"lock", "unlock"}:
        task["locked"] = action == "lock"
    elif action == "attach":
        material = growth.owned(db, principal, body["material_id"], "material")
        task["material_id"] = material.id
    elif action in {"defer", "split"}:
        if task["status"] in {"completed", "skipped"}:
            raise DomainError("INVALID_STATE", "已结束任务不能调整")
        if action == "defer":
            task["date"] = body["date"]
        else:
            part = body.get("minutes", 0)
            if not 0 < part < task["minutes"]:
                raise DomainError("INVALID_SPLIT", "拆分耗时必须小于原任务且大于零", 422)
            second = {
                **copy.deepcopy(task),
                "id": new_id(),
                "minutes": task["minutes"] - part,
                "requires": [task_id],
                "locked": False,
                "status": "pending",
            }
            task["minutes"] = part
            for other in payload["tasks"]:
                if other["id"] != task_id:
                    other["requires"] = [
                        second["id"] if r == task_id else r for r in other["requires"]
                    ]
            payload["tasks"].insert(payload["tasks"].index(task) + 1, second)
    else:
        raise DomainError("INVALID_ACTION", "未知任务操作", 422)
    profile = profile_context(db, principal)
    payload["validation"] = validate_plan(
        payload, profile["weekly_minutes"], catalogue.skill_map(db)
    )
    result = growth.save(db, principal, "plan", payload, trace, row, version)
    if action == "complete" and not task.get("project_id"):
        add_evidence(
            db,
            principal,
            task["skill_id"],
            "learning_completion",
            plan_id,
            result["version"],
            "supported",
            trace,
        )
    if task != before:
        result["replan_preview"] = auto_preview(db, principal, "TaskChanged", trace)
    if action == "complete":
        from careerpilot import analytics

        analytics.record(db, principal, "learning_completed", trace, trusted=True)
    return result


def context_hash(db, principal):
    return growth.payload_hash(
        {
            "profile": growth.data(growth.profile(db, principal)),
            "goals": growth.goal_snapshot(db, principal),
            "evidence": ledger(db, principal),
            "skills": {k: v["version"] for k, v in catalogue.skill_map(db).items()},
            "jobs": [
                (r.id, r.version)
                for r in db.scalars(
                    growth.owner_query(principal, "job").order_by(growth.GrowthRecord.id)
                )
            ],
        }
    )


def protected_tasks(payload):
    by_id = {t["id"]: t for t in payload["tasks"]}
    protected = {t["id"] for t in payload["tasks"] if t["locked"] or t["status"] == "completed"}
    pending = list(protected)
    while pending:
        for parent in by_id[pending.pop()]["requires"]:
            if parent not in protected:
                protected.add(parent)
                pending.append(parent)
    return [copy.deepcopy(t) for t in payload["tasks"] if t["id"] in protected]


def replan_preview(db, principal, plan_id, reason, trace):
    row = growth.owned(db, principal, plan_id, "plan")
    current = json.loads(row.payload_json)
    profile = profile_context(db, principal)
    profile["weekly_minutes"] = plan_budget(current, profile["weekly_minutes"])
    protected = protected_tasks(current)
    states = {s["skill_id"]: s["status"] for s in snapshots(db, principal)}
    tasks = [
        copy.deepcopy(t)
        for t in current["tasks"]
        if t not in protected
        and t["status"] != "skipped"
        and (states[t["skill_id"]] != "verified" or t.get("project_id"))
    ]
    retained_ids = {t["id"] for t in protected + tasks}
    for task in tasks:
        task["requires"] = [r for r in task["requires"] if r in retained_ids]
    # Failure adds concrete remedial work, while preserving all protected tasks.
    failed = {key for key, value in states.items() if value == "needs_reassessment"}
    for key in sorted(failed):
        if not any(t.get("remedial") and t["skill_id"] == key for t in tasks):
            template = next((t for t in current["tasks"] if t["skill_id"] == key), None)
            if template:
                last_by_skill = {t["skill_id"]: t["id"] for t in protected + tasks}
                tasks.append(
                    {
                        **copy.deepcopy(template),
                        "id": new_id(),
                        "minutes": min(60, profile["weekly_minutes"]),
                        "title": catalogue.require_skill(db, key)["name"] + " 薄弱点补救",
                        "requires": [
                            last_by_skill[parent]
                            for parent in catalogue.require_skill(db, key)["requires"]
                            if parent in last_by_skill
                        ],
                        "locked": False,
                        "status": "pending",
                        "remedial": True,
                        "outcome": "",
                        "verification": "objective_quiz:foundation.v1",
                    }
                )
    updated_goal = next(
        (g["id"] for g in growth.goal_snapshot(db, principal) if g["status"] == "active"), None
    )
    if updated_goal != current["goal_id"]:
        replacement = build_plan(
            db, principal, current["job_ids"], current["start_date"], current["deadline"]
        )
        tasks = replacement["tasks"]
        protected_skills = {t["skill_id"] for t in protected}
        # Keep the protected work, and link new tasks to it for shared prerequisites.
        fixed_end = {
            k: next(t["id"] for t in reversed(protected) if t["skill_id"] == k)
            for k in protected_skills
        }
        covered_minutes: Counter[str] = Counter()
        for task in protected:
            covered_minutes[task["skill_id"]] += task["minutes"]
        removed_ids = {}
        remaining = []
        for task in tasks:
            covered = min(covered_minutes[task["skill_id"]], task["minutes"])
            covered_minutes[task["skill_id"]] -= covered
            if covered == task["minutes"]:
                removed_ids[task["id"]] = task["skill_id"]
            else:
                task["minutes"] -= covered
                remaining.append(task)
        tasks = remaining
        for task in tasks:
            task["requires"] = [
                fixed_end[removed_ids[r]] if r in removed_ids else r for r in task["requires"]
            ]
    candidate = {
        **copy.deepcopy(current),
        "goal_id": updated_goal,
        "profile_version": profile["version"],
        "verified_skills": [k for k, v in states.items() if v == "verified"],
    }
    candidate["tasks"] = schedule(
        split_for_budget(tasks, profile["weekly_minutes"]),
        current["start_date"],
        profile["weekly_minutes"],
        protected,
    )
    validation_error = None
    try:
        candidate["validation"] = validate_plan(
            candidate, profile["weekly_minutes"], catalogue.skill_map(db), protected
        )
    except DomainError as exc:
        validation_error = {"code": exc.code, "message": exc.message}
    before = {t["id"]: t for t in current["tasks"]}
    after = {t["id"]: t for t in candidate["tasks"]}
    diff = {
        "added": [t for k, t in after.items() if k not in before],
        "removed": [t for k, t in before.items() if k not in after],
        "changed": [
            {"before": before[k], "after": t}
            for k, t in after.items()
            if k in before and before[k] != t
        ],
    }
    return growth.save(
        db,
        principal,
        "plan_preview",
        {
            "plan_id": plan_id,
            "base_version": row.version,
            "reason": reason,
            "status": "pending",
            "context_hash": context_hash(db, principal),
            "candidate": candidate,
            "diff": diff,
            "validation_error": validation_error,
        },
        trace,
    )


def auto_preview(db, principal, reason, trace):
    row = active_plan(db, principal)
    if row is None:
        return None
    try:
        return replan_preview(db, principal, row.id, reason, trace)
    except DomainError as exc:
        return growth.save(
            db,
            principal,
            "plan_preview",
            {
                "plan_id": row.id,
                "base_version": row.version,
                "reason": reason,
                "status": "pending",
                "context_hash": context_hash(db, principal),
                "candidate": json.loads(row.payload_json),
                "diff": {"added": [], "removed": [], "changed": []},
                "validation_error": {"code": exc.code, "message": exc.message},
            },
            trace,
        )


def decide_preview(db, principal, preview_id, accept, version, trace):
    growth.lock_owner(db, principal)
    preview = growth.owned(db, principal, preview_id, "plan_preview")
    payload = json.loads(preview.payload_json)
    if payload["status"] != "pending":
        raise DomainError("INVALID_STATE", "重排预览已处理")
    if accept:
        if source := payload.get("project_source"):
            from careerpilot.project_workflow import source_context

            if source_context(db, principal, source["project_id"]) != source["source_context"]:
                raise DomainError("STALE_PROJECT", "项目、成果或陈述已改变，请重新预览", 412)
        if source := payload.get("interview_source"):
            review = growth.data(
                growth.owned(db, principal, source["review_id"], "interview_review")
            )
            if review["version"] != source["review_version"] or review["status"] != "confirmed":
                raise DomainError("STALE_REVIEW", "面试复盘已修改或撤回，请重新预览", 412)
        row = growth.owned(db, principal, payload["plan_id"], "plan")
        if (
            row.version != payload["base_version"]
            or context_hash(db, principal) != payload["context_hash"]
        ):
            raise DomainError("STALE_PREVIEW", "计划或证据已变化，请重新预览", 412)
        if payload["validation_error"]:
            raise DomainError(
                payload["validation_error"]["code"], payload["validation_error"]["message"], 422
            )
        old = json.loads(row.payload_json)
        protected = protected_tasks(old)
        validate_plan(
            payload["candidate"],
            profile_context(db, principal)["weekly_minutes"],
            catalogue.skill_map(db),
            protected,
        )
        growth.save(
            db, principal, "plan", payload["candidate"], trace, row, payload["base_version"]
        )
    return growth.save(
        db,
        principal,
        "plan_preview",
        {**payload, "status": "accepted" if accept else "rejected"},
        trace,
        preview,
        version,
    )


def restore_plan(db, principal, plan_id, target_version, version, trace):
    row = growth.owned(db, principal, plan_id, "plan")
    history = growth.history(db, principal, plan_id, "plan")
    target = next((h["payload"] for h in history if h["version"] == target_version), None)
    if target is None:
        raise DomainError("NOT_FOUND", "历史版本不存在", 404)
    old = json.loads(row.payload_json)
    protected = protected_tasks(old)
    target = {**target, "status": old["status"]}
    target["verified_skills"] = [
        s["skill_id"] for s in snapshots(db, principal) if s["status"] == "verified"
    ]
    active_goal = next(
        (g["id"] for g in growth.goal_snapshot(db, principal) if g["status"] == "active"), None
    )
    if target["goal_id"] != active_goal:
        raise DomainError("STALE_PLAN", "历史计划的职业目标已改变，请先切回目标或重新规划", 412)
    validate_plan(
        target, profile_context(db, principal)["weekly_minutes"], catalogue.skill_map(db), protected
    )
    result = growth.save(db, principal, "plan", target, trace, row, version)
    growth.audit(db, principal, row.id, f"plan.restored_from_{target_version}", row.version, trace)
    return result
