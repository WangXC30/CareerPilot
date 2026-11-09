"""The sole assistant orchestrator: LangGraph + durable SQLite checkpoints.

The existing queue owns dispatch leases, not graph steps. Domain effects and a
stable receipt commit together, so replay after checkpoint loss is harmless.
"""

import json
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, TypedDict
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from careerpilot import catalogue, growth, learning
from careerpilot.chat_limits import GRAPH_RECURSION_LIMIT, MAX_TOOL_CALLS
from careerpilot.domain import DomainError, Principal
from careerpilot.platform_models import BackgroundTask, RoleGrant
from careerpilot.tools import ToolSpec, authorize_tool
from careerpilot.web_sources import search
from careerpilot.workflow_models import WorkflowEvent

GRAPH_VERSION = "growth.v1"
TERMINAL = {"completed", "failed", "cancelled"}


class State(TypedDict, total=False):
    run_id: str
    intent: str
    arguments: dict[str, Any]
    approved: bool
    result: dict[str, Any]
    summary: str
    status: str


def checkpoint_path(engine):
    configured = os.environ.get("CAREERPILOT_WORKFLOW_DB")
    if configured:
        return Path(configured)
    if engine.url.drivername.startswith("sqlite") and engine.url.database not in {None, ":memory:"}:
        return Path(engine.url.database).resolve().with_name("workflows.sqlite")
    raise RuntimeError("A persistent CAREERPILOT_WORKFLOW_DB is required")


def export_checkpoints(engine, run_ids):
    """Return only owned graph state; never expose an arbitrary thread selector."""
    if not checkpoint_path(engine).is_file():
        return []
    result = []
    with saver(engine) as store:
        for run_id in run_ids:
            config = {"configurable": {"thread_id": run_id}}
            for item in store.list(config):
                result.append(
                    {"run_id": run_id, "checkpoint": item.checkpoint, "metadata": item.metadata}
                )
    return result


def purge_checkpoints(engine, run_ids):
    if not checkpoint_path(engine).is_file():
        return
    with saver(engine) as store:
        for run_id in run_ids:
            store.delete_thread(run_id)


@contextmanager
def saver(engine):
    path = checkpoint_path(engine)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False, timeout=10)
    try:
        conn.execute("PRAGMA busy_timeout=10000")
        conn.execute("PRAGMA journal_mode=WAL")
        revision = conn.execute("PRAGMA user_version").fetchone()[0]
        if revision not in {0, 1}:
            raise RuntimeError("Workflow storage schema is incompatible")
        store = SqliteSaver(conn)
        store.setup()
        conn.execute("PRAGMA user_version=1")
        yield store
    finally:
        conn.close()


def invalidate_checkpoints(db, principal, trace):
    """Revoke resumability immediately; the single Worker purges after execution."""
    growth.lock_owner(db, principal)
    run_ids = list(
        db.scalars(
            growth.owner_query(principal, "agent_run").with_only_columns(growth.GrowthRecord.id)
        )
    )
    if not run_ids:
        return
    db.execute(
        update(BackgroundTask)
        .where(
            BackgroundTask.user_id == principal.user_id,
            BackgroundTask.tenant_id == principal.tenant_id,
            BackgroundTask.kind == "growth.assistant",
            BackgroundTask.status.in_({"queued", "running", "awaiting_approval"}),
        )
        .values(
            status="cancelled", lease_token="", lease_until=0, version=BackgroundTask.version + 1
        )
    )
    growth.save(db, principal, "workflow_cleanup", {"run_ids": run_ids}, trace)


def process_cleanup(engine):
    from sqlalchemy import delete

    from careerpilot.growth_models import GrowthRevision

    with Session(engine) as db, db.begin():
        rows = list(
            db.scalars(
                select(growth.GrowthRecord).where(growth.GrowthRecord.kind == "workflow_cleanup")
            )
        )
        for row in rows:
            purge_checkpoints(engine, json.loads(row.payload_json)["run_ids"])
            db.execute(delete(GrowthRevision).where(GrowthRevision.record_id == row.id))
            db.delete(row)

    from careerpilot.conversation_deletion import process_pending

    process_pending(engine)


def owner(task):
    return Principal(task.user_id, task.tenant_id)


def append_event(db, principal, run_id, event_key, event_type, payload):
    growth.lock_owner(db, principal)
    previous = db.scalar(
        select(WorkflowEvent).where(
            WorkflowEvent.run_id == run_id, WorkflowEvent.event_key == event_key
        )
    )
    if previous:
        return previous.sequence
    sequence = (
        db.scalar(select(func.max(WorkflowEvent.sequence)).where(WorkflowEvent.run_id == run_id))
        or 0
    ) + 1
    db.add(
        WorkflowEvent(
            run_id=run_id,
            sequence=sequence,
            user_id=principal.user_id,
            tenant_id=principal.tenant_id,
            event_key=event_key,
            event_type=event_type,
            payload_json=growth.dump(payload),
        )
    )
    db.flush()
    return sequence


