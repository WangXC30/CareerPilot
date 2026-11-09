"""Erase conversation content now; reclaim its execution storage after the Worker stops."""

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from careerpilot import growth, workflows
from careerpilot.chat_limits import MAX_TOOL_CALLS
from careerpilot.domain import DomainError, Principal
from careerpilot.growth_models import GrowthRecord, GrowthRevision
from careerpilot.persistence import AuditEvent, IdempotencyRecord
from careerpilot.platform_models import (
    BackgroundTask,
    InboxReceipt,
    OutboxEvent,
    QuotaBucket,
    StateChange,
)
from careerpilot.workflow_models import WorkflowEvent

CONTENT_KINDS = {
    "conversation",
    "agent_run",
    "workflow_receipt",
    "chat_model_receipt",
    "chat_tool_receipt",
}


def batches(values):
    for offset in range(0, len(values), 100):
        yield values[offset : offset + 100]


def cleanup_id(conversation_id):
    return str(uuid5(NAMESPACE_URL, f"careerpilot:delete-conversation:{conversation_id}"))


def receipt_ids(run_id):
    return [
        str(uuid5(NAMESPACE_URL, f"careerpilot:{run_id}:domain.v1")),
        *[
            str(uuid5(NAMESPACE_URL, f"careerpilot:{run_id}:chat.{kind}:{index}"))
            for kind in ("model", "tool")
            for index in range(MAX_TOOL_CALLS + 1)
        ],
    ]


def owned(model, principal):
    return (model.user_id == principal.user_id, model.tenant_id == principal.tenant_id)


def erase_content(db, principal, record_ids, run_ids):
    for group in batches(run_ids):
        db.execute(
            delete(WorkflowEvent).where(
                *owned(WorkflowEvent, principal), WorkflowEvent.run_id.in_(group)
            )
        )
    for group in batches(record_ids):
        db.execute(
            delete(GrowthRevision).where(
                *owned(GrowthRevision, principal), GrowthRevision.record_id.in_(group)
            )
        )
        db.execute(
            delete(AuditEvent).where(
                *owned(AuditEvent, principal), AuditEvent.aggregate_id.in_(group)
            )
        )
        db.execute(
            delete(GrowthRecord).where(
                *owned(GrowthRecord, principal),
                GrowthRecord.id.in_(group),
                GrowthRecord.kind.in_(CONTENT_KINDS),
            )
        )


def request_delete(db, principal, conversation_id, version, trace):
    growth.lock_owner(db, principal)
    row = growth.owned(db, principal, conversation_id, "conversation")
    if row.version != version:
        raise DomainError("VERSION_CONFLICT", "对话已变化，请刷新后再删除。", 412)
    run_ids = list(dict.fromkeys(json.loads(row.payload_json)["run_ids"]))
    for group in batches(run_ids):
        found = set(
            db.scalars(
                growth.owner_query(principal, "agent_run")
                .with_only_columns(GrowthRecord.id)
                .where(GrowthRecord.id.in_(group))
            )
        )
        if found != set(group):
            raise DomainError("NOT_FOUND", "对话运行记录不存在。", 404)
        # Keep task rows until the running handler has acknowledged cancellation.
        db.execute(
            update(BackgroundTask)
            .where(*owned(BackgroundTask, principal), BackgroundTask.id.in_(group))
            .values(
                status="cancelled",
                lease_token="",
                lease_until=0,
                version=BackgroundTask.version + 1,
            )
        )
    candidates = [
        conversation_id,
        *run_ids,
        *[key for run_id in run_ids for key in receipt_ids(run_id)],
    ]
    record_ids = []
    for group in batches(candidates):
        record_ids.extend(
            db.scalars(
                select(GrowthRecord.id).where(
                    *owned(GrowthRecord, principal),
                    GrowthRecord.id.in_(group),
                    GrowthRecord.kind.in_(CONTENT_KINDS),
                )
            )
        )
    erase_content(db, principal, record_ids, run_ids)
    # IDs only: this durable queue never retains messages, summaries or tool payloads.
    growth.save(
        db,
        principal,
        "conversation_cleanup",
        {"run_ids": run_ids, "record_ids": record_ids},
        trace,
        record_id=cleanup_id(conversation_id),
    )


