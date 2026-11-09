"""Structured experiences extend confirmed memory, not a second fact store."""

from datetime import date
from typing import Annotated, Literal
from uuid import NAMESPACE_URL, uuid5

from fastapi import Depends, Header, Request
from pydantic import Field, model_validator

from careerpilot import growth, resumes
from careerpilot.domain import DomainError
from careerpilot.growth_api import ConfirmInput, Input
from careerpilot.safety import scan_upload


class PartialDate(Input):
    value: str | None = Field(default=None, pattern=r"^\d{4}(-\d{2})?(-\d{2})?$")
    precision: Literal["unknown", "year", "month", "day"] = "unknown"

    @model_validator(mode="after")
    def valid_date(self):
        lengths = {"year": 4, "month": 7, "day": 10}
        if self.precision == "unknown":
            if self.value is not None:
                raise ValueError("Unknown dates must be empty")
        elif self.value is None or len(self.value) != lengths[self.precision]:
            raise ValueError("Date precision must match the value")
        else:
            date.fromisoformat(
                self.value + {"year": "-01-01", "month": "-01", "day": ""}[self.precision]
            )
        return self


class ExperienceInput(ConfirmInput):
    kind: Literal["education", "internship", "work", "project"]
    title: str = Field(min_length=1, max_length=200)
    organization: str = Field(default="", max_length=200)
    role: str = Field(default="", max_length=200)
    start_date: PartialDate = Field(default_factory=PartialDate)
    end_date: PartialDate = Field(default_factory=PartialDate)
    situation: str = Field(default="", max_length=2000)
    task: str = Field(default="", max_length=2000)
    action: str = Field(min_length=1, max_length=2000)
    result: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def chronology(self):
        if self.start_date.value and self.end_date.value:
            width = min(len(self.start_date.value), len(self.end_date.value))
            if self.start_date.value[:width] > self.end_date.value[:width]:
                raise ValueError("Experience ends before it starts")
        return self


def save_experience(db, principal, body, trace, row=None, version=0, record_id=None, origin=None):
    """Use the same confirmed self-report record for forms and chat additions."""
    payload = body.model_dump(mode="json", exclude={"confirmed"})
    scan_upload(growth.dump(payload).encode(), "experience.txt")
    text = "\n".join(
        v
        for v in (
            body.title,
            body.organization,
            body.role,
            body.start_date.value,
            body.end_date.value,
            body.situation,
            body.task,
            body.action,
            body.result,
        )
        if v
    )
    content = {
        "text": text,
        "category": "self_report",
        "trust": "user_confirmed",
        "skill_status": "unverified",
        "deleted": False,
        "structured_experience": True,
        "experience": payload,
        "verification": "user_confirmed_not_independently_verified",
    }
    if origin is None and row is not None:
        origin = growth.data(row).get("origin")
    if origin:
        content["origin"] = origin
    return growth.save(db, principal, "memory", content, trace, row, version, record_id=record_id)


def register_profile_editor(app, session_dependency, identity):
    Db, Identity = Depends(session_dependency), Depends(identity)
    Version = Annotated[str, Header(pattern=r'^"[0-9]+"$')]
    Key = Annotated[str, Header(min_length=1, max_length=128)]

    @app.get("/api/v1/experiences")
    def experiences(db=Db, principal=Identity):
        return [
            growth.data(row)
            for row in db.scalars(growth.owner_query(principal, "memory"))
            if growth.data(row).get("structured_experience") and not growth.data(row).get("deleted")
        ]

    @app.put("/api/v1/experiences/{record_id}")
    def save(
        record_id: str,
        body: ExperienceInput,
        if_match: Version,
        idempotency_key: Key,
        request: Request,
        db=Db,
        principal=Identity,
    ):
        growth.lock_owner(db, principal)
        payload = body.model_dump(mode="json", exclude={"confirmed"})
        scan_upload(growth.dump(payload).encode(), "experience.txt")
        key = (
            str(uuid5(NAMESPACE_URL, f"experience:{principal.user_id}:{idempotency_key}"))
            if record_id == "new"
            else record_id
        )
        row = (
            db.get(growth.GrowthRecord, key)
            if record_id == "new"
            else growth.owned(db, principal, key, "memory")
        )
        if row and record_id == "new":
            if growth.data(row).get("experience") != payload:
                raise DomainError("IDEMPOTENCY_CONFLICT", "请求内容已经改变", 409)
            return growth.data(row)
        output = save_experience(
            db,
            principal,
            body,
            request.state.trace_id,
            row,
            int(if_match.strip('"')),
            record_id=key,
        )
        db.commit()
        return output

    @app.post("/api/v1/experiences/{record_id}/fact", status_code=201)
    def use_fact(
        record_id: str,
        body: ConfirmInput,
        if_match: Version,
        idempotency_key: Key,
        request: Request,
        db=Db,
        principal=Identity,
    ):
        growth.lock_owner(db, principal)
        row = growth.owned(db, principal, record_id, "memory")
        current = growth.data(row)
        if not current.get("structured_experience"):
            raise DomainError("VALIDATION_ERROR", "请选择经历记录", 422)
        if row.version != int(if_match.strip('"')):
            raise DomainError("VERSION_CONFLICT", "经历已更新", 412)
        import json

        from careerpilot.persistence import IdempotencyRecord
        from careerpilot.safety import payload_hash

        digest = payload_hash(
            {"action": "experience.fact", "id": record_id, "version": row.version}
        )
        previous = db.get(
            IdempotencyRecord, (principal.user_id, principal.tenant_id, idempotency_key)
        )
        if previous:
            if previous.request_hash != digest:
                raise DomainError("IDEMPOTENCY_CONFLICT", "请求内容已经改变", 409)
            return json.loads(previous.response_json)
        kind = current["experience"]["kind"]
        output = resumes.create_fact(
            db,
            principal,
            {
                "kind": kind if kind in {"education", "project"} else "experience",
                "source_type": "memory",
                "source_id": record_id,
                "source_version": row.version,
            },
            request.state.trace_id,
        )
        db.add(
            IdempotencyRecord(
                user_id=principal.user_id,
                tenant_id=principal.tenant_id,
                key=idempotency_key,
                request_hash=digest,
                response_json=growth.dump(output),
            )
        )
        db.commit()
        return output
