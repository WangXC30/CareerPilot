"""Typed maintenance of personal information; derived verdicts stay domain controlled."""

import hashlib
import json
import re
from typing import Annotated, Literal

from fastapi import Depends, Header, Query, Request
from pydantic import ConfigDict, Field
from sqlalchemy import delete, select

from careerpilot import application, catalogue, growth, reporting, resumes, source_library
from careerpilot.application import audit
from careerpilot.business_editor import BUSINESS_KINDS, public_record
from careerpilot.domain import DomainError
from careerpilot.growth_api import ConfirmInput, JobInput
from careerpilot.growth_models import GrowthRecord, GrowthRevision
from careerpilot.hiring_api import FactInput
from careerpilot.persistence import CareerGoal, IdempotencyRecord
from careerpilot.progress_api import LogInput
from careerpilot.safety import scan_upload
from careerpilot.source_api import DocumentInput


class MaterialEdit(ConfirmInput):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)
    filename: str = Field(min_length=1, max_length=180, pattern=r"^[^/\\\x00]+$")
    content: str = Field(min_length=1, max_length=1000000)


class ProjectDetails(ConfirmInput):
    title: str = Field(min_length=1, max_length=200)
    note: str = Field(default="", max_length=4000)


class DraftEdit(ConfirmInput):
    text: str = Field(min_length=1, max_length=20000)


class ReflectionEdit(ConfirmInput):
    reflection: str = Field(min_length=1, max_length=10000)


class ResumeDetails(ConfirmInput):
    title: str = Field(min_length=1, max_length=100)


class JobEdit(JobInput, ConfirmInput):
    pass


class NoteInput(ConfirmInput):
    text: str = Field(min_length=1, max_length=2000)
    category: Literal["self_report"] = "self_report"


# Deletion operates only on user information, never provider settings, spend,
# workflow receipts, catalogue entries, or evaluator verdicts in isolation.
KINDS = {
    "profile",
    "material",
    "memory",
    "resume_fact",
    "source_document",
    "practice_project",
    "resume",
    "job_application",
    "application_draft",
    "job",
    "study_log",
    "quarter_review",
    "plan",
}
CHILDREN = {
    "material": {"memory": "source_id"},
    "resume_fact": {"resume_fact_revocation": "fact_id"},
    "practice_project": {
        "project_asset": "project_id",
        "project_submission": "project_id",
        "project_assessment": "project_id",
        "project_narrative": "project_id",
        "project_review": "project_id",
        "project_plan_preview": "project_id",
        "plan_preview": "project_id",
    },
    "resume": {"resume_version": "resume_id"},
    "job_application": {
        "application_reminder": "application_id",
        "application_notice": "application_id",
    },
    "plan": {"plan_preview": "plan_id", "review_pipeline": "plan_id"},
}


def owner_rows(db, principal):
    return list(
        db.scalars(
            select(GrowthRecord).where(
                GrowthRecord.user_id == principal.user_id,
                GrowthRecord.tenant_id == principal.tenant_id,
            )
        )
    )


