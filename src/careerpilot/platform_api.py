"""Platform HTTP adapters: object policies remain independent of the model."""

import time
from typing import Annotated, Literal

from fastapi import Depends, Header, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select, update

from careerpilot.application import audit
from careerpilot.domain import DomainError
from careerpilot.gateway import reserve
from careerpilot.persistence import utc_now
from careerpilot.platform_models import (
    BackgroundTask,
    Consent,
    ModelUsage,
    RequestMetric,
    SourceRecord,
    StateChange,
    StateRecord,
)
from careerpilot.runtime import cancel, enqueue, owned_task, retry, task_data
from careerpilot.safety import require_source, scan_upload, source_scopes
from careerpilot.states import TRANSITIONS, authorize, validate_transition


class ConsentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    granted: bool


class TaskInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["baseline.validate"] = "baseline.validate"


class StateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["skill", "learning_task", "application", "goal"]


class TransitionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target: str = Field(min_length=1, max_length=40)
    reason: str = Field(min_length=1, max_length=200)
    # These references are validated by domain services when V1 evidence exists.
    # This V0 endpoint cannot fabricate assessment or application proof.


def register_platform(app, engine, session_dependency, identity):
    Db = Depends(session_dependency)
    Identity = Depends(identity)
    Version = Annotated[str, Header(pattern=r'^"[1-9][0-9]*"$')]

    @app.get("/api/v1/consents")
    def consents(db=Db, principal=Identity):
        rows = db.scalars(
            select(Consent).where(
                Consent.user_id == principal.user_id, Consent.tenant_id == principal.tenant_id
            )
        )
        return [
            {
                "purpose": c.purpose,
                "granted": bool(c.granted),
                "version": c.version,
                "updated_at": c.updated_at,
            }
            for c in rows
        ]

    @app.put("/api/v1/consents/{purpose}")
    def set_consent(
        purpose: Literal["external_model", "public_web", "product_analytics"],
        body: ConsentInput,
        request: Request,
        db=Db,
        principal=Identity,
    ):
        from careerpilot import growth

        growth.lock_owner(db, principal)
        row = db.get(Consent, (principal.user_id, principal.tenant_id, purpose))
        if row is None:
            row = Consent(
                user_id=principal.user_id, tenant_id=principal.tenant_id, purpose=purpose, version=0
            )
            db.add(row)
        row.granted, row.version, row.updated_at = int(body.granted), row.version + 1, utc_now()
        if purpose == "product_analytics" and not body.granted:
            from careerpilot.analytics import purge

            purge(db, principal)
        audit(
            db,
            principal,
            principal.user_id,
            "consent." + ("granted" if body.granted else "revoked"),
            row.version,
            request.state.trace_id,
        )
        db.commit()
        return {"purpose": purpose, "granted": body.granted, "version": row.version}

    @app.post("/api/v1/uploads/scan")
    async def scan(request: Request, filename: str, db=Db, principal=Identity):
        source = db.get(SourceRecord, "user_import")
        now = int(time.time())
        require_source(source, "import", now)
        reserve(db, principal, f"source:user_import:{now // 60}", 1, source.rate_per_minute)
        db.commit()
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > 1024 * 1024:
                raise DomainError("UPLOAD_REJECTED", "文件超过 1MiB", 422)
        text = scan_upload(bytes(data), filename)
        return {
            "status": "scanned",
            "bytes": len(data),
            "trust": "untrusted_data",
            "characters": len(text),
            "persisted": False,
        }

    @app.post("/api/v1/agent-tasks", status_code=202)
    def create_task(
        body: TaskInput,
        request: Request,
        idempotency_key: Annotated[str, Header(min_length=1, max_length=128)],
        db=Db,
        principal=Identity,
    ):
        result = enqueue(db, principal, body.kind, idempotency_key, request.state.trace_id)
        db.commit()
        return result

    @app.get("/api/v1/agent-tasks")
    def tasks(
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
        cursor: str | None = None,
        db=Db,
        principal=Identity,
    ):
        query = select(BackgroundTask).where(
            BackgroundTask.user_id == principal.user_id,
            BackgroundTask.tenant_id == principal.tenant_id,
        )
        if cursor:
            query = query.where(BackgroundTask.id > cursor)
        return [task_data(t) for t in db.scalars(query.order_by(BackgroundTask.id).limit(limit))]

    @app.get("/api/v1/agent-tasks/{task_id}")
    def task(task_id: str, db=Db, principal=Identity):
        return task_data(owned_task(db, principal, task_id))

    @app.post("/api/v1/agent-tasks/{task_id}/cancel")
    def cancel_task(task_id: str, if_match: Version, db=Db, principal=Identity):
        result = cancel(db, principal, task_id, int(if_match.strip('"')))
        db.commit()
        return result

    @app.post("/api/v1/agent-tasks/{task_id}/retry")
    def retry_task(task_id: str, if_match: Version, db=Db, principal=Identity):
        result = retry(db, principal, task_id, int(if_match.strip('"')))
        db.commit()
        return result

    @app.post("/api/v1/state-records", status_code=201)
    def create_state(body: StateInput, db=Db, principal=Identity):
        initial = {
            "skill": "unassessed",
            "learning_task": "pending",
            "application": "draft",
            "goal": "draft",
        }[body.kind]
        row = StateRecord(
            user_id=principal.user_id, tenant_id=principal.tenant_id, kind=body.kind, status=initial
        )
        db.add(row)
        db.commit()
        return {"id": row.id, "kind": row.kind, "status": row.status, "version": row.version}

    @app.post("/api/v1/state-records/{record_id}/transition")
    def transition(
        record_id: str,
        body: TransitionInput,
        if_match: Version,
        request: Request,
        db=Db,
        principal=Identity,
    ):
        row = db.get(StateRecord, record_id)
        authorize(principal, "private.write", (row.tenant_id, row.user_id) if row else None)
        validate_transition(row.kind, row.status, body.target)
        if row.kind == "goal" and body.target == "active":
            # Goal activation requires CP-122 impact/confirmation; not a backdoor.
            raise DomainError("PREVIEW_REQUIRED", "请通过职业目标预览流程激活", 409)
        old, version = row.status, int(if_match.strip('"'))
        result = db.execute(
            update(StateRecord)
            .where(
                StateRecord.id == row.id,
                StateRecord.version == version,
                StateRecord.tenant_id == principal.tenant_id,
                StateRecord.user_id == principal.user_id,
            )
            .values(status=body.target, version=version + 1)
        )
        if result.rowcount != 1:
            raise DomainError("VERSION_CONFLICT", "状态版本已更新", 412)
        db.add(
            StateChange(
                user_id=principal.user_id,
                tenant_id=principal.tenant_id,
                aggregate_id=row.id,
                actor=principal.user_id,
                previous=old,
                current=body.target,
                reason=body.reason,
                revision=version + 1,
                trace_id=request.state.trace_id,
            )
        )
        db.commit()
        return {"id": row.id, "status": body.target, "version": version + 1}

    @app.get("/api/v1/state-policies")
    def policies(principal=Identity):
        return {
            kind: {state: sorted(targets) for state, targets in states.items()}
            for kind, states in TRANSITIONS.items()
        }

    @app.get("/api/v1/admin/source-health")
    def source_health(db=Db, principal=Identity):
        authorize(principal, "operations.read")
        return [
            {
                "id": s.id,
                "enabled": bool(s.enabled),
                "status": s.status,
                "last_error_class": s.last_error_class,
                "retries": s.retries,
                "responsibility": s.responsibility,
                "last_checked_at": s.last_checked_at,
                "access_basis": s.access_basis,
                "reviewed_until": s.reviewed_until,
                "scopes": source_scopes(s),
                "rate_per_minute": s.rate_per_minute,
                "retention_days": s.retention_days,
            }
            for s in db.scalars(select(SourceRecord).order_by(SourceRecord.id))
        ]

    @app.post("/api/v1/admin/sources/{source_id}/enable")
    def enable_source(source_id: str, db=Db, principal=Identity):
        authorize(principal, "operations.read")
        source = db.get(SourceRecord, source_id)
        if source is not None:
            source.enabled = 1
        require_source(
            source,
            "import"
            if source_id == "user_import"
            else "metadata"
            if source_id == "github_metadata"
            else "search",
            int(time.time()),
        )
        source.status = "ready"
        db.commit()
        return {"id": source.id, "enabled": True}

    @app.get("/api/v1/admin/operations")
    def operations(db=Db, principal=Identity):
        authorize(principal, "operations.read")
        # Aggregate counts only; operators cannot inspect private task/usage text.
        task_counts = db.execute(
            select(BackgroundTask.status, func.count())
            .where(BackgroundTask.tenant_id == principal.tenant_id)
            .group_by(BackgroundTask.status)
        ).all()
        errors = db.execute(
            select(BackgroundTask.last_error_class, func.count())
            .where(
                BackgroundTask.tenant_id == principal.tenant_id,
                BackgroundTask.last_error_class != "",
            )
            .group_by(BackgroundTask.last_error_class)
        ).all()
        usage = db.execute(
            select(
                func.coalesce(func.sum(ModelUsage.input_tokens), 0),
                func.coalesce(func.sum(ModelUsage.output_tokens), 0),
                func.coalesce(func.sum(ModelUsage.estimated_cost_micros), 0),
            ).where(ModelUsage.tenant_id == principal.tenant_id)
        ).one()
        return {
            "tasks": dict(task_counts),
            "errors": dict(errors),
            "model": {
                "input_tokens": usage[0],
                "output_tokens": usage[1],
                "estimated_cost_micros": usage[2],
            },
            "private_content_visible": False,
            "audit_event_count": db.scalar(
                select(func.count())
                .select_from(StateChange)
                .where(StateChange.tenant_id == principal.tenant_id)
            ),
            "retry_attempts": db.scalar(
                select(func.coalesce(func.sum(BackgroundTask.attempts), 0)).where(
                    BackgroundTask.tenant_id == principal.tenant_id
                )
            ),
        }

    @app.get("/api/v1/admin/metrics")
    def metrics(db=Db, principal=Identity):
        authorize(principal, "operations.read")
        rows = db.scalars(
            select(RequestMetric)
            .where(or_empty_or_tenant(principal))
            .order_by(RequestMetric.created_at.desc())
            .limit(1000)
        ).all()
        elapsed = sorted(r.elapsed_ms for r in rows)
        return {
            "requests": len(rows),
            "internal_failures": sum(r.status >= 500 for r in rows),
            "p95_ms": elapsed[max(0, (len(elapsed) * 95 + 99) // 100 - 1)] if elapsed else None,
            "external_source_failures": db.scalar(
                select(func.count())
                .select_from(SourceRecord)
                .where(SourceRecord.last_error_class != "")
            ),
            "window": "latest_1000_requests",
            "contains_private_content": False,
        }


def or_empty_or_tenant(principal):
    return RequestMetric.tenant_id.in_(["", principal.tenant_id])
