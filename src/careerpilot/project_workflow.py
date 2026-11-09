"""Versioned project progress, private artifacts and reviewable plan integration."""

import copy
import hashlib
import re
from datetime import datetime
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo

from careerpilot import catalogue, growth, learning, project_evidence, projects
from careerpilot.domain import DomainError
from careerpilot.persistence import new_id
from careerpilot.safety import scan_upload


def text(value):
    try:
        content = value.encode("utf-8")
    except UnicodeEncodeError:
        raise DomainError("INVALID_TEXT", "请使用有效 UTF-8 文本", 422) from None
    return scan_upload(content, "project-note.txt")


def items(db, principal, kind, project_id):
    return [
        growth.data(r)
        for r in db.scalars(
            growth.owner_query(principal, kind).order_by(
                growth.GrowthRecord.created_at, growth.GrowthRecord.id
            )
        )
        if projects.payload(r).get("project_id") == project_id
    ]


def source_context(db, principal, project_id):
    return growth.payload_hash(
        {
            "project": growth.data(growth.owned(db, principal, project_id, "practice_project")),
            "assets": [
                (a["id"], a["version"]) for a in items(db, principal, "project_asset", project_id)
            ],
            "narratives": [
                (a["id"], a["version"])
                for a in items(db, principal, "project_narrative", project_id)
            ],
            "submissions": [
                (a["id"], a["version"])
                for a in items(db, principal, "project_submission", project_id)
            ],
            "assessments": [
                (a["id"], a["version"])
                for a in items(db, principal, "project_assessment", project_id)
            ],
        }
    )


def progress(db, principal, project):
    receipts = project_evidence.current(db, principal)
    current = [r for r in receipts if r["artifact_id"] == project["latest_submission_id"]]
    results = []
    for milestone in project["template"]["milestones"]:
        stored = project["milestones"].get(milestone["id"], "pending")
        value = (
            {"status": stored, "note": "", "asset_ids": []} if isinstance(stored, str) else stored
        )
        assets = [
            growth.data(growth.owned(db, principal, key, "project_asset"))
            for key in value.get("asset_ids", [])
        ]
        valid_assets = all(a["status"] == "active" for a in assets)
        result = {**milestone, **value, "source_assets_current": valid_assets}
        if milestone["verification"] == "sandbox_tests":
            result.update(
                status="passed" if current else "unverified",
                note="",
                assessment_id=current[0]["assessment_id"] if current else None,
            )
        elif result["status"] == "completed" and not valid_assets:
            result["status"] = "needs_review"
        results.append(result)
    return results


def detail(db, principal, project_id):
    project = growth.data(growth.owned(db, principal, project_id, "practice_project"))
    reviews = items(db, principal, "project_review", project_id)
    context = source_context(db, principal, project_id)
    milestones = progress(db, principal, project)
    return {
        **project,
        "progress": milestones,
        "assets": items(db, principal, "project_asset", project_id),
        "narrative": next(iter(items(db, principal, "project_narrative", project_id)), None),
        "reviews": [
            {
                **r,
                "current": r["id"] == reviews[-1]["id"]
                and r["source_context"] == context
                and r["learning_context"] == learning.context_hash(db, principal)
                and (
                    r["decision"] != "accept"
                    or all(m["status"] in {"passed", "completed"} for m in milestones)
                ),
            }
            for r in reviews
        ],
    }


def check_assets(db, principal, project_id, asset_ids):
    for key in asset_ids:
        asset = growth.data(growth.owned(db, principal, key, "project_asset"))
        if asset["project_id"] != project_id or asset["status"] != "active":
            raise DomainError("ASSET_UNAVAILABLE", "成果必须属于此项目且未撤回", 422)


