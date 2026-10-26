"""Owned, paginated history; source facts never imply verified ability."""

import json

from sqlalchemy import String, literal, select, union_all

from careerpilot.growth_models import GrowthRecord, GrowthRevision
from careerpilot.persistence import AuditEvent
from careerpilot.v1_models import EvidenceRevocation, SkillEvidence


def events(db, principal, before=None, limit=50):
    def owned(model):
        return (model.user_id == principal.user_id, model.tenant_id == principal.tenant_id)

    revisions = (
        select(
            (
                literal("revision:")
                + GrowthRevision.record_id
                + literal(":")
                + GrowthRevision.version.cast(String)
            ).label("key"),
            GrowthRevision.created_at.label("at"),
            GrowthRecord.kind.label("kind"),
            GrowthRevision.record_id.label("source_id"),
            GrowthRevision.version.label("version"),
        )
        .join(GrowthRecord, GrowthRecord.id == GrowthRevision.record_id)
        .where(
            *owned(GrowthRevision),
            *owned(GrowthRecord),
            GrowthRecord.kind.in_(("profile", "plan", "goal_history")),
        )
    )
    parts = [revisions]
    for model, kind, id_column, version in (
        (SkillEvidence, "evidence", SkillEvidence.id, SkillEvidence.source_version),
        (EvidenceRevocation, "revocation", EvidenceRevocation.evidence_id, literal(0)),
        (AuditEvent, "goal_event", AuditEvent.id, AuditEvent.revision),
    ):
        query = select(
            (literal(kind + ":") + id_column).label("key"),
            model.created_at.label("at"),
            literal(kind).label("kind"),
            id_column.label("source_id"),
            version.label("version"),
        ).where(*owned(model))
        if model is AuditEvent:
            # New goal snapshots already represent the operation. Keep raw audits
            # only where older data has no corresponding content snapshot.
            snapshot = (
                select(GrowthRevision.record_id)
                .join(
                    GrowthRecord,
                    GrowthRecord.id == GrowthRevision.record_id,
                )
                .where(
                    *owned(GrowthRevision),
                    *owned(GrowthRecord),
                    GrowthRecord.kind == "goal_history",
                    GrowthRevision.record_id == AuditEvent.aggregate_id,
                    GrowthRevision.version == AuditEvent.revision,
                )
                .exists()
            )
            query = query.where(AuditEvent.action.like("goal.%"), ~snapshot)
        parts.append(query)
    stream = union_all(*parts).subquery()
    query = select(stream)
    if before:
        at, key = before
        query = query.where((stream.c.at < at) | ((stream.c.at == at) & (stream.c.key < key)))
    rows = (
        db.execute(query.order_by(stream.c.at.desc(), stream.c.key.desc()).limit(limit + 1))
        .mappings()
        .all()
    )
    result = []
    for row in rows[:limit]:
        item = dict(row)
        kind = item["kind"]
        if kind in {"profile", "plan", "goal_history"}:
            revision = db.get(GrowthRevision, (item["source_id"], item["version"]))
            previous = db.get(GrowthRevision, (item["source_id"], item["version"] - 1))
            payload = json.loads(revision.payload_json)
            old = json.loads(previous.payload_json) if previous else {}
            item["changes"] = {
                key: {"before": old.get(key), "after": payload.get(key)}
                for key in sorted(old.keys() | payload.keys())
                if old.get(key) != payload.get(key)
            }
            item["reason"] = (
                payload.get("reason")
                or {
                    "profile": "本人保存档案；早期版本如无细分审计则不推测触发操作",
                    "plan": "计划版本变更，历史内容保留",
                    "goal_history": "职业目标变更快照",
                }[kind]
            )
            audits = db.scalars(
                select(AuditEvent).where(
                    *owned(AuditEvent),
                    AuditEvent.aggregate_id == item["source_id"],
                    AuditEvent.revision == item["version"],
                )
            ).all()
            restored = next((a for a in audits if ".restored_from_" in a.action), None)
            if restored:
                item["reason"] = "本人恢复历史版本 v" + restored.action.split(".restored_from_")[1]
            elif kind == "plan":
                if not previous:
                    item["reason"] = "根据岗位差距生成计划草案"
                elif old.get("status") != payload.get("status"):
                    item["reason"] = "本人确认计划状态变更"
                for audit in audits:
                    preview_revision = db.scalar(
                        select(GrowthRevision)
                        .join(
                            AuditEvent,
                            (AuditEvent.aggregate_id == GrowthRevision.record_id)
                            & (AuditEvent.revision == GrowthRevision.version),
                        )
                        .where(
                            *owned(GrowthRevision),
                            *owned(AuditEvent),
                            AuditEvent.trace_id == audit.trace_id,
                            AuditEvent.action == "plan_preview.saved",
                        )
                        .limit(1)
                    )
                    if preview_revision:
                        preview = json.loads(preview_revision.payload_json)
                        if (
                            preview.get("status") == "accepted"
                            and preview.get("plan_id") == item["source_id"]
                        ):
                            item["reason"] = "本人接受计划预览：" + str(
                                preview.get("reason", "计划调整")
                            )
                            item["preview_id"] = preview_revision.record_id
        elif kind == "evidence":
            evidence = db.get(SkillEvidence, item["source_id"])
            item["details"] = {
                key: getattr(evidence, key)
                for key in (
                    "skill_id",
                    "kind",
                    "scope",
                    "conclusion",
                    "source_id",
                    "source_version",
                    "expires_at",
                )
            }
            item["revoked"] = db.get(EvidenceRevocation, evidence.id) is not None
            item["reason"] = "能力来源记录；自述、学习完成和测验结果分别保留"
        elif kind == "revocation":
            item["reason"] = db.get(EvidenceRevocation, item["source_id"]).reason
        else:
            audit = db.get(AuditEvent, item["source_id"])
            item["reason"] = audit.action
            item["goal_id"] = audit.aggregate_id
        result.append(item)
    return {
        "events": result,
        "next": [rows[limit - 1]["at"], rows[limit - 1]["key"]] if len(rows) > limit else None,
    }
