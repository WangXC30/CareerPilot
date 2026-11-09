"""Workspace-scoped workflow commands, resumable SSE and private draft editing."""

import json
import time
from typing import Annotated, Literal
from uuid import NAMESPACE_URL, uuid5

from fastapi import Depends, Header, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import Field
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from careerpilot import (
    assistant,
    conversation_deletion,
    conversations,
    growth,
    i18n,
    runtime,
    workflows,
)
from careerpilot import guidance as guidance_rules
from careerpilot.domain import DomainError
from careerpilot.growth_api import ConfirmInput, Input
from careerpilot.platform_models import BackgroundTask
from careerpilot.safety import scan_upload
from careerpilot.v1_api import AssistantInput
from careerpilot.workflow_models import WorkflowEvent


class RunInput(AssistantInput):
    require_confirmation: bool = True


class ResumeInput(Input):
    approved: bool


class ConversationInput(Input):
    title: str = Field(default="", max_length=80)


class DraftInput(Input):
    values: dict = Field(default_factory=dict)
    base_record_version: int = Field(default=0, ge=0)


class GuidanceInput(Input):
    action: Literal["explore", "learn", "career", "defer", "resume", "answered", "reset"]
    run_id: str | None = Field(default=None, max_length=36)


def register_workflows(app, engine, session_dependency, identity):
    Db, Identity = Depends(session_dependency), Depends(identity)
    Key = Annotated[str, Header(min_length=1, max_length=128)]
    Version = Annotated[str, Header(pattern=r'^"[0-9]+"$')]

    @app.get("/api/v1/workflows/preferences")
    def preferences(db=Db, principal=Identity):
        return [
            value
            for row in db.scalars(growth.owner_query(principal, "chat_preference"))
            if not (value := growth.data(row)).get("deleted")
        ]

    @app.post("/api/v1/workflows/preferences/{preference_id}/forget")
    def forget_preference(
        preference_id: str,
        body: ConfirmInput,
        request: Request,
        if_match: Version,
        db=Db,
        principal=Identity,
    ):
        growth.lock_owner(db, principal)
        value = conversations.forget(
            db, principal, preference_id, int(if_match.strip('"')), request.state.trace_id
        )
        db.commit()
        return value

    @app.get("/api/v1/workflows/conversations")
    def list_conversations(db=Db, principal=Identity):
        return [
            conversations.public(growth.data(row))
            for row in db.scalars(
                growth.owner_query(principal, "conversation").order_by(
                    growth.GrowthRecord.created_at.desc(), growth.GrowthRecord.id.desc()
                )
            )
        ]

    @app.post("/api/v1/workflows/conversations", status_code=201)
    def create_conversation(
        body: ConversationInput, request: Request, idempotency_key: Key, db=Db, principal=Identity
    ):
        growth.lock_owner(db, principal)
        scan_upload(body.title.encode(), "title.txt")
        key = str(
            uuid5(
                NAMESPACE_URL, f"chat:{principal.tenant_id}:{principal.user_id}:{idempotency_key}"
            )
        )
        row = db.get(growth.GrowthRecord, key)
        if db.get(growth.GrowthRecord, conversation_deletion.cleanup_id(key)):
            raise DomainError("CHAT_DELETING", "该对话正在清理，请新建对话。", 409)
        if row:
            value = growth.data(growth.owned(db, principal, key, "conversation"))
            if value.get("initial_title", "") != body.title:
                raise DomainError("IDEMPOTENCY_CONFLICT", "幂等键已用于不同请求。", 409)
        else:
            value = conversations.create(db, principal, body.title, request.state.trace_id, key)
        db.commit()
        return conversations.public(value)

    @app.delete("/api/v1/workflows/conversations/{conversation_id}", status_code=204)
    def delete_conversation(
        conversation_id: str,
        body: ConfirmInput,
        request: Request,
        if_match: Version,
        db=Db,
        principal=Identity,
    ):
        conversation_deletion.request_delete(
            db, principal, conversation_id, int(if_match.strip('"')), request.state.trace_id
        )
        db.commit()

    @app.get("/api/v1/workflows/conversations/{conversation_id}/runs")
    def conversation_runs(
        conversation_id: str,
        request: Request,
        db=Db,
        principal=Identity,
        limit: int = Query(default=30, ge=1, le=100),
        before: int | None = Query(default=None, ge=0),
    ):
        value = growth.data(growth.owned(db, principal, conversation_id, "conversation"))
        ids = value["run_ids"]
        end = min(before, len(ids)) if before is not None else len(ids)
        start = max(0, end - limit)
        return {
            "items": [
                workflows.snapshot(
                    db, principal, key, i18n.negotiate(request.headers.get("accept-language"))
                )
                for key in reversed(ids[start:end])
            ],
            "before": start,
            "has_more": start > 0,
        }

    @app.post("/api/v1/workflows/runs", status_code=202)
    def create(body: RunInput, request: Request, idempotency_key: Key, db=Db, principal=Identity):
        scan_upload(body.message.encode(), "message.txt")
        growth.lock_owner(db, principal)
        run = assistant.enqueue(
            db, principal, body.model_dump(), idempotency_key, request.state.trace_id
        )
        workflows.append_event(db, principal, run["id"], "queued", "state", {"status": "queued"})
        result = workflows.snapshot(
            db, principal, run["id"], i18n.negotiate(request.headers.get("accept-language"))
        )
        db.commit()
        return result

    @app.get("/api/v1/workflows/runs")
    def runs(
        request: Request, db=Db, principal=Identity, limit: int = Query(default=30, ge=1, le=100)
    ):
        rows = db.scalars(
            growth.owner_query(principal, "agent_run")
            .order_by(growth.GrowthRecord.created_at.desc())
            .limit(limit)
        )
        return [
            workflows.snapshot(
                db, principal, r.id, i18n.negotiate(request.headers.get("accept-language"))
            )
            for r in rows
        ]

    @app.get("/api/v1/workflows/runs/{run_id}")
    def get_run(run_id: str, request: Request, db=Db, principal=Identity):
        return workflows.snapshot(
            db, principal, run_id, i18n.negotiate(request.headers.get("accept-language"))
        )

    @app.post("/api/v1/workflows/runs/{run_id}/cancel")
    def cancel(run_id: str, if_match: Version, db=Db, principal=Identity):
        growth.lock_owner(db, principal)
        current = workflows.snapshot(db, principal, run_id)
        if current["status"] in workflows.TERMINAL:
            raise DomainError("INVALID_TRANSITION", "运行已结束", 409)
        runtime.cancel(db, principal, run_id, int(if_match.strip('"')))
        workflows.append_event(db, principal, run_id, "cancelled", "state", {"status": "cancelled"})
        result = workflows.snapshot(db, principal, run_id)
        db.commit()
        return result

    @app.post("/api/v1/workflows/runs/{run_id}/resume", status_code=202)
    def resume(run_id: str, body: ResumeInput, if_match: Version, db=Db, principal=Identity):
        growth.lock_owner(db, principal)
        run = workflows.snapshot(db, principal, run_id)
        if (
            run["graph_version"] != workflows.GRAPH_VERSION
            or run["status"] != "waiting_confirmation"
        ):
            raise DomainError("INVALID_TRANSITION", "运行不在等待确认状态", 409)
        version = int(if_match.strip('"'))
        task = runtime.owned_task(db, principal, run_id)
        if task.version != version:
            raise DomainError("VERSION_CONFLICT", "运行已更新", 412)
        db.execute(
            update(BackgroundTask)
            .where(
                BackgroundTask.id == run_id,
                BackgroundTask.version == version,
            )
            .values(status="queued", version=version + 1, available_at=0, attempts=0)
        )
        row = growth.owned(db, principal, run_id, "agent_run")
        payload = json.loads(row.payload_json)
        payload.update(status="queued", resume_requested=True, resume_approved=body.approved)
        payload["confirmation_response"] = {
            "action": (payload.get("confirmation") or {}).get("action"),
            "status": "accepted" if body.approved else "declined",
        }
        payload.pop("confirmation", None)
        growth.save(db, principal, "agent_run", payload, task.trace_id, row, row.version)
        workflows.append_event(
            db, principal, run_id, f"resume:{version}", "state", {"status": "queued"}
        )
        runtime.emit(db, task, "AgentTaskQueued.v1")
        db.commit()
        return workflows.snapshot(db, principal, run_id)

    @app.get("/api/v1/workflows/runs/{run_id}/events")
    def events(
        run_id: str,
        request: Request,
        db=Db,
        principal=Identity,
        after: int = Query(default=0, ge=0),
        follow: bool = True,
    ):
        workflows.snapshot(db, principal, run_id)
        cursor = request.headers.get("last-event-id", str(after))
        if not cursor.isdecimal():
            raise DomainError("VALIDATION_ERROR", "事件游标无效", 422)
        start = int(cursor)

        def stream():
            sequence, deadline = start, time.monotonic() + 20
            while True:
                with Session(engine) as session:
                    # A reset must terminate old subscriptions before disclosing
                    # new workspace state. Never resolve a different owner here.
                    try:
                        from careerpilot.v1_models import DeletionRequest

                        if session.scalar(
                            select(DeletionRequest.id).where(
                                DeletionRequest.user_id == principal.user_id,
                                DeletionRequest.tenant_id == principal.tenant_id,
                                DeletionRequest.status == "queued",
                            )
                        ):
                            raise DomainError("LOCAL_WORKSPACE_RESETTING", "工作区正在清空", 409)
                        current = workflows.snapshot(
                            session,
                            principal,
                            run_id,
                            i18n.negotiate(request.headers.get("accept-language")),
                        )
                    except DomainError:
                        yield "event: workspace_changed\ndata: {}\n\n"
                        return
                    rows = list(
                        session.scalars(
                            select(WorkflowEvent)
                            .where(
                                WorkflowEvent.run_id == run_id,
                                WorkflowEvent.user_id == principal.user_id,
                                WorkflowEvent.tenant_id == principal.tenant_id,
                                WorkflowEvent.sequence > sequence,
                            )
                            .order_by(WorkflowEvent.sequence)
                        )
                    )
                    for row in rows:
                        payload = {
                            "run_id": run_id,
                            "sequence": row.sequence,
                            "type": row.event_type,
                            "payload": json.loads(row.payload_json),
                        }
                        yield f"id: {row.sequence}\nevent: update\ndata: {growth.dump(payload)}\n\n"
                        sequence = row.sequence
                    # Snapshots repair missing/expired/future cursors; stream end
                    # never implies completion. The stored status is authoritative.
                    yield f"event: snapshot\ndata: {growth.dump(current)}\n\n"
                if (
                    not follow
                    or current["status"] in workflows.TERMINAL
                    or time.monotonic() >= deadline
                ):
                    return
                time.sleep(0.5)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-store",
                "X-Accel-Buffering": "no",
            },
        )

    @app.post("/api/v1/workflows/runs/{run_id}/retry", status_code=202)
    def retry_run(run_id: str, if_match: Version, db=Db, principal=Identity):
        growth.lock_owner(db, principal)
        run = workflows.snapshot(db, principal, run_id)
        if run["graph_version"] != workflows.GRAPH_VERSION or run["status"] != "failed":
            raise DomainError("INVALID_TRANSITION", "只能重试当前版本的失败运行", 409)
        if conversation_id := run["input"].get("conversation_id"):
            conversation = growth.data(growth.owned(db, principal, conversation_id, "conversation"))
            if conversation["run_ids"][-1] != run_id:
                raise DomainError("CHAT_RETRY_STALE", "已有后续消息，请重新发送请求。", 409)
        task = runtime.owned_task(db, principal, run_id)
        version = int(if_match.strip('"'))
        if task.version != version:
            raise DomainError("VERSION_CONFLICT", "运行已更新", 412)
        row = growth.owned(db, principal, run_id, "agent_run")
        payload = json.loads(row.payload_json)
        payload.update(status="queued", summary="", result=None, resume_requested=False)
        growth.save(db, principal, "agent_run", payload, task.trace_id, row, row.version)
        db.execute(
            update(BackgroundTask)
            .where(BackgroundTask.id == run_id, BackgroundTask.version == version)
            .values(
                status="queued",
                attempts=0,
                version=version + 1,
                available_at=0,
                lease_token="",
                lease_until=0,
            )
        )
        workflows.append_event(
            db, principal, run_id, f"retry:{version}", "state", {"status": "queued"}
        )
        runtime.emit(db, task, "AgentTaskQueued.v1")
        db.commit()
        return workflows.snapshot(db, principal, run_id)

    @app.get("/api/v1/drafts/{kind}/{record_key}")
    def get_draft(kind: str, record_key: str, db=Db, principal=Identity):
        row = db.get(growth.GrowthRecord, draft_id(principal, kind, record_key))
        return growth.data(row) if row else {"version": 0, "values": {}, "base_record_version": 0}

    @app.get("/api/v1/workflows/guidance")
    def guidance(db=Db, principal=Identity):
        return guidance_state(db, principal)

    @app.get("/api/v1/workflows/requirements")
    def action_requirements(
        action: Literal["explore", "plan", "match", "resume", "interview", "adjust_plan"],
        job_id: Annotated[str | None, Query(max_length=36)] = None,
        db=Db,
        principal=Identity,
    ):
        return guidance_rules.requirements(db, principal, action, job_id)

    @app.put("/api/v1/workflows/guidance")
    def update_guidance(
        body: GuidanceInput, if_match: Version, request: Request, db=Db, principal=Identity
    ):
        growth.lock_owner(db, principal)
        key = guidance_id(principal)
        row = db.get(growth.GrowthRecord, key)
        version = int(if_match.strip('"'))
        previous = growth.data(row).get("values", {}) if row else {}
        if body.action == "answered" and previous.get("response_received"):
            return guidance_state(db, principal)
        if (row.version if row else 0) != version:
            raise DomainError("VERSION_CONFLICT", "引导已在另一页面更新，请重新读取。", 412)
        if body.action == "answered":
            if (
                not body.run_id
                or not previous.get("purpose")
                or previous.get("status") != "in_progress"
            ):
                raise DomainError("VALIDATION_ERROR", "当前没有待回答的引导问题。", 422)
            run = growth.data(growth.owned(db, principal, body.run_id, "agent_run"))
            if not run.get("input", {}).get("message"):
                raise DomainError("VALIDATION_ERROR", "对话回答为空。", 422)
        values = {
            **previous,
            "status": "deferred"
            if body.action == "defer"
            else (
                "not_started"
                if body.action == "reset"
                or (body.action == "resume" and not previous.get("purpose"))
                else "in_progress"
            ),
            "purpose": previous.get("purpose")
            if body.action in {"defer", "resume", "answered"}
            else None
            if body.action == "reset"
            else body.action,
            "asked": (
                ["purpose", body.action]
                if body.action in {"explore", "learn", "career"}
                else ["purpose"]
                if body.action == "reset"
                else previous.get("asked", ["purpose"])
            ),
            "response_received": True
            if body.action == "answered"
            else (
                False
                if body.action in {"explore", "learn", "career", "reset"}
                else previous.get("response_received", False)
            ),
        }
        growth.save(
            db,
            principal,
            "private_draft",
            {
                "record_kind": "guidance",
                "record_key": "workspace",
                "values": values,
                "base_record_version": 0,
            },
            request.state.trace_id,
            row,
            version,
            record_id=key,
        )
        db.commit()
        return guidance_state(db, principal)

    @app.put("/api/v1/drafts/{kind}/{record_key}")
    def save_draft(
        kind: str,
        record_key: str,
        body: DraftInput,
        if_match: Version,
        request: Request,
        db=Db,
        principal=Identity,
    ):
        key = draft_id(principal, kind, record_key)
        raw = growth.dump(body.values).encode()
        if len(raw) > 100000:
            raise DomainError("VALIDATION_ERROR", "草稿过大", 422)
        scan_upload(raw, "draft.json")
        growth.lock_owner(db, principal)
        row = db.get(growth.GrowthRecord, key)
        version = int(if_match.strip('"'))
        if row and row.version != version:
            raise DomainError("VERSION_CONFLICT", "草稿已在另一页面修改；请比较后保存。", 412)
        result = growth.save(
            db,
            principal,
            "private_draft",
            {
                **body.model_dump(),
                "record_kind": kind,
                "record_key": record_key,
            },
            request.state.trace_id,
            row,
            version,
            record_id=key,
        )
        db.commit()
        return result

    @app.delete("/api/v1/drafts/{kind}/{record_key}", status_code=204)
    def delete_draft(kind: str, record_key: str, if_match: Version, db=Db, principal=Identity):
        growth.lock_owner(db, principal)
        row = db.get(growth.GrowthRecord, draft_id(principal, kind, record_key))
        if row:
            if row.version != int(if_match.strip('"')):
                raise DomainError("VERSION_CONFLICT", "草稿已更新", 412)
            from sqlalchemy import delete

            from careerpilot.growth_models import GrowthRevision

            db.execute(delete(GrowthRevision).where(GrowthRevision.record_id == row.id))
            db.delete(row)
        db.commit()