def snapshot(db, principal, run_id, locale="zh-CN"):
    run = growth.data(growth.owned(db, principal, run_id, "agent_run"))
    task = db.get(BackgroundTask, run_id)
    status = run["status"]
    if task and task.status in {"failed", "cancelled"}:
        status = task.status
    elif status == "success":
        status = "completed"
    elif task and task.status == "running":
        status = "running"
    sequence = (
        db.scalar(select(func.max(WorkflowEvent.sequence)).where(WorkflowEvent.run_id == run_id))
        or 0
    )
    result = {
        **run,
        "run_id": run_id,
        "status": status,
        "sequence": sequence,
        "task_version": task.version if task else 0,
        "graph_version": run.get("graph_version", "legacy"),
    }

    def current_plan(value):
        if not isinstance(value, dict) or not value.get("id") or "tasks" not in value:
            return value
        row = db.scalar(
            growth.owner_query(principal, "plan").where(growth.GrowthRecord.id == value["id"])
        )
        return {**value, "current_status": growth.data(row)["status"] if row else "unavailable"}

    output = run.get("result") or {}
    if output.get("kind") == "assistant_chat":
        result["result"] = {
            **output,
            "tools": [
                {**tool, "result": current_plan(tool["result"])} for tool in output.get("tools", [])
            ],
        }
    elif output:
        result["result"] = current_plan(output)
    if not result.get("confirmation_response") and run.get("resume_approved") is True:
        from careerpilot.conversations import WRITES

        saved = next(
            (
                tool
                for tool in reversed((run.get("result") or {}).get("tools", []))
                if tool["status"] == "success" and tool["name"] in WRITES
            ),
            None,
        )
        if saved:
            result["confirmation_response"] = {
                "status": "saved",
                "action": saved["name"],
                "result_id": saved["result"].get("id"),
            }
    if locale == "en-US":
        from careerpilot.i18n import assistant_result

        localized = assistant_result(
            {**result, "status": "success" if status == "completed" else status}, locale
        )
        result = {**localized, "status": status}
        if status == "waiting_confirmation":
            result["summary"] = "Review and confirm before this operation continues."
            confirmation = result.get("confirmation", {})
            labels = {
                "plan": "Save this study plan as a draft. Publishing requires confirmation.",
                "revise_plan": "Save these changes as a draft or a rescheduling preview.",
                "replan": "Save this preview. Accepting it will update the active plan.",
                "publish_plan": "Publish this plan, replacing the current plan only where allowed.",
                "decide_preview": "Confirm this decision about the rescheduling preview.",
                "update_profile": (
                    "Save these profile changes, retaining other fields and history. "
                    "Review the existing plan separately."
                ),
                "save_profile_draft": (
                    "Save the reviewed fields as a private draft; "
                    "the official profile remains unchanged."
                ),
                "add_profile_note": (
                    "Add this self reported information to My information. "
                    "It is not independently verified."
                ),
                "add_experience": (
                    "Add this experience to My information as a confirmed self report, "
                    "not verified skill evidence."
                ),
                "remember": "Save this preference for future conversations.",
                "forget_preference": "Forget this saved preference. Original chat history remains.",
                "import_job": (
                    "Save this job description. "
                    "An existing record with the same source may be updated."
                ),
                "record_interview": (
                    "Review the exact date and application before "
                    "saving the local interview appointment."
                ),
                "save_interview_reflection": (
                    "Save this self-reported interview reflection, not an assessment."
                ),
                "plan_interview_review": (
                    "Create a plan preview from the confirmed review; accepting it is separate."
                ),
                "create_resume_fact": (
                    "Add this statement to your career facts as a confirmed self report. "
                    "It is not independently verified."
                ),
                "create_resume": "Create a resume from these confirmed facts.",
                "revise_resume": "Save a new resume version, retaining the previous versions.",
                "tailor_resume": "Reorder these facts for this job and save a new resume version.",
                "draft_material": "Save this application material as a draft. It will not be sent.",
                "create_application": "Create a local application draft. It is not submitted.",
            }
            if action := confirmation.get("action"):
                result["confirmation"] = {
                    **confirmation,
                    "summary": labels.get(action, "Review this change."),
                }
                if action == "decide_preview":
                    result["confirmation"]["summary"] = (
                        "Accept this preview and update the study plan."
                        if confirmation.get("accept")
                        else "Reject this rescheduling preview."
                    )
    return result