def milestone(db, principal, project_id, milestone_id, body, expected, trace):
    growth.lock_owner(db, principal)
    row = growth.owned(db, principal, project_id, "practice_project")
    value = projects.payload(row)
    definition = next((m for m in value["template"]["milestones"] if m["id"] == milestone_id), None)
    if definition is None:
        raise DomainError("NOT_FOUND", "里程碑不存在", 404)
    if definition["verification"] == "sandbox_tests":
        raise DomainError("TRUSTED_TEST_REQUIRED", "实现里程碑只能依据当前隔离测试回执", 422)
    text(body["note"])
    check_assets(db, principal, project_id, body["asset_ids"])
    if body["status"] == "completed" and not body["note"].strip():
        raise DomainError("PROJECT_NOTE_REQUIRED", "请记录本阶段的实际过程与结果", 422)
    value["milestones"][milestone_id] = {
        **body,
        "updated_at": growth.utc_now(),
        "basis": "user_report_not_capability_verification",
    }
    return growth.save(db, principal, "practice_project", value, trace, row, expected)


def create_asset(db, principal, project_id, args, key, trace):
    def make():
        growth.owned(db, principal, project_id, "practice_project")
        if args["kind"] == "text":
            if (
                not re.fullmatch(r"[\w .-]{1,100}\.(?:txt|md|json)", args["filename"])
                or not args["content"].strip()
                or args["url"]
            ):
                raise DomainError("INVALID_ASSET", "请提供无路径的纯文本文件名及内容", 422)
            content = text(args["content"])
            if len(content.encode()) > 65536:
                raise DomainError("ASSET_TOO_LARGE", "成果文本最多 64 KiB", 422)
        else:
            if (
                not re.fullmatch(
                    r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/"
                    r"(?:commit/[0-9a-fA-F]{40}|pull/[1-9][0-9]*)",
                    args["url"],
                )
                or args["content"]
            ):
                raise DomainError(
                    "INVALID_GITHUB_REFERENCE", "仅支持无凭据的 GitHub commit/PR 链接", 422
                )
            content = args["url"]
        return {
            **args,
            "project_id": project_id,
            "status": "active",
            "visibility": "private",
            "content_hash": hashlib.sha256(content.encode()).hexdigest(),
            "verification": "user_provided_not_fetched_or_verified",
        }

    return projects.once(
        db, principal, "project_asset", key, {**args, "project_id": project_id}, trace, make
    )


def withdraw_asset(db, principal, asset_id, expected, trace):
    growth.lock_owner(db, principal)
    row = growth.owned(db, principal, asset_id, "project_asset")
    return growth.save(
        db,
        principal,
        "project_asset",
        {**projects.payload(row), "status": "withdrawn"},
        trace,
        row,
        expected,
    )


def save_narrative(db, principal, project_id, body, expected, trace):
    growth.lock_owner(db, principal)
    growth.owned(db, principal, project_id, "practice_project")
    for field in ("situation", "task", "action", "result"):
        text(body[field])
    check_assets(db, principal, project_id, body["asset_ids"])
    if body["status"] == "confirmed" and not all(
        body[k].strip() for k in ("situation", "task", "action")
    ):
        raise DomainError("NARRATIVE_REQUIRED", "确认前请填写实际背景、任务与个人行动", 422)
    record_id = str(uuid5(NAMESPACE_URL, "project-narrative:" + project_id))
    row = db.scalar(
        growth.owner_query(principal, "project_narrative").where(
            growth.GrowthRecord.id == record_id
        )
    )
    return growth.save(
        db,
        principal,
        "project_narrative",
        {
            **body,
            "project_id": project_id,
            "basis": "user_confirmed_statement" if body["status"] == "confirmed" else "draft",
            "confirmed_at": growth.utc_now() if body["status"] == "confirmed" else None,
        },
        trace,
        row,
        expected,
        record_id=record_id,
    )


def narrative_source_status(db, principal, source):
    row = db.scalar(
        growth.owner_query(principal, "project_narrative").where(
            growth.GrowthRecord.id == source["narrative_id"]
        )
    )
    if row is None:
        return "source_deleted"
    value = growth.data(row)
    if value["status"] != "confirmed":
        return "source_withdrawn"
    if row.version != source["narrative_version"]:
        return "needs_reconfirmation"
    try:
        check_assets(db, principal, value["project_id"], value["asset_ids"])
    except DomainError:
        return "source_withdrawn"
    return "current"


