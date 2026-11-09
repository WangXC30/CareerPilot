"""Owned conversation history and a bounded model/tool loop in the sole LangGraph runtime."""

import asyncio
import copy
import json
import math
import os
import re
import sqlite3
from collections import Counter
from datetime import datetime
from difflib import SequenceMatcher
from functools import lru_cache
from typing import Any, TypedDict
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from careerpilot import (
    catalogue,
    chat_followup,
    chat_hiring,
    chat_model,
    chat_profile,
    growth,
    guidance,
    learning,
    resumes,
)
from careerpilot.chat_limits import MAX_TOOL_CALLS
from careerpilot.domain import DomainError
from careerpilot.platform_models import BackgroundTask
from careerpilot.safety import require_consent, scan_upload
from careerpilot.tools import ToolSpec, authorize_tool
from careerpilot.web_sources import search


def schema(properties=None, required=None):
    return {
        "type": "object",
        "properties": properties or {},
        "required": required or [],
        "additionalProperties": False,
    }


ID = {"type": "string", "minLength": 1, "maxLength": 36}
VERSION = {"type": "integer", "minimum": 1}
MINUTES = {"type": "integer", "minimum": 30, "maximum": 10080}
TOOLS = {
    **chat_profile.TOOLS,
    **chat_hiring.TOOLS,
    **chat_followup.TOOLS,
    "progress": schema(),
    "check_requirements": schema(
        {
            "action": {"enum": ["explore", "plan", "match", "resume", "interview", "adjust_plan"]},
            "job_id": ID,
        },
        ["action"],
    ),
    "read_history": schema({"query": {"type": "string", "maxLength": 200}}, ["query"]),
    "read_context": schema(
        {
            "section": {
                "enum": [
                    "profile",
                    "profile_draft",
                    "goals",
                    "experience",
                    "jobs",
                    "skills",
                    "plans",
                    "previews",
                    "preferences",
                    "projects",
                    "project_assessments",
                    "interview_reviews",
                    *chat_hiring.SECTIONS,
                ]
            },
            "query": {"type": "string", "maxLength": 200},
        },
        ["section"],
    ),
    "diagnose": schema({"job_ids": {"type": "array", "items": ID, "maxItems": 20}}, ["job_ids"]),
    "resources": schema({"query": {"type": "string", "minLength": 1, "maxLength": 200}}, ["query"]),
    "search_technical": schema(
        {"query": {"type": "string", "minLength": 1, "maxLength": 200}}, ["query"]
    ),
    "import_job": schema(
        {
            "content": {"type": "string", "minLength": 1, "maxLength": 12000},
            "source_url": {"type": ["string", "null"], "maxLength": 2000},
        },
        ["content"],
    ),
    "plan": schema(
        {
            "job_ids": {"type": "array", "items": ID, "maxItems": 20},
            "start_date": {"type": "string", "format": "date"},
            "weekly_minutes": MINUTES,
            "first_week_minutes": MINUTES,
        },
        ["job_ids"],
    ),
    "revise_plan": schema(
        {
            "plan_id": ID,
            "version": VERSION,
            "weekly_minutes": MINUTES,
            "first_week_minutes": MINUTES,
            "start_date": {"type": "string", "format": "date"},
        },
        ["plan_id", "version"],
    ),
    "replan": schema({"plan_id": ID, "version": VERSION}, ["plan_id", "version"]),
    "publish_plan": schema({"plan_id": ID, "version": VERSION}, ["plan_id", "version"]),
    "decide_preview": schema(
        {"preview_id": ID, "version": VERSION, "accept": {"type": "boolean"}},
        ["preview_id", "version", "accept"],
    ),
    "remember": schema({"text": {"type": "string", "minLength": 1, "maxLength": 500}}, ["text"]),
    "forget_preference": schema(
        {"preference_id": ID, "version": VERSION}, ["preference_id", "version"]
    ),
}
WRITES = {
    *chat_profile.TOOLS,
    *chat_hiring.WRITES,
    *chat_followup.TOOLS,
    "plan",
    "revise_plan",
    "replan",
    "publish_plan",
    "decide_preview",
    "remember",
    "forget_preference",
    "import_job",
}
DECISION = schema(
    {
        "action": {"enum": ["answer", *TOOLS]},
        "answer": {"type": "string", "minLength": 1, "maxLength": 8000},
        "arguments": {"type": "object"},
        "source_ids": {
            "type": "array",
            "items": {"type": "string", "maxLength": 100},
            "maxItems": 12,
        },
    },
    ["action", "answer", "arguments", "source_ids"],
)
SUMMARY = schema({"summary": {"type": "string", "maxLength": 2200}}, ["summary"])
ROLES = frozenset({"student", "operator", "admin"})


def explicit_job_text(message: str) -> str | None:
    """Recognize a narrow, explicit 'following job text' prefix, not arbitrary prose."""
    match = re.match(
        r"^\s*(?:(?:请|麻烦)?(?:帮我)?(?:导入|保存|添加)(?:以下|下列)"
        r"(?:岗位|职位)(?:原文|描述)|(?:please\s+)?(?:import|save|add)\s+"
        r"(?:the\s+)?following\s+job\s+(?:description|text))\s*[：:][ \t]*",
        message,
        re.IGNORECASE,
    )
    if not match:
        return None
    return message[match.end() :].strip()