def build_graph(engine, task_id, token, checkpointer):
    from careerpilot.assistant import ROLES, SCHEMAS, intent_from

    @contextmanager
    def transaction():
        with Session(engine) as db, db.begin():
            task = db.get(BackgroundTask, task_id)
            if task is None or task.status != "running" or task.lease_token != token:
                raise DomainError("TASK_CANCELLED", "任务已取消", 409)
            principal = owner(task)
            growth.lock_owner(db, principal)
            # Cancellation/reset and effects serialize on the owner row.
            db.refresh(task)
            if task.status != "running" or task.lease_token != token:
                raise DomainError("TASK_CANCELLED", "任务已取消", 409)
            from careerpilot.v1_models import DeletionRequest

            if db.scalar(
                select(DeletionRequest.id).where(
                    DeletionRequest.user_id == principal.user_id,
                    DeletionRequest.tenant_id == principal.tenant_id,
                    DeletionRequest.status == "queued",
                )
            ):
                raise DomainError("TASK_CANCELLED", "工作区正在清空", 409)
            grant = db.get(RoleGrant, (task.user_id, task.tenant_id))
            if grant:
                principal = Principal(task.user_id, task.tenant_id, grant.role)
            yield db, task, principal

    with Session(engine) as db:
        task = db.get(BackgroundTask, task_id)
        body = growth.data(growth.owned(db, owner(task), task_id, "agent_run"))["input"]
    if body.get("conversation_id") and body["intent"] == "auto":
        from careerpilot.conversations import build_graph as chat_graph

        return chat_graph(engine, task_id, checkpointer, transaction)

    def prepare(state):
        with transaction() as (db, task, principal):
            run = growth.data(growth.owned(db, principal, task_id, "agent_run"))
            body = run["input"]
            intent = intent_from(body["message"]) if body["intent"] == "auto" else body["intent"]
            if intent in {"diagnose", "plan"}:
                args = {"job_ids": body["job_ids"]}
                if intent == "plan":
                    profile = learning.profile_context(db, principal)
                    args["start_date"] = (
                        datetime.now(ZoneInfo(profile["timezone"])).date().isoformat()
                    )
            elif intent == "replan":
                plan = learning.active_plan(db, principal) if not body["plan_id"] else None
                args = {"plan_id": body["plan_id"] or (plan.id if plan else "")}
            elif intent == "resources":
                query = " ".join(
                    skill["id"]
                    for skill in catalogue.entries(db, "skill")
                    if any(a.casefold() in body["message"].casefold() for a in skill["aliases"])
                )
                args = {"query": query or body["message"][:200]}
            else:
                args = {}
            authorize_tool(
                db,
                principal,
                ToolSpec(
                    "growth." + intent, int(intent in {"plan", "replan"}), SCHEMAS[intent], ROLES
                ),
                args,
            )
            append_event(
                db, principal, task_id, "prepared", "node", {"node": "prepare", "intent": intent}
            )
            return {"run_id": task_id, "intent": intent, "arguments": args}

    def confirmation(state):
        with transaction() as (db, task, principal):
            run = growth.data(growth.owned(db, principal, task_id, "agent_run"))
            needed = run["input"].get("require_confirmation", False)
        if needed and state["intent"] in {"plan", "replan"}:
            approved = interrupt({"intent": state["intent"], "arguments": state["arguments"]})
            if approved is not True:
                return {"approved": False, "status": "cancelled", "summary": "已拒绝此次操作。"}
        return {"approved": True}

    def execute(state):
        with transaction() as (db, task, principal):
            receipt_id = str(uuid5(NAMESPACE_URL, f"careerpilot:{task_id}:domain.v1"))
            receipt = db.get(growth.GrowthRecord, receipt_id)
            if receipt:
                return json.loads(receipt.payload_json)
            intent, args = state["intent"], state["arguments"]
            authorize_tool(
                db,
                principal,
                ToolSpec(
                    "growth." + intent, int(intent in {"plan", "replan"}), SCHEMAS[intent], ROLES
                ),
                args,
            )
            if intent == "help":
                from careerpilot.assistant import help_reply

                result = help_reply(
                    growth.data(growth.owned(db, principal, task_id, "agent_run"))["input"][
                        "message"
                    ]
                )
                summary = result["summary"]
            elif intent == "diagnose":
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
                plan = learning.active_plan(db, principal)
                result = {
                    "skills": learning.snapshots(db, principal),
                    "active_plan": growth.data(plan) if plan else None,
                }
                summary = "已读取当前技能证据和学习计划。"
            output = {"result": result, "summary": summary, "status": "completed"}
            growth.save(
                db, principal, "workflow_receipt", output, task.trace_id, record_id=receipt_id
            )
            append_event(
                db, principal, task_id, "domain", "node", {"node": "execute", "intent": intent}
            )
            return output

    def finish(state):
        return state

    graph = StateGraph(State)
    graph.add_node("prepare", prepare)
    graph.add_node("confirmation", confirmation)
    graph.add_node("execute", execute)
    graph.add_node("summarize", finish)
    graph.add_edge(START, "prepare")
    graph.add_edge("prepare", "confirmation")
    graph.add_conditional_edges(
        "confirmation", lambda s: "execute" if s["approved"] else "summarize"
    )
    graph.add_edge("execute", "summarize")
    graph.add_edge("summarize", END)
    return graph.compile(checkpointer=checkpointer)