def narrative_draft(db, principal, project_id):
    project = growth.data(growth.owned(db, principal, project_id, "practice_project"))
    narratives = items(db, principal, "project_narrative", project_id)
    narrative = narratives[0] if narratives else None
    labels = {"situation": "背景", "task": "任务", "action": "个人行动", "result": "结果"}
    lines = [
        f"{label}：{narrative[key]}"
        for key, label in labels.items()
        if narrative and narrative[key].strip()
    ]
    unknown = [
        label for key, label in labels.items() if not narrative or not narrative[key].strip()
    ]
    receipt = next(
        (
            r
            for r in project_evidence.current(db, principal)
            if r["artifact_id"] == project["latest_submission_id"]
        ),
        None,
    )
    if receipt:
        lines.append("代码测试：通过 record-summary.tests.v1 的有限 Python 数据处理测试。")
    return {
        "text": "\n".join(lines),
        "unknown": unknown + ["未填写的业务指标与贡献比例"],
        "narrative": narrative,
        "receipt": receipt,
        "notice": "STAR 内容来自本人陈述；代码测试只验证所列行为，未独立核验经历或业务指标。",
    }


def confirm_narrative_fact(db, principal, project_id, expected, key, trace):
    def make():
        draft = narrative_draft(db, principal, project_id)
        value = draft["narrative"]
        if not value or value["version"] != expected:
            raise DomainError("VERSION_CONFLICT", "陈述已改变，请重新预览", 412)
        if value["status"] != "confirmed" or not draft["receipt"]:
            raise DomainError(
                "PROJECT_CONFIRMATION_REQUIRED", "请确认 STAR 陈述并取得当前代码通过回执", 422
            )
        check_assets(db, principal, project_id, value["asset_ids"])
        receipt = draft["receipt"]
        return {
            "kind": "project",
            "text": draft["text"],
            "source": {
                "type": "project",
                "id": receipt["artifact_id"],
                "version": receipt["artifact_version"],
                "artifact_hash": receipt["artifact_hash"],
                "assessment_id": receipt["assessment_id"],
                "assessment_version": receipt["assessment_version"],
                "narrative_id": value["id"],
                "narrative_version": expected,
            },
            "verification": "user_statement_with_limited_project_tests",
            "confirmed_at": growth.utc_now(),
        }

    return projects.once(
        db,
        principal,
        "resume_fact",
        key,
        {"project_id": project_id, "narrative_version": expected},
        trace,
        make,
    )


def review(db, principal, project_id, decision, note, expected, key, trace):
    def make():
        value = detail(db, principal, project_id)
        if value["version"] != expected:
            raise DomainError("VERSION_CONFLICT", "项目已更新，请刷新", 412)
        text(note)
        checks = [
            {
                "milestone_id": m["id"],
                "status": m["status"],
                "basis": m["verification"],
                "asset_ids": m.get("asset_ids", []),
                "assessment_id": m.get("assessment_id"),
            }
            for m in value["progress"]
        ]
        passed = all(c["status"] in {"passed", "completed"} for c in checks)
        if decision == "accept" and not passed:
            raise DomainError(
                "PROJECT_CHECKS_INCOMPLETE", "请先完成自述阶段并取得当前测试通过回执", 422
            )
        return {
            "project_id": project_id,
            "project_version": expected,
            "decision": decision,
            "note": note,
            "checks": checks,
            "rubric_version": "project-milestones.v1",
            "source_context": source_context(db, principal, project_id),
            "learning_context": learning.context_hash(db, principal),
            "basis": "user_review_with_separate_limited_tests",
            "visibility": "private",
        }

    return projects.once(
        db,
        principal,
        "project_review",
        key,
        {"project_id": project_id, "version": expected, "decision": decision, "note": note},
        trace,
        make,
    )