def erase_execution(db, principal, run_ids, record_ids):
    erase_content(db, principal, record_ids, run_ids)
    for group in batches(run_ids):
        event_ids = list(
            db.scalars(
                select(OutboxEvent.id).where(
                    *owned(OutboxEvent, principal), OutboxEvent.aggregate_id.in_(group)
                )
            )
        )
        for events in batches(event_ids):
            db.execute(delete(InboxReceipt).where(InboxReceipt.event_id.in_(events)))
        for model in (OutboxEvent, StateChange, AuditEvent):
            db.execute(delete(model).where(*owned(model, principal), model.aggregate_id.in_(group)))
        db.execute(
            delete(BackgroundTask).where(
                *owned(BackgroundTask, principal), BackgroundTask.id.in_(group)
            )
        )
        db.execute(
            delete(QuotaBucket).where(
                *owned(QuotaBucket, principal),
                QuotaBucket.scope.in_(["task:" + key for key in group]),
            )
        )
    run_set = set(run_ids)
    for row in list(
        db.scalars(select(IdempotencyRecord).where(*owned(IdempotencyRecord, principal)))
    ):
        value = json.loads(row.response_json)
        if (
            isinstance(value, dict)
            and value.get("kind") == "growth.assistant"
            and value.get("id") in run_set
        ):
            db.delete(row)
    # Content-free model usage and aggregate budgets remain; deleting chat is not a refund.


def compact_storage(engine):
    paths = []
    if engine.dialect.name == "sqlite" and engine.url.database not in {None, ":memory:"}:
        paths.append(Path(engine.url.database))
    try:
        paths.append(workflows.checkpoint_path(engine))
    except RuntimeError:
        pass
    for path in dict.fromkeys(paths):
        if not path.is_file():
            continue
        try:
            with closing(sqlite3.connect(path, isolation_level=None, timeout=0.5)) as connection:
                if connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0]:
                    return False
                connection.execute("VACUUM")
                if connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0]:
                    return False
        except sqlite3.OperationalError:
            # Readers, disk pressure or another short write can delay compaction.
            # Keep the queue and retry next cycle; deleted content is already inaccessible.
            return False
    return True


def process_pending(engine):
    try:
        _process_pending(engine)
    except sqlite3.OperationalError:
        # A busy checkpoint database must not terminate the Worker or lose the queue.
        return


def _process_pending(engine):
    """Only the single Worker invokes this between handlers, fencing late checkpoints."""
    with Session(engine) as db, db.begin():
        jobs = list(
            db.scalars(select(GrowthRecord).where(GrowthRecord.kind == "conversation_cleanup"))
        )
        job_ids = [row.id for row in jobs]
        for row in jobs:
            principal = Principal(row.user_id, row.tenant_id)
            growth.lock_owner(db, principal)
            value = json.loads(row.payload_json)
            workflows.purge_checkpoints(engine, value["run_ids"])
            erase_execution(db, principal, value["run_ids"], value["record_ids"])
            # Once erasure has committed, only the compaction retry is outstanding.
            # Do not keep a large ID manifest (or its revisions) in the vacuumed file.
            db.execute(
                delete(GrowthRevision).where(
                    *owned(GrowthRevision, principal),
                    GrowthRevision.record_id == row.id,
                )
            )
            row.payload_json = growth.dump({"run_ids": [], "record_ids": []})
            row.version += 1
    if not job_ids or not compact_storage(engine):
        return
    with Session(engine) as db, db.begin():
        for key in job_ids:
            cleanup_row = db.get(GrowthRecord, key)
            if cleanup_row is None:
                continue
            principal = Principal(cleanup_row.user_id, cleanup_row.tenant_id)
            db.execute(
                delete(GrowthRevision).where(
                    *owned(GrowthRevision, principal), GrowthRevision.record_id == key
                )
            )
            db.execute(
                delete(AuditEvent).where(
                    *owned(AuditEvent, principal), AuditEvent.aggregate_id == key
                )
            )
            db.delete(cleanup_row)