@contextmanager
def heartbeat(engine, task_id, token):
    stopped = threading.Event()

    def renew():
        while not stopped.wait(5):
            with Session(engine) as db, db.begin():
                db.execute(
                    update(BackgroundTask)
                    .where(
                        BackgroundTask.id == task_id,
                        BackgroundTask.lease_token == token,
                        BackgroundTask.status == "running",
                    )
                    .values(lease_until=int(time.time()) + 30)
                )
            from careerpilot.worker import HEARTBEAT

            HEARTBEAT.touch()

    thread = threading.Thread(target=renew, daemon=True)
    thread.start()
    try:
        yield
    finally:
        stopped.set()
        thread.join(timeout=6)


def handler(engine, task_id, token, now):
    output: dict[str, Any]
    with Session(engine) as db:
        task = db.get(BackgroundTask, task_id)
        if task is None:
            return
        row = growth.owned(db, owner(task), task_id, "agent_run")
        run = growth.data(row)
        if run["status"] in {"success", "completed", "failed", "cancelled"}:
            return
    # Unmappable pre-cutover runs never invent checkpoints or replay old writes.
    if run.get("graph_version") != GRAPH_VERSION:
        output = {
            "status": "failed",
            "summary": "旧运行已停止，请重新发起。",
            "result": {"code": "GRAPH_VERSION_MISMATCH"},
        }
    else:
        try:
            with heartbeat(engine, task_id, token), saver(engine) as store:
                graph = build_graph(engine, task_id, token, store)
                config = {"configurable": {"thread_id": task_id}, "callbacks": []}
                saved = graph.get_state(config)
                command: Any = None if saved.values else {"run_id": task_id}
                if run.get("resume_requested"):
                    command = Command(resume=run["resume_approved"])
                result = graph.invoke(command, {**config, "recursion_limit": GRAPH_RECURSION_LIMIT})
                if result.get("__interrupt__"):
                    output = {
                        "status": "waiting_confirmation",
                        "confirmation": result["__interrupt__"][0].value,
                    }
                else:
                    output = {k: result[k] for k in ("status", "summary", "result") if k in result}
        except DomainError as exc:
            output = {
                "status": "failed",
                "summary": exc.message,
                "result": {"code": exc.code, "message": exc.message},
            }
            if run["input"].get("conversation_id"):
                with Session(engine) as db:
                    tools = []
                    for round_number in range(MAX_TOOL_CALLS):
                        key = str(
                            uuid5(NAMESPACE_URL, f"careerpilot:{task_id}:chat.tool:{round_number}")
                        )
                        receipt = db.get(growth.GrowthRecord, key)
                        if receipt:
                            tools.append(json.loads(receipt.payload_json))
                    output["result"].update(kind="assistant_chat", tools=tools, citations=[])
    with Session(engine) as db, db.begin():
        task = db.get(BackgroundTask, task_id)
        if task is None or task.status != "running" or task.lease_token != token:
            return
        principal = owner(task)
        growth.lock_owner(db, principal)
        row = growth.owned(db, principal, task_id, "agent_run")
        run = json.loads(row.payload_json)
        run.update(output, resume_requested=False)
        # Compatibility projection only; the graph remains the only executor.
        if run["status"] == "completed":
            run["status"] = "success"
            run["steps"] = [
                {
                    "tool": "growth." + result["intent"],
                    "arguments": result["arguments"],
                    "status": "success",
                }
            ]
            if run["result"].get("kind") == "assistant_chat":
                run["steps"] = [
                    {
                        "tool": "growth." + item["name"],
                        "arguments": item["arguments"],
                        "status": item["status"],
                    }
                    for item in run["result"]["tools"]
                ]
        growth.save(db, principal, "agent_run", run, task.trace_id, row, row.version)
        append_event(
            db,
            principal,
            task_id,
            f"state:{row.version}",
            "state",
            {"status": output["status"], "summary": output.get("summary", "")},
        )
        if output["status"] == "waiting_confirmation":
            task.status, task.lease_token, task.lease_until = "awaiting_approval", "", 0
            task.version += 1
