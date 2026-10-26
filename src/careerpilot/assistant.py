"""Single local assistant using schema-bound tools and the leased Worker runtime."""

import json
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from careerpilot import catalogue, growth, learning
from careerpilot.domain import DomainError, Principal
from careerpilot.persistence import utc_now
from careerpilot.platform_models import BackgroundTask, RoleGrant
from careerpilot.tools import ToolSpec, authorize_tool
from careerpilot.web_sources import search

ROLES = frozenset({"student", "operator", "admin"})
ARRAY_SCHEMA = {"type": "array", "items": {"type": "string", "maxLength": 36}, "maxItems": 20}
SCHEMAS = {
    "diagnose": {
        "type": "object",
        "properties": {"job_ids": ARRAY_SCHEMA},
        "required": ["job_ids"],
        "additionalProperties": False,
    },
    "plan": {
        "type": "object",
        "properties": {"job_ids": ARRAY_SCHEMA, "start_date": {"type": "string", "format": "date"}},
        "required": ["job_ids", "start_date"],
        "additionalProperties": False,
    },
    "progress": {"type": "object", "properties": {}, "additionalProperties": False},
    "resources": {
        "type": "object",
        "properties": {"query": {"type": "string", "maxLength": 200}},
        "required": ["query"],
        "additionalProperties": False,
    },
    "replan": {
        "type": "object",
        "properties": {"plan_id": {"type": "string", "maxLength": 36}},
        "required": ["plan_id"],
        "additionalProperties": False,
    },
}


def intent_from(message):
    text = message.casefold()
    if any(w in message for w in ["投递", "发送邮件", "删除账号", "读取令牌"]) or re.search(
        r"\b(?:apply\s+(?:for|to)|submit\s+(?:an?\s+)?application|send\s+(?:an?\s+)?email|"
        r"delete\s+(?:my\s+|the\s+)?account|read\s+(?:my\s+|the\s+)?token)\b",
        text,
    ):
        raise DomainError("TOOL_DENIED", "当前成长助手没有此操作权限", 403)
    for words, english, intent in [
        (
            ["重排", "调整计划"],
            r"\b(?:replan|reschedule|adjust\s+(?:my\s+|the\s+)?plan)\b",
            "replan",
        ),
        (["计划", "安排学习"], r"\b(?:plan|schedule)\b", "plan"),
        (["资源", "教程"], r"\b(?:resources?|tutorials?)\b", "resources"),
        (["差距", "岗位要求"], r"\b(?:gaps?|job\s+requirements)\b", "diagnose"),
    ]:
        if any(word in message for word in words) or re.search(english, text):
            return intent
    return "progress"


def enqueue(db, principal, payload, key, trace):
    from careerpilot.runtime import enqueue as enqueue_task

    task = enqueue_task(db, principal, "growth.assistant", key, trace, request_payload=payload)
    existing = db.get(growth.GrowthRecord, task["id"])
    if existing:
        return growth.data(growth.owned(db, principal, task["id"], "agent_run"))
    return growth.save(
        db,
        principal,
        "agent_run",
        {
            "status": "queued",
            "input": payload,
            "steps": [],
            "result": None,
            "trace_id": trace,
            "mode": "local_controlled_tools",
        },
        trace,
        record_id=task["id"],
    )


def handler(engine, task_id, token, now):
    from careerpilot.runtime import checkpoint

    if not checkpoint(engine, task_id, token, "growth_tools", now):
        return
    with Session(engine) as db, db.begin():
        task = db.get(BackgroundTask, task_id)
        if task is None or task.lease_token != token or task.status != "running":
            return
        grant = db.get(RoleGrant, (task.user_id, task.tenant_id))
        principal = Principal(task.user_id, task.tenant_id, grant.role if grant else "student")
        row = growth.owned(db, principal, task_id, "agent_run")
        run = json.loads(row.payload_json)
        if run["status"] in {"success", "failed"}:
            return
        body = run["input"]
        try:
            intent = intent_from(body["message"]) if body["intent"] == "auto" else body["intent"]
            if intent in {"diagnose", "plan"}:
                args = {"job_ids": body["job_ids"]}
                if intent == "plan":
                    profile = learning.profile_context(db, principal)
                    args["start_date"] = (
                        datetime.now(ZoneInfo(profile["timezone"])).date().isoformat()
                    )
            elif intent == "replan":
                args = {"plan_id": body["plan_id"] or ""}
            elif intent == "resources":
                query = " ".join(
                    skill["id"]
                    for skill in catalogue.entries(db, "skill")
                    if any(
                        alias.casefold() in body["message"].casefold() for alias in skill["aliases"]
                    )
                )
                args = {"query": query or body["message"][:200]}
            else:
                args = {}
            spec = ToolSpec(
                "growth." + intent, 1 if intent in {"plan", "replan"} else 0, SCHEMAS[intent], ROLES
            )
            authorize_tool(db, principal, spec, args)
            if intent == "diagnose":
                result = learning.gap(db, principal, args["job_ids"])
                summary = f"已分析 {len(result['skills'])} 项技能要求，结论附原始岗位与证据。"
            elif intent == "plan":
                result = learning.create_plan(
                    db, principal, args["job_ids"], args["start_date"], None, task.trace_id
                )
                summary = f"已生成 {len(result['tasks'])} 项任务的计划草案；需要你检查并发布。"
            elif intent == "replan":
                result = learning.replan_preview(
                    db, principal, args["plan_id"], "UserRequested", task.trace_id
                )
                summary = "已生成重排差异预览，等待你接受或拒绝。"
            elif intent == "resources":
                result = search(db, args["query"], principal)
                summary = f"在官方来源索引中找到 {len(result['results'])} 项资源。"
            else:
                result = {"skills": learning.snapshots(db, principal), "active_plan": None}
                plan = learning.active_plan(db, principal)
                if plan:
                    result["active_plan"] = growth.data(plan)
                summary = "已读取当前技能证据和学习计划。"
            run.update(
                status="success",
                result=result,
                summary=summary,
                steps=[
                    {
                        "tool": spec.name,
                        "arguments": args,
                        "status": "success",
                        "finished_at": utc_now(),
                    }
                ],
            )
        except DomainError as exc:
            run.update(
                status="failed",
                result={"code": exc.code, "message": exc.message},
                summary=exc.message,
                steps=[{"status": "denied", "code": exc.code}],
            )
        growth.save(db, principal, "agent_run", run, task.trace_id, row, row.version)