def draft_id(principal, kind, record_key):
    if (
        kind not in {"profile", "experience", "project", "material", "fact", "interview"}
        or len(record_key) > 100
    ):
        raise DomainError("VALIDATION_ERROR", "草稿类型或记录无效", 422)
    return str(
        uuid5(NAMESPACE_URL, f"draft:{principal.tenant_id}:{principal.user_id}:{kind}:{record_key}")
    )


def guidance_id(principal):
    return str(uuid5(NAMESPACE_URL, f"guidance:{principal.tenant_id}:{principal.user_id}"))


def guidance_state(db, principal):
    row = db.get(growth.GrowthRecord, guidance_id(principal))
    values = growth.data(row).get("values", {}) if row else {}
    profile = growth.profile(db, principal)
    draft = db.get(growth.GrowthRecord, draft_id(principal, "profile", "personal"))
    has_draft = bool(draft and growth.data(draft).get("values"))
    has_context = any(
        goal.get("status") != "archived" for goal in growth.goal_snapshot(db, principal)
    )
    if not has_context:
        has_context = any(
            item.get("trust") == "user_confirmed"
            and bool(item.get("text"))
            and not item.get("deleted")
            and not item.get("revoked")
            and item.get("status") not in {"deleted", "revoked", "user_archived"}
            for item in (
                growth.data(r) for r in db.scalars(growth.owner_query(principal, "memory"))
            )
        )
    if has_draft:
        status = (
            "deferred"
            if values.get("status") == "deferred"
            else "in_progress"
            if values.get("purpose")
            else "draft_pending"
        )
    elif profile or has_context:
        status = "satisfied"
    else:
        status = values.get("status", "not_started")
    purpose_action = {"explore": "explore", "learn": "plan", "career": "match"}
    action = purpose_action.get(values.get("purpose") or "")
    missing = (
        [
            item["field"]
            for item in guidance_rules.requirements(db, principal, action)["items"]
            if item["state"] == "unknown"
        ]
        if action
        else ([] if status == "satisfied" else ["purpose"])
    )
    return {
        "version": row.version if row else 0,
        "status": status,
        "purpose": values.get("purpose"),
        "asked": values.get("asked", []),
        "answered": (
            ["purpose", values["purpose"]]
            if values.get("response_received")
            else ["purpose"]
            if values.get("purpose")
            else []
        ),
        "response_received": values.get("response_received", False),
        "profile_version": profile.version if profile else 0,
        "profile_draft": has_draft,
        "missing": missing,
    }
