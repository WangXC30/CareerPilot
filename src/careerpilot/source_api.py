"""Owned source references, explicit comparison and bounded public metadata reads."""

from datetime import date
from typing import Annotated, Literal

from fastapi import Depends, Header, Query, Request
from pydantic import Field, field_validator

from careerpilot import catalogue, github_sources, growth, source_library
from careerpilot.growth_api import ConfirmInput, Input

Kind = Literal[
    "project", "documentation", "community", "video", "article", "course", "exercise", "experience"
]


class ClaimInput(Input):
    topic: str = Field(min_length=1, max_length=80)
    value: str = Field(min_length=1, max_length=300)
    start: int = Field(ge=0)
    end: int = Field(ge=1)


class DocumentInput(ConfirmInput):
    rights_confirmed: Literal[True]
    title: str = Field(min_length=1, max_length=200)
    url: str = Field(default="", max_length=2000)
    text: str = Field(default="", max_length=4000)
    kind: Kind = "article"
    skill_ids: list[str] = Field(default_factory=list, max_length=10)
    declared_type: Literal["official", "personal", "advertising", "unknown"] = "unknown"
    author: str = Field(default="", max_length=100)
    published_on: date | None = None
    authorization_scope: Literal["reference_only", "local_excerpt"]
    claims: list[ClaimInput] = Field(default_factory=list, max_length=8)

    @field_validator("claims")
    @classmethod
    def unique_topics(cls, value):
        if len({c.topic.casefold() for c in value}) != len(value):
            raise ValueError("同一来源的观点主题不能重复")
        return value


class MetadataInput(ConfirmInput):
    url: str = Field(min_length=1, max_length=2000)
    skill_ids: list[str] = Field(default_factory=list, max_length=10)


class CompareInput(Input):
    source_ids: list[str] = Field(min_length=2, max_length=4)

    @field_validator("source_ids")
    @classmethod
    def unique(cls, value):
        if len(set(value)) != len(value):
            raise ValueError("请选择不同的来源")
        return value


def register_sources(app, session_dependency, identity):
    Db, Identity = Depends(session_dependency), Depends(identity)
    Version = Annotated[str, Header(pattern=r'^"[1-9][0-9]*"$')]

    def validate_skills(db, skills):
        for skill_id in skills:
            catalogue.require_skill(db, skill_id)
        return sorted(set(skills))

    @app.get("/api/v1/source-discovery")
    def discover(
        q: Annotated[str, Query(max_length=200)] = "",
        skill_id: str | None = None,
        job_id: str | None = None,
        kind: Literal[
            "all",
            "project",
            "documentation",
            "community",
            "video",
            "article",
            "course",
            "exercise",
            "experience",
        ] = "all",
        platform: Literal["all", "github", "xiaohongshu", "douyin", "other"] = "all",
        db=Db,
        principal=Identity,
    ):
        return source_library.discover(db, principal, q, skill_id, job_id, kind, platform)

    @app.get("/api/v1/source-documents")
    def listing(db=Db, principal=Identity):
        return source_library.listing(db, principal)

    @app.post("/api/v1/source-documents", status_code=201)
    def import_document(body: DocumentInput, request: Request, db=Db, principal=Identity):
        args = body.model_dump(mode="json", exclude={"confirmed", "rights_confirmed"})
        args["skill_ids"] = validate_skills(db, args["skill_ids"])
        result = source_library.import_document(db, principal, args, request.state.trace_id)
        db.commit()
        return result

    @app.get("/api/v1/source-documents/{source_id}")
    def get(source_id: str, db=Db, principal=Identity):
        return growth.data(source_library.owned(db, principal, source_id))

    @app.delete("/api/v1/source-documents/{source_id}", status_code=204)
    def delete(source_id: str, if_match: Version, request: Request, db=Db, principal=Identity):
        source_library.delete_document(
            db, principal, source_id, int(if_match.strip('"')), request.state.trace_id
        )
        db.commit()

    @app.post("/api/v1/source-comparisons")
    def compare(body: CompareInput, db=Db, principal=Identity):
        return source_library.compare(db, principal, body.source_ids)

    @app.post("/api/v1/source-github-metadata", status_code=201)
    def metadata(body: MetadataInput, request: Request, db=Db, principal=Identity):
        skills = validate_skills(db, body.skill_ids)
        result = github_sources.read(db, principal, body.url, skills, request.state.trace_id)
        db.commit()
        return result
