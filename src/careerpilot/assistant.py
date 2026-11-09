"""Single local assistant using schema-bound tools and the leased Worker runtime."""

import re

from careerpilot import growth
from careerpilot.domain import DomainError

ROLES = frozenset({"student", "operator", "admin"})
ARRAY_SCHEMA = {"type": "array", "items": {"type": "string", "maxLength": 36}, "maxItems": 20}
SCHEMAS = {
    "help": {"type": "object", "properties": {}, "additionalProperties": False},
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
    if any(
        word in message for word in ["进展", "进度", "完成情况", "掌握情况", "成长情况"]
    ) or re.search(r"\b(?:progress|how am i doing)\b", text):
        return "progress"
    return "help"


def help_reply(message):
    greeting = bool(re.fullmatch(r"(?:你好[呀啊]?|您好|嗨|hello|hi|hey)[!！。.\s]*", message, re.I))
    introduction = (
        "你好！我是你的成长任务助手。" if greeting else "我还没有把这条消息识别为具体任务。"
    )
    return {
        "kind": "assistant_help",
        "summary": introduction
        + "\n我可以帮你分析岗位差距、生成学习计划草案、查找学习资源、查看进展或预览重排。"
        "\n你可以发“查看当前进展”“给我 Python 学习资源”，或关联岗位后发“分析岗位差距”。"
        "\n目前是任务型助手，尚未接入开放式对话模型；不会把任意问题都当成读取进度。",
        "summary_en": (
            "Hello! I am your growth task assistant."
            if greeting
            else "I could not identify a specific task in this message."
        )
        + "\nI can analyze job gaps, draft study plans, find resources, "
        "read progress and preview replanning."
        "\nTry 'show my progress', 'find Python resources', "
        "or select jobs and ask for a gap analysis."
        "\nOpen-ended model chat is not connected yet.",
    }


def enqueue(db, principal, payload, key, trace):
    from careerpilot.runtime import enqueue as enqueue_task

    task = enqueue_task(db, principal, "growth.assistant", key, trace, request_payload=payload)
    existing = db.get(growth.GrowthRecord, task["id"])
    if existing:
        return growth.data(growth.owned(db, principal, task["id"], "agent_run"))
    if payload.get("conversation_id"):
        from careerpilot.conversations import attach

        attach(db, principal, payload, task["id"], trace)
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
            "mode": "langgraph",
            "graph_version": "growth.v1",
        },
        trace,
        record_id=task["id"],
    )


def handler(engine, task_id, token, now):
    from careerpilot.workflows import handler as graph_handler

    return graph_handler(engine, task_id, token, now)
