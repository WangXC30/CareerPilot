"""Three durable tool steps; a restart resumes after the last committed result."""

import time

from sqlalchemy import update
from sqlalchemy.orm import Session

from careerpilot import growth, learning, reporting, source_library, web_sources
from careerpilot.domain import DomainError, Principal
from careerpilot.persistence import affected
from careerpilot.platform_models import BackgroundTask
from careerpilot.safety import require_consent, scan_upload

STEPS = ("period_report", "source_references", "replan_preview")


def context(db, principal, plan_id):
    plan = growth.owned(db, principal, plan_id, "plan")
    return {"growth_hash": learning.context_hash(db, principal), "plan_version": plan.version}


def enqueue(db, principal, args, key, trace):
    from careerpilot.runtime import enqueue as enqueue_task

    growth.lock_owner(db, principal)
    reporting.range_dates(args["start"], args["end"])
    plan = growth.data(growth.owned(db, principal, args["plan_id"], "plan"))
    if plan["status"] != "active":
        raise DomainError("ACTIVE_PLAN_REQUIRED", "请选择当前已发布计划", 409)
    if args["reference_url"]:
        require_consent(db, principal, "public_web")
        args = {**args, "reference_url": source_library.reference_url(args["reference_url"])}
        from urllib.parse import urlsplit

        if urlsplit(args["reference_url"]).hostname not in web_sources.ALLOWED_HOSTS:
            raise DomainError("UNSAFE_URL", "可选读取仅支持已登记的公开官方文档主机", 422)
    scan_upload(args["query"].encode(), "review-query.txt")
    task = enqueue_task(db, principal, "growth.review_pipeline", key, trace, request_payload=args)
    existing = db.get(growth.GrowthRecord, task["id"])
    if existing:
        return public(db, principal, existing)
    row = growth.save(
        db,
        principal,
        "review_pipeline",
        {
            "input": args,
            "context": context(db, principal, args["plan_id"]),
            "steps": [],
            "status": "queued",
            "last_error": None,
            "trace_id": trace,
            "scope": "固定三步白名单；只生成报告、来源引用和计划预览，不接受计划或执行外部投递",
        },
        trace,
        record_id=task["id"],
    )
    return public(db, principal, db.get(growth.GrowthRecord, row["id"]))


def public(db, principal, row):
    from careerpilot.runtime import owned_task, task_data

    result = growth.data(row)
    result["task"] = task_data(owned_task(db, principal, row.id))
    result["completed_steps"] = len(result["steps"])
    result["next_step"] = STEPS[len(result["steps"])] if len(result["steps"]) < len(STEPS) else None
    return result


def execute_step(db, principal, run, stage, trace):
    args = run["input"]
    if stage == "period_report":
        return reporting.report(db, principal, args["start"], args["end"])
    if stage == "source_references":
        result = source_library.discover(db, principal, args["query"])
        result["private_reference_ids"] = [
            r["id"] for r in result["results"] if r["access_mode"] != "curated_index"
        ]
        result["results"] = [r for r in result["results"] if r["access_mode"] == "curated_index"]
        if args["reference_url"]:
            fetched = web_sources.fetch(db, principal, args["reference_url"])
            scan_upload(fetched["excerpt"].encode(), "public-excerpt.txt")
            saved = source_library.store(
                db,
                principal,
                {
                    "title": "复盘读取的公开文档",
                    "url": fetched["source_url"],
                    "text": fetched["excerpt"],
                    "kind": "documentation",
                    "skill_ids": [],
                    "platform": "other",
                    "claims": [],
                    "published_on": None,
                    "author": "",
                    "source_type": "public_read",
                    "access_mode": "public_web",
                    "authorization_scope": "bounded_public_excerpt",
                    "retrieved_at": fetched["retrieved_at"],
                    "imported_at": None,
                    "verification": "已读取公开短摘录，内容仍需核对；不产生能力证据",
                },
                trace,
            )
            result["public_reference"] = {
                "source_id": saved["id"],
                "source_url": fetched["source_url"],
                "retrieved_at": fetched["retrieved_at"],
                "sha256": fetched["sha256"],
            }
        return result
    return learning.replan_preview(db, principal, args["plan_id"], "UserRequested", trace)


def fence(db, task_id, token, effective_now, stage):
    result = db.execute(
        update(BackgroundTask)
        .where(
            BackgroundTask.id == task_id,
            BackgroundTask.kind == "growth.review_pipeline",
            BackgroundTask.status == "running",
            BackgroundTask.lease_token == token,
            BackgroundTask.lease_until > effective_now,
        )
        .values(
            checkpoint=stage, version=BackgroundTask.version + 1, lease_until=effective_now + 30
        )
    )
    return affected(result) == 1


def handler(engine, task_id, token, now):
    started = time.monotonic()
    stage = ""
    try:
        for stage in STEPS:
            with Session(engine) as db, db.begin():
                task = db.get(BackgroundTask, task_id)
                if task is None:
                    return
                principal = Principal(task.user_id, task.tenant_id)
                growth.lock_owner(db, principal)
                if not fence(db, task_id, token, now + int(time.monotonic() - started), stage):
                    return
                row = growth.owned(db, principal, task_id, "review_pipeline")
                run = growth.data(row)
                if any(s["name"] == stage for s in run["steps"]):
                    continue
                if context(db, principal, run["input"]["plan_id"]) != run["context"]:
                    raise DomainError(
                        "STALE_WORKFLOW_CONTEXT",
                        "档案、证据、岗位或计划已变更，请保留旧结果并新建复盘",
                        409,
                    )
                result = execute_step(db, principal, run, stage, task.trace_id)
                if not fence(
                    db, task_id, token, now + int(time.monotonic() - started), stage + "_committed"
                ):
                    raise DomainError("LEASE_EXPIRED", "步骤租约已失效", 409)
                run["steps"].append(
                    {
                        "name": stage,
                        "result": result,
                        "finished_at": growth.utc_now(),
                        "tool": "review." + stage,
                        "trace_id": task.trace_id,
                    }
                )
                growth.save(
                    db,
                    principal,
                    "review_pipeline",
                    {
                        **reporting.body(run),
                        "status": "success" if stage == STEPS[-1] else "partial_success",
                        "last_error": None,
                    },
                    task.trace_id,
                    row,
                    row.version,
                )
    except Exception as exc:
        with Session(engine) as db, db.begin():
            task = db.get(BackgroundTask, task_id)
            if (
                task
                and task.status == "running"
                and task.lease_token == token
                and task.lease_until > now + int(time.monotonic() - started)
            ):
                principal = Principal(task.user_id, task.tenant_id)
                growth.lock_owner(db, principal)
                row = growth.owned(db, principal, task_id, "review_pipeline")
                run = growth.data(row)
                growth.save(
                    db,
                    principal,
                    "review_pipeline",
                    {
                        **reporting.body(run),
                        "status": "partial_success" if run["steps"] else "failed",
                        "last_error": {
                            "step": stage,
                            "code": exc.code
                            if isinstance(exc, DomainError)
                            else "STEP_UNAVAILABLE",
                        },
                    },
                    task.trace_id,
                    row,
                    row.version,
                )
        raise