def finish_tools(state, reason):
    """Close the reply from actual receipts without another model call or business effect."""
    tools = state.get("tools", [])
    saved = sum(tool["status"] == "success" and tool["name"] in WRITES for tool in tools)
    failed = sum(tool["status"] == "failed" for tool in tools)
    declined = sum(tool["status"] == "declined" for tool in tools)
    unavailable = any(
        tool["result"].get("code") == "NOT_FOUND" for tool in tools if tool["status"] == "failed"
    )
    english = state.get("context", {}).get("locale") == "en-US"
    if english:
        summary = f"Saved {saved} changes; {failed} actions failed; {declined} were declined. "
        summary += (
            (
                "This step referenced an unavailable record. Choose the intended job or record "
                "and continue; the failed action was not repeated."
                if unavailable
                else "The failed or declined action was not repeated. Review its result and "
                "clarify any changes before continuing."
            )
            if reason == "repeated_failure"
            else "This request needs more steps. Saved results remain available below. "
            "You can continue in this conversation; unfinished actions have not been executed."
        )
    else:
        summary = f"已保存 {saved} 项变更；{failed} 项操作未成功；{declined} 项被拒绝。"
        summary += (
            (
                "当前操作引用的记录不存在或不可用，已停止重复尝试。"
                "请核对并选择目标岗位或资料后继续，尚未完成该操作。"
                if unavailable
                else "已停止重复失败或被拒绝的操作。请核对下方结果，说明需要调整的内容后继续；"
                "尚未完成的操作未执行。"
            )
            if reason == "repeated_failure"
            else "这项请求还需要后续步骤，已保存的结果保留在下方。"
            "可以在本对话继续处理，尚未完成的操作未执行。"
        )
    if reason in {"already_saved", "model_budget"}:
        summary = (
            "Your confirmed changes have been saved. " if english else "你确认的操作已经保存。"
        )
        if reason == "already_saved":
            summary += (
                "The same operation will not be created or confirmed again. "
                if english
                else "相同操作不会再次创建或要求确认。"
            )
        else:
            summary += (
                "The model reply reached its quota; saved results are retained below. "
                "Further actions have not been executed. "
                if english
                else "后续模型回复达到额度限制，已保存结果保留在下方，其他操作未继续执行。"
            )
        if any(
            tool["status"] == "success"
            and tool["name"] == "plan"
            and tool["result"].get("status") == "draft"
            for tool in tools
        ):
            summary += (
                "The study plan was saved as a draft. Publishing requires a separate confirmation."
                if english
                else "学习计划已保存为草稿，发布后才开始执行，发布需要单独确认。"
            )
    if reason == "repeated_progress":
        summary = (
            "Progress was retrieved, but the assistant repeated the same request. "
            "See the result below and continue with a more specific question."
            if english
            else "已读取学习进度，但助手重复请求了相同内容，已停止重复调用。"
            "请查看下方结果，或提出更具体的问题继续。"
        )
    if reason == "repeated_read":
        summary = (
            "The information was already checked, but the assistant repeated the request. "
            "Please continue in this conversation; no unfinished action was performed."
            if english
            else "资料已检查，但助手重复请求了相同内容，已停止重复调用。"
            "可以继续本次对话；尚未完成的操作没有执行。"
        )
    return {"action": "answer", "answer": summary, "arguments": {}, "source_ids": []}


def create(db, principal, title, trace, record_id=None):
    return growth.save(
        db,
        principal,
        "conversation",
        {
            "title": title or "新对话",
            "initial_title": title,
            "run_ids": [],
            "summary": "",
            "summary_upto": 0,
        },
        trace,
        record_id=record_id,
    )


def public(value):
    return {key: value[key] for key in ("id", "title", "version", "created_at")}


def attach(db, principal, payload, run_id, trace):
    """Called under the owner lock; idempotent delivery cannot append a turn twice."""
    row = growth.owned(db, principal, payload["conversation_id"], "conversation")
    conversation = growth.data(row)
    if run_id in conversation["run_ids"]:
        return
    for job_id in payload.get("job_ids", []):
        growth.owned(db, principal, job_id, "job")
    if payload.get("plan_id"):
        growth.owned(db, principal, payload["plan_id"], "plan")
    if len(conversation["run_ids"]) >= 500:
        raise DomainError("CHAT_LIMIT", "此对话已达 500 轮，请新建对话继续。", 422)
    for previous_id in conversation["run_ids"][-1:]:
        task = db.get(BackgroundTask, previous_id)
        if task and task.status in {"queued", "running", "awaiting_approval"}:
            raise DomainError("CHAT_BUSY", "请等待上一条回复，或先确认/取消上一项操作。", 409)
    conversation["run_ids"].append(run_id)
    if payload.get("job_ids"):
        conversation["job_ids"] = payload["job_ids"]
    if payload.get("plan_id"):
        conversation["plan_id"] = payload["plan_id"]
    if len(conversation["run_ids"]) == 1 and conversation["title"] == "新对话":
        conversation["title"] = payload["message"][:40]
    growth.save(db, principal, "conversation", clean_payload(conversation), trace, row, row.version)


def history(db, principal, conversation, run_id):
    ids = conversation["run_ids"]
    end = ids.index(run_id)
    turns = []
    for key in ids[conversation["summary_upto"] : end]:
        value = growth.data(growth.owned(db, principal, key, "agent_run"))
        task = db.get(BackgroundTask, key)
        status = task.status if task and task.status in {"failed", "cancelled"} else value["status"]
        answer = value.get("summary", "") if status == "succeeded" else ""
        # Never teach the model that a cancelled or failed operation succeeded.
        if value["status"] in {"success", "completed"} and status not in {"failed", "cancelled"}:
            answer = value.get("summary", "")
        turns.append(
            [
                {"role": "user", "content": value["input"]["message"]},
                {"role": "assistant", "content": answer or f"此轮状态：{status}；无成功结果。"},
            ]
        )
    return turns