def plan_preview(db, principal, project_id, plan_id, expected, key, trace):
    def make():
        project = growth.data(growth.owned(db, principal, project_id, "practice_project"))
        if project["version"] != expected:
            raise DomainError("VERSION_CONFLICT", "项目已更新，请刷新", 412)
        row = growth.owned(db, principal, plan_id, "plan")
        current = projects.payload(row)
        if current["status"] != "active":
            raise DomainError("ACTIVE_PLAN_REQUIRED", "请先发布学习计划", 409)
        if any(t.get("project_id") == project_id for t in current["tasks"]):
            raise DomainError("PROJECT_ALREADY_PLANNED", "项目已在此计划中，不能重复添加", 409)
        profile = learning.profile_context(db, principal, current)
        profile["weekly_minutes"] = learning.plan_budget(current, profile["weekly_minutes"])
        graph = catalogue.skill_map(db)
        candidate = copy.deepcopy(current)
        candidate["profile_version"] = profile["version"]
        candidate["catalogue_versions"] = {k: v["version"] for k, v in graph.items()}
        fixed = learning.protected_tasks(current)
        fixed_ids = {t["id"] for t in fixed}
        fixed += [
            copy.deepcopy(t)
            for t in current["tasks"]
            if t["status"] == "skipped" and t["id"] not in fixed_ids
        ]
        fixed_ids = {t["id"] for t in fixed}
        tasks = [copy.deepcopy(t) for t in current["tasks"] if t["id"] not in fixed_ids]
        verified = {
            s["skill_id"] for s in learning.snapshots(db, principal) if s["status"] == "verified"
        }
        last = {t["skill_id"]: t["id"] for t in fixed + tasks if t["status"] != "skipped"}

        def append(skill_id, title, minutes, milestone_id, verification):
            for parent in graph[skill_id]["requires"]:
                if parent not in verified and parent not in last:
                    append(
                        parent,
                        graph[parent]["name"] + " 项目先修",
                        graph[parent]["minutes"],
                        "prerequisite",
                        "learning_record",
                    )
            dependencies = ([last[skill_id]] if skill_id in last else []) + [
                last[p] for p in graph[skill_id]["requires"] if p in last
            ]
            while minutes:
                duration = min(90, profile["weekly_minutes"], minutes)
                item = {
                    "id": new_id(),
                    "skill_id": skill_id,
                    "title": title,
                    "minutes": duration,
                    "requires": list(dict.fromkeys(dependencies)),
                    "locked": False,
                    "status": "pending",
                    "resources": ["resource-" + skill_id],
                    "outcome": "",
                    "definition_of_done": "保存本阶段成果并按项目验收清单复查；打卡不授予能力验证",
                    "verification": verification,
                    "project_id": project_id,
                    "project_version": expected,
                    "project_milestone_id": milestone_id,
                }
                tasks.append(item)
                last[skill_id] = item["id"]
                dependencies = [item["id"]]
                minutes -= duration

        for milestone in project["template"]["milestones"]:
            append(
                project["template"]["skill_ids"][0],
                milestone["title"],
                milestone["minutes"],
                milestone["id"],
                "project_sandbox_tests"
                if milestone["verification"] == "sandbox_tests"
                else "project_user_report",
            )
        today = datetime.now(ZoneInfo(profile["timezone"])).date().isoformat()
        candidate["tasks"] = learning.schedule(
            tasks, max(today, current["start_date"]), profile["weekly_minutes"], fixed
        )
        candidate["verified_skills"] = sorted(verified)
        error = None
        try:
            candidate["validation"] = learning.validate_plan(
                candidate, profile["weekly_minutes"], graph, fixed
            )
        except DomainError as exc:
            error = {"code": exc.code, "message": exc.message}
        before = {t["id"]: t for t in current["tasks"]}
        return {
            "plan_id": plan_id,
            "base_version": row.version,
            "reason": "ProjectPracticeAdded",
            "status": "pending",
            "context_hash": learning.context_hash(db, principal),
            "candidate": candidate,
            "diff": {
                "added": [t for t in candidate["tasks"] if t["id"] not in before],
                "removed": [],
                "changed": [
                    {"before": before[t["id"]], "after": t}
                    for t in candidate["tasks"]
                    if t["id"] in before and t != before[t["id"]]
                ],
            },
            "validation_error": error,
            "project_source": {
                "project_id": project_id,
                "source_context": source_context(db, principal, project_id),
            },
        }

    growth.lock_owner(db, principal)
    return projects.once(
        db,
        principal,
        "plan_preview",
        key,
        {
            "action": "project.plan_preview",
            "project_id": project_id,
            "version": expected,
            "plan_id": plan_id,
        },
        trace,
        make,
    )