def references(value, ids):
    if isinstance(value, dict):
        # Only structural references count. A note that happens to contain an ID
        # is not a dependency; nested source.id and facts[].id still count.
        return any(
            (key == "id" or key.endswith("_id") or key.endswith("_ids"))
            and contains(item, ids)
            or isinstance(item, (dict, list))
            and references(item, ids)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(references(item, ids) for item in value)
    return False


def contains(value, ids):
    if isinstance(value, str):
        return value in ids
    if isinstance(value, list):
        return any(contains(item, ids) for item in value)
    return False


def describe(row):
    payload = json.loads(row.payload_json)
    title = (
        "Expired source"
        if row.kind == "source_document" and (payload["expires_at"] <= growth.utc_now())
        else next(
            (
                payload.get(key)
                for key in ("title", "filename", "text", "reflection")
                if payload.get(key)
            ),
            "",
        )
    )
    if not title:
        title = (payload.get("template") or {}).get("title") or (payload.get("job") or {}).get(
            "title"
        )
    return {
        "id": row.id,
        "kind": row.kind,
        "version": row.version,
        "title": str(title or row.created_at)[:160],
    }


def deletion_preview(db, principal, kind, record_id):
    if kind not in KINDS:
        raise DomainError("NOT_FOUND", "资料类别不存在", 404)
    root = growth.owned(db, principal, record_id, kind)
    rows = owner_rows(db, principal)
    selected = {root.id: root}
    for row in rows:
        field = CHILDREN.get(kind, {}).get(row.kind)
        if field and json.loads(row.payload_json).get(field) == root.id:
            selected[row.id] = row
    # A resume's private generated drafts/translations belong to its versions.
    if kind == "resume":
        for row in rows:
            if row.kind in {"application_draft", "resume_translation"} and (
                json.loads(row.payload_json).get("resume_version_id") in selected
            ):
                selected[row.id] = row
    ids = set(selected)
    # Sources and facts have explicitly invalidatable downstream snapshots.
    allow_missing = {"profile", "material", "memory", "resume_fact", "source_document"}
    blockers = []
    if kind not in allow_missing:
        for row in rows:
            if (
                row.id not in ids
                and row.kind in KINDS | {"resume_version"}
                and references(json.loads(row.payload_json), ids)
            ):
                if row.kind == "resume_version":
                    parent_id = json.loads(row.payload_json)["resume_id"]
                    row = growth.owned(db, principal, parent_id, "resume")
                blockers.append(describe(row))
    blockers = list({item["id"]: item for item in blockers}.values())
    related = sorted(
        [describe(row) for row in selected.values()],
        key=lambda item: item["id"],
    )
    blockers.sort(key=lambda item: item["id"])
    return (
        root,
        selected,
        {
            "records": related,
            "blockers": blockers,
            "fingerprint": growth.payload_hash({"records": related, "blockers": blockers}),
        },
    )


class DeleteInformation(ConfirmInput):
    fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")


def register_information(app, session_dependency, identity):
    Db, Identity = Depends(session_dependency), Depends(identity)
    Version = Annotated[str, Header(pattern=r'^"[0-9]+"$')]

    def changed(db, principal, trace):
        from careerpilot.workflows import invalidate_checkpoints

        invalidate_checkpoints(db, principal, trace)

    @app.get("/api/v1/information")
    def listing(
        kind: Annotated[str, Query(max_length=40)] = "",
        q: Annotated[str, Query(max_length=200)] = "",
        db=Db,
        principal=Identity,
    ):
        if kind and kind not in KINDS | BUSINESS_KINDS | {"career_goal"}:
            raise DomainError("NOT_FOUND", "资料类别不存在", 404)
        result = []
        for row in owner_rows(db, principal):
            if row.kind not in KINDS | BUSINESS_KINDS or kind and row.kind != kind:
                continue
            item = public_record(row)
            if row.kind == "source_document" and item["expires_at"] <= growth.utc_now():
                item = {
                    "id": row.id,
                    "version": row.version,
                    "created_at": row.created_at,
                    "title": "Expired source",
                    "expired": True,
                }
            if not q or q.casefold() in growth.dump(item).casefold():
                result.append({**item, "record_kind": row.kind})
        if not kind or kind == "career_goal":
            for row in db.scalars(
                select(CareerGoal).where(
                    CareerGoal.user_id == principal.user_id,
                    CareerGoal.tenant_id == principal.tenant_id,
                )
            ):
                item = application.goal_data(row)
                if not q or q.casefold() in growth.dump(item).casefold():
                    result.append({**item, "record_kind": "career_goal"})
        return result

    @app.get("/api/v1/information/{kind}/{record_id}/history")
    def history(kind: str, record_id: str, db=Db, principal=Identity):
        if kind not in KINDS | BUSINESS_KINDS:
            raise DomainError("NOT_FOUND", "资料类别不存在", 404)
        if kind == "source_document":
            source_library.owned(db, principal, record_id)
        if kind == "interview_session":
            from careerpilot.interviews import public

            return [
                {
                    **revision,
                    "payload": public(
                        {
                            **revision["payload"],
                            "id": record_id,
                            "version": revision["version"],
                            "created_at": revision["created_at"],
                        }
                    ),
                }
                for revision in growth.history(db, principal, record_id, kind)
            ]
        return growth.history(db, principal, record_id, kind)

    @app.get("/api/v1/information/{kind}/{record_id}/deletion-preview")
    def preview(kind: str, record_id: str, db=Db, principal=Identity):
        return deletion_preview(db, principal, kind, record_id)[2]

    @app.delete("/api/v1/information/{kind}/{record_id}", status_code=204)
    def remove(
        kind: str,
        record_id: str,
        body: DeleteInformation,
        if_match: Version,
        request: Request,
        db=Db,
        principal=Identity,
    ):
        growth.lock_owner(db, principal)
        root, selected, preview = deletion_preview(db, principal, kind, record_id)
        if root.version != int(if_match.strip('"')) or body.fingerprint != preview["fingerprint"]:
            raise DomainError("VERSION_CONFLICT", "资料或关联已改变，请重新预览", 412)
        if preview["blockers"]:
            raise DomainError("INFORMATION_REFERENCED", "请先处理删除预览中的关联资料", 409)
        ids = set(selected)
        # Revoke objective evidence before removing a project and its receipts.
        from careerpilot.v1_models import EvidenceRevocation, SkillEvidence

        for evidence in db.scalars(
            select(SkillEvidence).where(
                SkillEvidence.user_id == principal.user_id,
                SkillEvidence.tenant_id == principal.tenant_id,
                SkillEvidence.source_id.in_(ids),
            )
        ):
            if db.get(EvidenceRevocation, evidence.id) is None:
                db.add(
                    EvidenceRevocation(
                        evidence_id=evidence.id,
                        user_id=principal.user_id,
                        tenant_id=principal.tenant_id,
                        reason="Source information deleted",
                    )
                )
        # Stored HTTP responses must not replay removed content after deletion.
        for receipt in db.scalars(
            select(IdempotencyRecord).where(
                IdempotencyRecord.user_id == principal.user_id,
                IdempotencyRecord.tenant_id == principal.tenant_id,
            )
        ):
            if any(key in receipt.response_json for key in ids):
                db.delete(receipt)
        changed(db, principal, request.state.trace_id)
        for row in selected.values():
            db.execute(delete(GrowthRevision).where(GrowthRevision.record_id == row.id))
            db.delete(row)
        audit(db, principal, record_id, "information.deleted", root.version, request.state.trace_id)
        db.commit()

    @app.post("/api/v1/memories", status_code=201)
    def add_note(body: NoteInput, request: Request, db=Db, principal=Identity):
        scan_upload(body.text.encode(), "note.txt")
        result = growth.save(
            db,
            principal,
            "memory",
            {
                "text": body.text,
                "category": "self_report",
                "trust": "user_confirmed",
                "skill_status": "unverified",
                "deleted": False,
                "verification": "user_confirmed_not_independently_verified",
            },
            request.state.trace_id,
        )
        db.commit()
        return result

    @app.put("/api/v1/materials/{record_id}")
    def edit_material(
        record_id: str,
        body: MaterialEdit,
        if_match: Version,
        request: Request,
        db=Db,
        principal=Identity,
    ):
        growth.authorize_import(db, principal)
        row = growth.owned(db, principal, record_id, "material")
        content = scan_upload(body.content.encode(), body.filename)
        if not content.strip() or len(content.encode()) > 1024 * 1024:
            raise DomainError("UPLOAD_REJECTED", "材料需非空且不超过 1MiB", 422)
        result = growth.save(
            db,
            principal,
            "material",
            {
                "filename": body.filename,
                "content": content,
                "sha256": hashlib.sha256(content.encode()).hexdigest(),
                "parser_version": "lines-v1",
                "status": "pending_confirmation",
                "candidates": [
                    {"text": m.group().strip(), "start": m.start(), "end": m.end()}
                    for m in re.finditer(r"[^\r\n]+", content)
                    if m.group().strip()
                ][:100],
            },
            request.state.trace_id,
            row,
            int(if_match.strip('"')),
        )
        for memory in db.scalars(growth.owner_query(principal, "memory")):
            payload = json.loads(memory.payload_json)
            if payload.get("source_id") == record_id:
                growth.save(
                    db,
                    principal,
                    "memory",
                    {**payload, "superseded_by": record_id},
                    request.state.trace_id,
                    memory,
                    memory.version,
                )
        changed(db, principal, request.state.trace_id)
        db.commit()
        return result

    @app.put("/api/v1/resume-facts/{record_id}")
    def edit_fact(
        record_id: str,
        body: FactInput,
        if_match: Version,
        request: Request,
        db=Db,
        principal=Identity,
    ):
        growth.lock_owner(db, principal)
        row = growth.owned(db, principal, record_id, "resume_fact")
        if db.get(GrowthRecord, resumes.revoke_id(principal, record_id)):
            raise DomainError("FACT_REVOKED", "此事实已撤回，请新增确认后的事实", 409)
        payload = resumes.fact_candidate(db, principal, body.model_dump(exclude={"confirmed"}))
        result = growth.save(
            db,
            principal,
            "resume_fact",
            {
                **payload,
                "confirmed_at": growth.utc_now(),
            },
            request.state.trace_id,
            row,
            int(if_match.strip('"')),
        )
        changed(db, principal, request.state.trace_id)
        db.commit()
        return result

    @app.put("/api/v1/source-documents/{record_id}")
    def edit_source(
        record_id: str,
        body: DocumentInput,
        if_match: Version,
        request: Request,
        db=Db,
        principal=Identity,
    ):
        growth.authorize_import(db, principal)
        row = source_library.owned(db, principal, record_id)
        args = body.model_dump(mode="json", exclude={"confirmed", "rights_confirmed"})
        for skill in args["skill_ids"]:
            catalogue.require_skill(db, skill)
        payload = source_library.document_payload(args)
        fingerprint = growth.payload_hash(
            {k: v for k, v in payload.items() if k not in {"imported_at", "retrieved_at"}}
        )
        previous = json.loads(row.payload_json)
        payload.update(
            trust="untrusted_reference",
            capability_evidence=False,
            expires_at=previous["expires_at"],
        )
        payload["content_hash"] = fingerprint
        result = growth.save(
            db,
            principal,
            "source_document",
            payload,
            request.state.trace_id,
            row,
            int(if_match.strip('"')),
        )
        changed(db, principal, request.state.trace_id)
        db.commit()
        return result

    @app.put("/api/v1/projects/{record_id}/details")
    def edit_project(
        record_id: str,
        body: ProjectDetails,
        if_match: Version,
        request: Request,
        db=Db,
        principal=Identity,
    ):
        return edit_content(
            db,
            principal,
            "practice_project",
            record_id,
            body.model_dump(exclude={"confirmed"}),
            if_match,
            request,
        )

    @app.put("/api/v1/jobs/{record_id}")
    def edit_job(
        record_id: str,
        body: JobEdit,
        if_match: Version,
        request: Request,
        db=Db,
        principal=Identity,
    ):
        growth.authorize_import(db, principal)
        row = growth.owned(db, principal, record_id, "job")
        payload = growth.job_payload(body.content, body.source_url)
        payload["sources"] = json.loads(row.payload_json)["sources"] + payload["sources"]
        result = growth.save(
            db, principal, "job", payload, request.state.trace_id, row, int(if_match.strip('"'))
        )
        changed(db, principal, request.state.trace_id)
        db.commit()
        return result

    @app.put("/api/v1/study-logs/{record_id}")
    def edit_log(
        record_id: str,
        body: LogInput,
        if_match: Version,
        request: Request,
        db=Db,
        principal=Identity,
    ):
        growth.lock_owner(db, principal)
        row = growth.owned(db, principal, record_id, "study_log")
        args = body.model_dump(mode="json", exclude={"confirmed"})
        reporting.validate_log(db, principal, args, excluding=record_id)
        previous = json.loads(row.payload_json)
        result = growth.save(
            db,
            principal,
            "study_log",
            {**previous, **args},
            request.state.trace_id,
            row,
            int(if_match.strip('"')),
        )
        db.commit()
        return result

    @app.put("/api/v1/application-drafts/{record_id}")
    def edit_draft(
        record_id: str,
        body: DraftEdit,
        if_match: Version,
        request: Request,
        db=Db,
        principal=Identity,
    ):
        growth.lock_owner(db, principal)
        row = growth.owned(db, principal, record_id, "application_draft")
        version = resumes.owned_version(db, principal, growth.data(row)["resume_version_id"])
        if not resumes.version_status(db, principal, version)["valid"]:
            raise DomainError(
                "FACT_CONFIRMATION_REQUIRED", "来源已改变，请先确认事实和简历版本", 409
            )
        return edit_content(
            db, principal, "application_draft", record_id, {"text": body.text}, if_match, request
        )

    @app.put("/api/v1/resumes/{record_id}/details")
    def edit_resume(
        record_id: str,
        body: ResumeDetails,
        if_match: Version,
        request: Request,
        db=Db,
        principal=Identity,
    ):
        return edit_content(
            db, principal, "resume", record_id, {"title": body.title}, if_match, request
        )

    @app.put("/api/v1/quarter-reviews/{record_id}")
    def edit_reflection(
        record_id: str,
        body: ReflectionEdit,
        if_match: Version,
        request: Request,
        db=Db,
        principal=Identity,
    ):
        return edit_content(
            db,
            principal,
            "quarter_review",
            record_id,
            {"reflection": body.reflection},
            if_match,
            request,
        )

    def edit_content(db, principal, kind, record_id, fields, if_match, request):
        growth.lock_owner(db, principal)
        row = growth.owned(db, principal, record_id, kind)
        scan_upload(growth.dump(fields).encode(), "information.txt")
        result = growth.save(
            db,
            principal,
            kind,
            {**json.loads(row.payload_json), **fields},
            request.state.trace_id,
            row,
            int(if_match.strip('"')),
        )
        changed(db, principal, request.state.trace_id)
        db.commit()
        return result