def packed(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def clean_payload(value):
    return {key: item for key, item in value.items() if key not in {"id", "version", "created_at"}}


def bounded(value, limit=6000):
    """Keep valid JSON and mark omissions rather than slicing JSON mid-record."""
    if len(packed(value).encode()) <= limit:
        return value
    if isinstance(value, list):
        items: list[Any] = []
        for item in value:
            item = bounded(item, min(2500, limit))
            if len(packed([*items, item]).encode()) > limit - 100:
                break
            items.append(item)
        return {"items": items, "omitted": len(value) - len(items)}
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            candidate = bounded(item, min(2000, limit // 2))
            if len(packed({**result, key: candidate}).encode()) <= limit - 100:
                result[key] = candidate
        return {**result, "truncated": True}
    return str(value)[: max(0, (limit - 100) // 4)] + "…[已截短]"


def read_context(db, principal, section, query=""):
    if section == "profile_draft":
        from careerpilot.workflow_api import draft_id

        row = db.get(growth.GrowthRecord, draft_id(principal, "profile", "personal"))
        if not row:
            return {"items": [], "sources": []}
        draft = growth.data(row)
        profile = growth.profile(db, principal)
        profile_version = profile.version if profile else 0
        item = {
            "version": draft["version"],
            "values": draft.get("values", {}),
            "base_record_version": draft.get("base_record_version", 0),
            "profile_version": profile_version,
            "status": "draft_needs_review"
            if draft.get("base_record_version", 0) != profile_version
            else "private_draft_not_confirmed",
        }
        if query and query.casefold() not in packed(item).casefold():
            return {"items": [], "sources": []}
        return {
            "items": [bounded(item, 6000)],
            "sources": [
                {
                    "id": row.id,
                    "version": row.version,
                    "label": "私人档案草稿（未确认）",
                    "href": "/app/profile",
                }
            ],
        }
    if section == "skills":
        return {"items": learning.snapshots(db, principal), "sources": []}
    if section == "goals":
        return {"items": growth.goal_snapshot(db, principal), "sources": []}
    kind = {
        "profile": "profile",
        "experience": "memory",
        "jobs": "job",
        "plans": "plan",
        "previews": "plan_preview",
        "preferences": "chat_preference",
        "projects": "practice_project",
        "project_assessments": "project_assessment",
        "interview_reviews": "interview_review",
        **chat_hiring.SECTIONS,
    }[section]
    items, sources = [], []
    rows = db.scalars(
        growth.owner_query(principal, kind).order_by(
            growth.GrowthRecord.created_at.desc(), growth.GrowthRecord.id.desc()
        )
    )
    for row in rows:
        item = growth.data(row)
        if kind == "project_assessment":
            item.pop("lease_token_hash", None)
        if kind == "resume_fact":
            item["source_status"] = resumes.fact_status(db, principal, item)
        elif kind == "resume_version":
            item["source_status"] = resumes.version_status(db, principal, item)
        if kind == "plan_preview":
            item = {
                key: item[key]
                for key in (
                    "id",
                    "version",
                    "plan_id",
                    "base_version",
                    "status",
                    "reason",
                    "validation_error",
                )
                if key in item
            }
        if (
            item.get("deleted")
            or kind in {"practice_project", "project_assessment", "interview_review"}
            and (item.get("user_archived") or item.get("status") in {"withdrawn", "deleted"})
            or kind == "memory"
            and (
                item.get("deleted")
                or item.get("revoked")
                or item.get("status") in {"revoked", "deleted"}
            )
        ):
            continue
        if query and query.casefold() not in packed(item).casefold():
            continue
        if kind == "profile":
            item = {
                key: item[key]
                for key in (
                    "id",
                    "version",
                    "education",
                    "major",
                    "academic_year",
                    "weekly_minutes",
                    "timezone",
                    "preferred_cities",
                    "graduation_date",
                    "expected_monthly_salary",
                    "preferences",
                )
                if key in item
            }
        if kind == "plan":
            totals: Counter = Counter()
            for task in item.get("tasks", []):
                if task["status"] != "skipped":
                    totals[learning.week(task["date"])] += task["minutes"]
            item = {
                key: item[key]
                for key in (
                    "id",
                    "version",
                    "status",
                    "start_date",
                    "deadline",
                    "job_ids",
                    "mode_budget_minutes",
                    "first_week_minutes",
                )
                if key in item
            } | {
                "weekly_totals": dict(totals),
                "tasks": bounded(growth.data(row).get("tasks", []), 3500),
            }
        items.append(item)
        sources.append(
            {
                "id": row.id,
                "label": item.get("title", section),
                "version": row.version,
                "href": "/app/"
                + (
                    "goals"
                    if kind == "job"
                    else "learn"
                    if kind in {"plan", "plan_preview", "practice_project", "project_assessment"}
                    else "career"
                    if section in chat_hiring.SECTIONS or kind == "interview_review"
                    else "profile"
                ),
            }
        )
        if len(items) == 12:
            break
    return {"items": bounded(items, 7000), "sources": sources}


@lru_cache(maxsize=1)
def _technical_model(directory):
    from careerpilot.rag_embedding import E5Embedder, model_identity

    return E5Embedder(directory), model_identity(directory)


@lru_cache(maxsize=1)
def _technical_reranker(directory):
    from careerpilot.rag_embedding import BGEReranker

    return BGEReranker(directory)


def technical_search(query):
    from careerpilot.technical_rag import TECHNICAL_ALIASES
    from careerpilot.technical_rag import search as local_search

    index = os.environ.get('CAREERPILOT_RAG_INDEX', 'data/rag_index/technical.sqlite3')
    directory = os.environ.get('CAREERPILOT_RAG_MODEL', 'data/rag_models/multilingual-e5-small')
    reranker_dir = os.environ.get('CAREERPILOT_RAG_RERANKER', 'data/rag_models/bge-reranker-v2-m3')
    try:
        model, identity = _technical_model(directory)
        found = local_search(index, query, model, identity, limit=20)
        if found:
            reranker = _technical_reranker(reranker_dir)
            aliases = [value for key, value in TECHNICAL_ALIASES.items() if key in query]
            rerank_query = query + (' ' + ' '.join(aliases) if aliases else '')
            scores = reranker(rerank_query, [item['text'] for item in found])
            if len(scores) != len(found) or not all(math.isfinite(score) for score in scores):
                raise ValueError('Reranker result count mismatch')
            ordered = sorted(zip(scores, found), key=lambda pair: pair[0], reverse=True)
            found, counts = [], Counter()
            for score, item in ordered:
                if score < -2:
                    continue
                title = item['source']['title']
                if counts[title] >= 2:
                    continue
                if any(previous['source']['title'] == title and SequenceMatcher(
                    None, previous['text'], item['text'], autojunk=False
                ).ratio() >= 0.8 for previous in found):
                    continue
                found.append(item)
                counts[title] += 1
                if len(found) == 4:
                    break
    except (OSError, ImportError, ValueError, RuntimeError, sqlite3.Error) as exc:
        raise DomainError(
            'RAG_UNAVAILABLE', '本地技术资料库不可用，请检查模型和索引。', 503
        ) from exc
    items, sources = [], []
    for result in found:
        origin = result['source']
        page = f" · PDF 第 {origin['page']} 页" if origin.get('page') else ''
        items.append({'source_id': result['id'], 'excerpt': result['text']})
        href = origin['url'] + (f"#page={origin['page']}" if origin.get('page') else '')
        label = origin['title'] + page + ' · ' + origin.get('license', '')
        sources.append({'id': result['id'], 'label': label, 'href': href})
    return {'items': items, 'sources': sources,
            'notice': ('检索结果仅是候选依据；应核对内容，不能将不相关内容用于回答。'
                       if items else '未找到足够相关的资料；不得编造来源。')}


def authorize(db, principal, action, args):
    if action not in TOOLS:
        raise DomainError("TOOL_DENIED", "此工具未向成长助手开放。", 403)
    authorize_tool(
        db, principal, ToolSpec("chat." + action, int(action in WRITES), TOOLS[action], ROLES), args
    )
    # JSON Schema format is optional in the generic validator; validate dates explicitly.
    if action in {"plan", "revise_plan"} and "start_date" in args:
        try:
            datetime.strptime(args["start_date"], "%Y-%m-%d")
        except ValueError:
            raise DomainError("CHAT_DATE_INVALID", "开始日期格式不正确。", 422) from None


def checked(db, principal, key, kind, version):
    row = growth.owned(db, principal, key, kind)
    if row.version != version:
        raise DomainError("VERSION_CONFLICT", "资料已改变，请重新读取并预览。", 412)
    return growth.data(row)


def adjust(db, principal, candidate, args, protected=None):
    candidate = clean_payload(candidate)
    if (
        not growth.profile(db, principal)
        and "budget_cap_minutes" in candidate
        and "weekly_minutes" in args
    ):
        # The explicitly confirmed plan budget is local to this plan, not a profile constraint.
        candidate["budget_cap_minutes"] = args["weekly_minutes"]
    maximum = learning.profile_context(db, principal, candidate)["weekly_minutes"]
    budget = args.get("weekly_minutes", learning.plan_budget(candidate, maximum))
    if budget > maximum:
        raise DomainError("BUDGET_EXCEEDED", "此次预算超过档案预算，请先在我的资料修改。", 422)
    candidate["mode_budget_minutes"] = budget
    candidate["start_date"] = args.get("start_date", candidate["start_date"])
    first = args.get("first_week_minutes", candidate.get("first_week_minutes"))
    if first is not None:
        candidate["first_week_minutes"] = min(first, budget)
    protected = protected or []
    fixed_ids = {task["id"] for task in protected}
    pending = [t for t in candidate["tasks"] if t["id"] not in fixed_ids]
    # Split large tasks only to meet the general weekly budget. A first-week cap can
    # defer a whole task; it never deletes learning work or changes protected tasks.
    candidate["tasks"] = learning.schedule(
        learning.split_for_budget(pending, budget),
        candidate["start_date"],
        budget,
        protected,
        candidate.get("first_week_minutes"),
    )
    candidate["validation"] = learning.validate_plan(
        candidate,
        maximum,
        catalogue.skill_map(db),
        protected,
    )
    return candidate


def proposal(db, principal, action, args):
    """Compute reviewable content without writing business state."""
    if action in chat_hiring.WRITES:
        return chat_hiring.proposal(db, principal, action, args)
    if action in chat_followup.TOOLS:
        return chat_followup.proposal(db, principal, action, args)
    if action in chat_profile.TOOLS:
        return chat_profile.proposal(db, principal, action, args)
    if action == "import_job":
        candidate = growth.job_payload(args["content"], args.get("source_url"))
        jobs = [
            {"id": row.id, "version": row.version, "payload": json.loads(row.payload_json)}
            for row in db.scalars(
                growth.owner_query(principal, "job").order_by(growth.GrowthRecord.id)
            )
        ]
        source = candidate["sources"][0]["source_url"]
        return {
            "summary": "保存以下用户提供的岗位原文；相同来源可能更新已有岗位，请核对。",
            "preview": {"text": args["content"] + ("\n\n来源：" + source if source else "")},
            "context_hash": growth.payload_hash({"jobs": jobs}),
        }
    if action == "remember":
        scan_upload(args["text"].encode(), "preference.txt")
        return {
            "summary": "保存这条跨会话偏好；它不会更新档案或证明技能掌握。",
            "preview": {"text": args["text"]},
            "context_hash": None,
        }
    if action == "forget_preference":
        preference = checked(
            db, principal, args["preference_id"], "chat_preference", args["version"]
        )
        return {
            "summary": "忘记以下跨会话偏好。历史聊天仍可查阅。",
            "preview": {"text": preference["text"]},
            "context_hash": None,
        }
    if "plan_id" in args:
        checked(db, principal, args["plan_id"], "plan", args["version"])
    if "preview_id" in args:
        checked(db, principal, args["preview_id"], "plan_preview", args["version"])
    context = learning.context_hash(db, principal)
    if action == "plan":
        previous = learning.active_plan(db, principal)
        active = growth.data(previous) if previous else None
        if not growth.profile(db, principal) and "weekly_minutes" not in args and not active:
            raise DomainError("BUDGET_REQUIRED", "请先明确每周学习时间预算", 422)
        profile = learning.profile_context(
            db, principal, active, weekly_minutes=args.get("weekly_minutes")
        )
        day = (
            args.get("start_date") or datetime.now(ZoneInfo(profile["timezone"])).date().isoformat()
        )
        candidate = adjust(
            db,
            principal,
            learning.build_plan(
                db, principal, args["job_ids"], day, weekly_minutes=profile["weekly_minutes"]
            ),
            args,
        )
        return {
            "summary": "生成以下学习计划草案，保存后仍需确认发布。",
            "preview": candidate,
            "candidate": candidate,
            "context_hash": context,
        }
    if action in {"revise_plan", "replan", "publish_plan"}:
        plan = checked(db, principal, args["plan_id"], "plan", args["version"])
        if action == "publish_plan":
            return {
                "summary": "确认发布这份计划，替换当前可替换的已发布计划。",
                "preview": plan,
                "context_hash": context,
            }
        if plan["status"] not in {"active", "draft"}:
            raise DomainError("INVALID_STATE", "请选当前已发布计划或草案。", 422)
        if action == "replan":
            preview = learning.build_replan_preview(db, principal, plan["id"], "ChatRequested")
            return {
                "summary": "保存以下重排预览，接受前不会替换当前计划。",
                "preview": preview["candidate"],
                "record": preview,
                "context_hash": context,
            }
        candidate = adjust(db, principal, copy.deepcopy(plan), args, learning.protected_tasks(plan))
        return {
            "summary": "保存以下计划调整；已发布计划仅生成重排预览，接受前不会替换。",
            "preview": candidate,
            "candidate": candidate,
            "context_hash": context,
        }
    if action == "decide_preview":
        preview = checked(db, principal, args["preview_id"], "plan_preview", args["version"])
        return {
            "summary": "接受这份重排并更新计划。" if args["accept"] else "拒绝这份重排。",
            "preview": preview["candidate"],
            "context_hash": context,
        }
    raise DomainError("TOOL_DENIED", "此工具没有可确认的写入内容。", 403)


def execute(db, principal, action, args, proposed, trace, conversation_id, run_id):
    if action in chat_followup.TOOLS:
        return chat_followup.execute(db, principal, action, args, proposed, trace, run_id)
    if action == "import_job":
        growth.lock_owner(db, principal)
        if proposal(db, principal, action, args)["context_hash"] != proposed["context_hash"]:
            raise DomainError("STALE_PREVIEW", "岗位资料已改变，请重新预览。", 412)
        saved = growth.import_job(db, principal, args["content"], args.get("source_url"), trace)
        return {
            "kind": "job_import",
            "saved": True,
            "id": saved["id"],
            "version": saved["version"],
            "deduplicated": saved["deduplicated"],
            "href": "/app/goals",
            "sources": [
                {
                    "id": saved["id"],
                    "version": saved["version"],
                    "label": "岗位原文",
                    "href": "/app/goals",
                }
            ],
        }
    if action in chat_hiring.TOOLS:
        return chat_hiring.execute(db, principal, action, args, proposed, trace, run_id)
    if action in chat_profile.TOOLS:
        return chat_profile.execute(db, principal, action, proposed, trace, conversation_id, run_id)
    if action == "progress":
        plan = learning.active_plan(db, principal)
        return {
            "skills": learning.snapshots(db, principal),
            "active_plan": growth.data(plan) if plan else None,
        }
    if action == "check_requirements":
        return guidance.requirements(db, principal, args["action"], args.get("job_id"))
    if action == "read_history":
        conversation = growth.data(growth.owned(db, principal, conversation_id, "conversation"))
        matches = []
        for key in reversed(conversation["run_ids"]):
            run = growth.data(growth.owned(db, principal, key, "agent_run"))
            task = db.get(BackgroundTask, key)
            item = {
                "message": run["input"]["message"],
                "answer": run.get("summary", ""),
                "status": task.status
                if task and task.status in {"failed", "cancelled"}
                else run["status"],
                "tools": (run.get("result") or {}).get("tools", []),
            }
            if args["query"].casefold() in packed(item).casefold():
                matches.append(item)
            if len(matches) == 8:
                break
        return {"items": bounded(matches, 6500)}
    if action == "read_context":
        output = read_context(db, principal, **args)
        return (
            {**output, "kind": "practice_checks"}
            if args["section"] == "project_assessments"
            else output
        )
    if action == "diagnose":
        return learning.gap(db, principal, args["job_ids"])
    if action == "resources":
        return search(db, args["query"], principal)
    if action == "search_technical":
        return technical_search(args["query"])
    if action not in {"remember", "forget_preference"} and proposed[
        "context_hash"
    ] != learning.context_hash(db, principal):
        raise DomainError("STALE_PREVIEW", "资料或能力证据已改变，请重新预览。", 412)
    if action == "plan":
        return growth.save(db, principal, "plan", proposed["candidate"], trace)
    if action in {"revise_plan", "replan"}:
        old = checked(db, principal, args["plan_id"], "plan", args["version"])
        if action == "replan":
            return growth.save(db, principal, "plan_preview", proposed["record"], trace)
        candidate = proposed["candidate"]
        if old["status"] == "draft":
            row = growth.owned(db, principal, old["id"], "plan")
            return growth.save(db, principal, "plan", candidate, trace, row, row.version)
        before = {t["id"]: t for t in old["tasks"]}
        after = {t["id"]: t for t in candidate["tasks"]}
        return growth.save(
            db,
            principal,
            "plan_preview",
            {
                "plan_id": old["id"],
                "base_version": old["version"],
                "reason": "ChatRequested",
                "status": "pending",
                "context_hash": proposed["context_hash"],
                "candidate": candidate,
                "validation_error": None,
                "diff": {
                    "added": [t for k, t in after.items() if k not in before],
                    "removed": [t for k, t in before.items() if k not in after],
                    "changed": [
                        {"before": before[k], "after": t}
                        for k, t in after.items()
                        if k in before and before[k] != t
                    ],
                },
            },
            trace,
        )
    if action == "publish_plan":
        return learning.publish_plan(db, principal, args["plan_id"], args["version"], trace)
    if action == "decide_preview":
        return learning.decide_preview(
            db, principal, args["preview_id"], args["accept"], args["version"], trace
        )
    if action == "forget_preference":
        checked(db, principal, args["preference_id"], "chat_preference", args["version"])
        return forget(db, principal, args["preference_id"], args["version"], trace)
    return growth.save(db, principal, "chat_preference", {"text": args["text"]}, trace)


def forget(db, principal, preference_id, version, trace):
    row = growth.owned(db, principal, preference_id, "chat_preference")
    return growth.save(
        db, principal, "chat_preference", {"text": "", "deleted": True}, trace, row, version
    )


class ChatState(TypedDict, total=False):
    run_id: str
    intent: str
    arguments: dict[str, Any]
    history: list
    context: dict
    sources: list
    tools: list
    rounds: int
    decision: dict
    proposed: dict
    approved: bool
    summary: str
    result: dict
    status: str


def build_graph(engine, task_id, checkpointer, transaction):
    """Every I/O boundary releases SQL locks; each tool effect has a durable receipt."""

    def invoke(principal, trace, data, output_schema, suffix, max_tokens=1800):
        return asyncio.run(
            chat_model.gateway(engine).generate(
                principal,
                task_id,
                packed(data),
                output_schema,
                prompt_version=chat_model.PROMPT_VERSION + ":" + suffix,
                schema_version="growth.chat.v1",
                trace_id=trace,
                max_tokens=max_tokens,
                retries=1,
                retry_on=(
                    frozenset({"MODEL_EMPTY_RESPONSE", "MODEL_INCOMPLETE_ANSWER"})
                    if output_schema is DECISION
                    else frozenset({"MODEL_EMPTY_RESPONSE"})
                ),
            )
        )

    def load(state):
        with transaction() as (db, task, principal):
            run = growth.data(growth.owned(db, principal, task_id, "agent_run"))
            conversation = growth.data(
                growth.owned(db, principal, run["input"]["conversation_id"], "conversation")
            )
            turns = history(db, principal, conversation, task_id)
            old = []
            while len(turns) > 6 or len(packed(turns).encode()) > 10000:
                turn = turns.pop(0)
                old.append(
                    [{**message, "content": bounded(message["content"], 7000)} for message in turn]
                )
            summary = conversation["summary"]
            trace = task.trace_id
            recent_actions = []
            position = conversation["run_ids"].index(task_id)
            for key in conversation["run_ids"][max(0, position - 4) : position]:
                previous = growth.data(growth.owned(db, principal, key, "agent_run"))
                for item in (previous.get("result") or {}).get("tools", []):
                    value = item["result"]
                    reference = {
                        key: value[key]
                        for key in (
                            "id",
                            "version",
                            "plan_id",
                            "base_version",
                            "status",
                            "resume_id",
                            "resume_version_id",
                            "sequence",
                            "saved",
                            "code",
                        )
                        if key in value
                    }
                    recent_actions.append(
                        {
                            "name": item["name"],
                            "arguments": item["arguments"],
                            "status": item["status"],
                            "reference": reference,
                        }
                    )
        if old:
            # Process complete old turns in bounded batches. Store only after the model
            # succeeds; failure never discards original messages or advances the boundary.
            batch: list[Any] = []
            for turn in old:
                if len(packed([*batch, turn]).encode()) > 15000 and batch:
                    result = invoke(
                        principal,
                        trace,
                        {
                            "message": "总结目标、约束、纠正、指代和实际结果，不新增事实。",
                            "previous_summary": summary,
                            "turns": batch,
                        },
                        SUMMARY,
                        "summary",
                        1200,
                    )
                    summary, batch = result["summary"], []
                batch.append(turn)
            if batch:
                result = invoke(
                    principal,
                    trace,
                    {
                        "message": "压缩历史，保留目标、约束、纠正、指代和实际结果。不要新增事实。",
                        "previous_summary": summary,
                        "turns": batch,
                    },
                    SUMMARY,
                    "summary",
                    1200,
                )
                summary = result["summary"]
            with transaction() as (db, task, principal):
                require_consent(db, principal, "external_model")
                row = growth.owned(db, principal, conversation["id"], "conversation")
                current = growth.data(row)
                current.update(
                    summary=summary, summary_upto=conversation["summary_upto"] + len(old)
                )
                growth.save(
                    db, principal, "conversation", clean_payload(current), trace, row, row.version
                )
        with transaction() as (db, task, principal):
            profile = read_context(db, principal, "profile")
            goals = read_context(db, principal, "goals")
            preferences = read_context(db, principal, "preferences")
            timezone = (
                profile["items"][0].get("timezone", "Asia/Shanghai")
                if profile["items"]
                else "Asia/Shanghai"
            )
            context = {
                "today": datetime.now(ZoneInfo(timezone)).date().isoformat(),
                "timezone": timezone,
                "profile": bounded(profile["items"], 1600),
                "goals": bounded(goals["items"], 1600),
                "preferences": bounded(preferences["items"], 1500),
                "summary": summary,
                "linked_jobs": run["input"].get("job_ids") or conversation.get("job_ids", []),
                "linked_plan": run["input"].get("plan_id") or conversation.get("plan_id"),
                "locale": run["input"].get("locale"),
                "recent_actions": bounded(recent_actions, 2200),
            }
        return {
            "run_id": task_id,
            "intent": "chat",
            "arguments": {},
            "history": sum(turns, []),
            "context": context,
            "sources": profile["sources"] + preferences["sources"],
            "tools": [],
            "rounds": 0,
        }

    def model(state):
        receipt_id = str(
            uuid5(NAMESPACE_URL, f"careerpilot:{task_id}:chat.model:{state['rounds']}")
        )
        with transaction() as (db, task, principal):
            require_consent(db, principal, "external_model")
            if receipt := db.get(growth.GrowthRecord, receipt_id):
                return {"decision": json.loads(receipt.payload_json)["decision"]}
            body = growth.data(growth.owned(db, principal, task_id, "agent_run"))["input"]
            trace = task.trace_id
        # Bound execution in code. Do not ask a model to stop and then fail the whole
        # run if it chooses another tool. Keep completed receipts visible instead.
        if state["rounds"] >= MAX_TOOL_CALLS:
            decision = finish_tools(state, "step_budget")
        else:
            decision = None
        data = {
            "message": body["message"],
            "history": state["history"],
            "context": state["context"],
            "sources": state["sources"],
            "tools": TOOLS,
            "tools_allowed": state["rounds"] < MAX_TOOL_CALLS,
            "remaining_tool_calls": max(0, MAX_TOOL_CALLS - state["rounds"]),
            "tool_results": bounded(state["tools"][-4:], 6500),
            "tool_history": [
                {
                    "name": tool["name"],
                    "status": tool["status"],
                    "id": tool["result"].get("id"),
                    "code": tool["result"].get("code"),
                }
                for tool in state["tools"]
            ],
        }
        while data["history"] and len(packed(data).encode()) > 31000:
            data["history"] = data["history"][2:]
            data["history_note"] = "部分历史未放入本轮窗口；需要细节时用 read_history 回查。"
        if decision is None and len(packed(data).encode()) > 31000:
            raise DomainError("MODEL_LIMIT", "对话内容过长，请新建对话或缩小资料范围。", 422)
        if decision is None:
            try:
                decision = invoke(principal, trace, data, DECISION, "decision")
            except DomainError as exc:
                if exc.code not in {"QUOTA_EXCEEDED", "MODEL_LIMIT"} or not any(
                    tool["status"] == "success" and tool["name"] in WRITES
                    for tool in state["tools"]
                ):
                    raise
                decision = {**finish_tools(state, "model_budget"), "reply_warning": exc.code}
            if any(
                (
                    tool["status"] == "declined"
                    or tool is state["tools"][-1]
                    and tool["status"] == "failed"
                )
                and tool["name"] == decision["action"]
                and tool["arguments"] == decision["arguments"]
                for tool in state["tools"]
            ):
                decision = finish_tools(state, "repeated_failure")
            elif decision["action"] in WRITES and any(
                tool["status"] == "success"
                and tool["name"] == decision["action"]
                and tool["arguments"] == decision["arguments"]
                for tool in state["tools"]
            ):
                decision = finish_tools(state, "already_saved")
            elif decision["action"] in {"check_requirements", "read_context"} and any(
                tool["status"] == "success"
                and tool["name"] == decision["action"]
                and tool["arguments"] == decision["arguments"]
                for index, tool in enumerate(state["tools"])
                if not any(
                    t["status"] == "success" and t["name"] in WRITES
                    for t in state["tools"][index + 1 :]
                )
            ):
                decision = finish_tools(state, "repeated_read")
            elif (
                decision["action"] == "progress"
                and state["tools"]
                and (
                    state["tools"][-1]["name"] == "progress"
                    and state["tools"][-1]["status"] == "success"
                    and state["tools"][-1]["arguments"] == decision["arguments"]
                )
            ):
                decision = finish_tools(state, "repeated_progress")
        with transaction() as (db, task, principal):
            require_consent(db, principal, "external_model")
            growth.save(
                db,
                principal,
                "chat_model_receipt",
                {"decision": decision},
                trace,
                record_id=receipt_id,
            )
        return {"decision": decision}

    def confirm(state):
        decision = state["decision"]
        action, args = decision["action"], decision["arguments"]
        with transaction() as (db, task, principal):
            authorize(db, principal, action, args)
            try:
                if action == "import_job":
                    message = growth.data(growth.owned(db, principal, task_id, "agent_run"))[
                        "input"
                    ]["message"]
                    explicit = explicit_job_text(message)
                    if args["content"] not in message or (
                        args.get("source_url") and args["source_url"] not in message
                    ):
                        raise DomainError(
                            "JOB_TEXT_NOT_PROVIDED",
                            "请直接粘贴完整岗位原文和来源，不使用模型改写的内容。",
                            422,
                        )
                    if explicit is not None and args["content"].strip() != explicit:
                        raise DomainError(
                            "JOB_TEXT_INCOMPLETE",
                            "岗位原文已提供，但提取内容不完整；请使用本轮完整原文重新预览。",
                            422,
                        )
                proposed = proposal(db, principal, action, args) if action in WRITES else {}
            except DomainError as exc:
                proposed = {"error": {"code": exc.code, "message": exc.message}}
        return {"proposed": proposed}

    def approval(state):
        if state["decision"]["action"] not in WRITES or state["proposed"].get("error"):
            return {"approved": True}
        receipt_id = str(uuid5(NAMESPACE_URL, f"careerpilot:{task_id}:chat.tool:{state['rounds']}"))
        with transaction() as (db, task, principal):
            if db.get(growth.GrowthRecord, receipt_id):
                return {"approved": True}
        # This node is replayed on resume, so the already-checkpointed proposal stays fixed.
        proposed = state["proposed"]
        approved = interrupt(
            {
                "summary": proposed["summary"],
                "preview": proposed["preview"],
                "action": state["decision"]["action"],
                "accept": state["decision"]["arguments"].get("accept"),
            }
        )
        return {"approved": approved is True}

    def tool(state):
        decision = state["decision"]
        action, args = decision["action"], decision["arguments"]
        receipt_id = str(uuid5(NAMESPACE_URL, f"careerpilot:{task_id}:chat.tool:{state['rounds']}"))
        with transaction() as (db, task, principal):
            require_consent(db, principal, "external_model")
            receipt = db.get(growth.GrowthRecord, receipt_id)
            if receipt:
                output = json.loads(receipt.payload_json)
            else:
                authorize(db, principal, action, args)
                if state["proposed"].get("error"):
                    output = {
                        "name": action,
                        "arguments": args,
                        "status": "failed",
                        "result": state["proposed"]["error"],
                    }
                elif not state["approved"]:
                    output = {
                        "name": action,
                        "arguments": args,
                        "status": "declined",
                        "result": {"message": "用户拒绝，未执行。"},
                    }
                else:
                    # A rejected tool result is data for the next model step, never success.
                    try:
                        with db.begin_nested():
                            result = execute(
                                db,
                                principal,
                                action,
                                args,
                                state["proposed"],
                                task.trace_id,
                                growth.data(growth.owned(db, principal, task_id, "agent_run"))[
                                    "input"
                                ]["conversation_id"],
                                task_id,
                            )
                        output = {
                            "name": action,
                            "arguments": args,
                            "status": "success",
                            "result": result,
                        }
                    except DomainError as exc:
                        output = {
                            "name": action,
                            "arguments": args,
                            "status": "failed",
                            "result": {"code": exc.code, "message": exc.message},
                        }
                if output["status"] == "failed":
                    output["result"]["recovery"] = (
                        "Do not repeat identical arguments. Read current owned records first "
                        "(jobs for job_ids; plans for plan_id; resume_facts/resumes for resumes). "
                        "Use only returned IDs; if no target exists, ask the user. "
                        "For a learning plan without a JD, use job_ids=[] and the current goal."
                    )
                growth.save(
                    db, principal, "chat_tool_receipt", output, task.trace_id, record_id=receipt_id
                )
            sources = {item["id"]: item for item in state["sources"]}
            for item in output["result"].get("sources", []):
                if isinstance(item, dict) and "id" in item:
                    sources[item["id"]] = item
            # Publish committed tool effects before waiting for another model reply.
            # Confirmation acknowledgement and business persistence are distinct states.
            row = growth.owned(db, principal, task_id, "agent_run")
            run = clean_payload(growth.data(row))
            run["result"] = {
                "kind": "assistant_chat",
                "tools": [*state["tools"], output],
                "citations": [],
            }
            confirmation = run.get("confirmation_response") or {}
            if (
                action in WRITES
                and confirmation.get("action") == action
                and confirmation.get("status") in {"accepted", "declined"}
            ):
                run["confirmation_response"] = {
                    "action": action,
                    "status": "saved" if output["status"] == "success" else output["status"],
                    "result_id": output["result"].get("id"),
                }
            growth.save(db, principal, "agent_run", run, task.trace_id, row, row.version)
            from careerpilot.workflows import append_event

            append_event(
                db,
                principal,
                task_id,
                f"chat.tool:{state['rounds']}",
                "tool",
                {"action": action, "status": output["status"]},
            )
        return {
            "tools": [*state["tools"], output],
            "rounds": state["rounds"] + 1,
            "sources": list(sources.values()),
        }

    def answer(state):
        decision = state["decision"]
        citations = [item for item in state["sources"] if item["id"] in decision["source_ids"]]
        # References for concrete tool results remain attached even if the model omits citations.
        return {
            "summary": decision["answer"],
            "status": "completed",
            "result": {
                "kind": "assistant_chat",
                "citations": citations,
                "tools": state["tools"],
                **(
                    {"reply_warning": decision["reply_warning"]}
                    if decision.get("reply_warning")
                    else {}
                ),
            },
        }

    graph = StateGraph(ChatState)
    for name, handler in [
        ("load", load),
        ("model", model),
        ("prepare_tool", confirm),
        ("approval", approval),
        ("tool", tool),
        ("answer", answer),
    ]:
        graph.add_node(name, handler)
    graph.add_edge(START, "load")
    graph.add_edge("load", "model")
    graph.add_conditional_edges(
        "model", lambda s: "answer" if s["decision"]["action"] == "answer" else "prepare_tool"
    )
    graph.add_edge("prepare_tool", "approval")
    graph.add_edge("approval", "tool")
    graph.add_edge("tool", "model")
    graph.add_edge("answer", END)
    return graph.compile(checkpointer=checkpointer)
