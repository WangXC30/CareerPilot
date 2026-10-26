"""V1 account, catalogue, evidence, learning and assistant HTTP contracts."""

import hashlib
import hmac
import json
import re
from datetime import date as Date
from typing import Annotated, Literal

from fastapi import Depends, Header, Query, Request, Response
from pydantic import ConfigDict, Field, field_validator, model_validator

from careerpilot import accounts, assessment, assistant, catalogue, growth, i18n, learning, market
from careerpilot.domain import DomainError
from careerpilot.growth_api import ConfirmInput, Input
from careerpilot.platform_models import BackgroundTask
from careerpilot.safety import scan_upload
from careerpilot.skill_graph import project as skill_graph
from careerpilot.v1_models import DeletionRequest, EvidenceRevocation, SkillEvidence
from careerpilot.web_sources import fetch, search


class LoginInput(Input):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=12, max_length=128)

    @field_validator("email")
    @classmethod
    def email_address(cls, value):
        value = value.strip()
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
            raise ValueError("Invalid email")
        return value.casefold()


class RegistrationInput(LoginInput):
    display_name: str = Field(min_length=1, max_length=100)
    privacy_accepted: Literal[True]


class DeleteInput(Input):
    confirmation: Literal["DELETE MY ACCOUNT"]


class DependenciesInput(Input):
    requires: list[str] = Field(max_length=30)


class SkillInput(Input):
    skill_id: str = Field(min_length=1, max_length=80)


class AnswersInput(Input):
    answers: list[Annotated[int, Field(ge=0, le=2, strict=True)]] = Field(
        min_length=1, max_length=30
    )


class GapInput(Input):
    job_ids: list[str] = Field(default_factory=list, max_length=20)


class SkillGraphInput(Input):
    job_ids: list[Annotated[str, Field(min_length=1, max_length=36)]] = Field(
        min_length=3, max_length=20
    )


class PlanInput(GapInput):
    start_date: Date
    deadline: Date | None = None


class RestorePlanInput(ConfirmInput):
    target_version: int = Field(ge=1)


class TaskAction(Input):
    action: Literal[
        "start", "pause", "complete", "skip", "lock", "unlock", "defer", "split", "attach"
    ]
    date: Date | None = None
    minutes: int | None = Field(default=None, ge=1, le=10080)
    outcome: str = Field(default="", max_length=4000)
    material_id: str | None = Field(default=None, max_length=36)

    @model_validator(mode="after")
    def required_arguments(self):
        if (
            self.action == "defer"
            and self.date is None
            or self.action == "split"
            and self.minutes is None
            or self.action == "attach"
            and self.material_id is None
        ):
            raise ValueError("Action argument required")
        return self


class FilterInput(Input):
    q: str = Field(default="", max_length=200)
    location: str = Field(default="", max_length=100)
    company: str = Field(default="", max_length=100)
    education: str = Field(default="", max_length=100)
    graduation_cohort: str = Field(default="", max_length=20)
    employment_type: str = Field(default="", max_length=40)
    duration: str = Field(default="", max_length=100)
    status: Literal["", "unverified", "verified", "possibly_expired", "closed"] = ""
    salary_min: int | None = Field(default=None, ge=0, le=1000000)
    salary_unit: Literal["month", "day"] = "month"
    deadline_before: Date | None = None


class SaveFilterInput(FilterInput):
    name: str = Field(min_length=1, max_length=100)


class MergeInput(ConfirmInput):
    source_id: str = Field(max_length=36)


class JobStatusInput(ConfirmInput):
    status: Literal["unverified", "verified", "possibly_expired", "closed"]
    reason: str = Field(min_length=1, max_length=200)


class UrlInput(Input):
    url: str = Field(min_length=1, max_length=2000)


class RevokeInput(ConfirmInput):
    reason: str = Field(min_length=1, max_length=200)


class AssistantInput(GapInput):
    message: str = Field(min_length=1, max_length=4000)
    intent: Literal["auto", "diagnose", "plan", "progress", "resources", "replan"] = "auto"
    plan_id: str | None = Field(default=None, max_length=36)


class MemoryResolution(ConfirmInput):
    preferred_id: str = Field(max_length=36)
    superseded_id: str = Field(max_length=36)


class MilestoneInput(ConfirmInput):
    title: str = Field(min_length=1, max_length=200)
    target_date: Date


class MilestoneState(ConfirmInput):
    completed: bool


def register_v1(app, session_dependency, identity):
    Db, Identity = Depends(session_dependency), Depends(identity)
    Version = Annotated[str, Header(pattern=r'^"[1-9][0-9]*"$')]

    @app.post("/api/v1/skill-graph")
    def graph_projection(body: SkillGraphInput, db=Db, principal=Identity):
        return skill_graph(db, principal, body.job_ids)

    @app.get("/api/v1/goal-milestones")
    def milestones(db=Db, principal=Identity):
        return [growth.data(r) for r in db.scalars(growth.owner_query(principal, "milestone"))]

    @app.post("/api/v1/career-goals/{goal_id}/milestones", status_code=201)
    def milestone(goal_id: str, body: MilestoneInput, request: Request, db=Db, principal=Identity):
        from careerpilot.application import owned_goal

        owned_goal(db, principal, goal_id)
        result = growth.save(
            db,
            principal,
            "milestone",
            {
                "goal_id": goal_id,
                "title": body.title,
                "target_date": body.target_date.isoformat(),
                "completed": False,
            },
            request.state.trace_id,
        )
        db.commit()
        return result

    @app.patch("/api/v1/goal-milestones/{milestone_id}")
    def milestone_state(
        milestone_id: str,
        body: MilestoneState,
        if_match: Version,
        request: Request,
        db=Db,
        principal=Identity,
    ):
        row = growth.owned(db, principal, milestone_id, "milestone")
        payload = json.loads(row.payload_json)
        result = growth.save(
            db,
            principal,
            "milestone",
            {**payload, "completed": body.completed},
            request.state.trace_id,
            row,
            int(if_match.strip('"')),
        )
        db.commit()
        return result

    @app.post("/api/v1/auth/register", status_code=201)
    def register(body: RegistrationInput, request: Request, db=Db):
        accounts.throttle(db, body.email, request.client.host if request.client else "unknown")
        result = accounts.register(
            db, body.email, body.password, body.display_name, request.headers.get("user-agent", "")
        )
        db.commit()
        return result

    @app.post("/api/v1/auth/login")
    def login(body: LoginInput, request: Request, db=Db):
        accounts.throttle(db, body.email, request.client.host if request.client else "unknown")
        result = accounts.login(
            db, body.email, body.password, request.headers.get("user-agent", "")
        )
        db.commit()
        return result

    @app.get("/api/v1/account/sessions")
    def sessions(request: Request, db=Db, principal=Identity):
        return accounts.active_sessions(db, principal, request.state.session_digest)

    @app.delete("/api/v1/account/sessions/{session_id}")
    def revoke_session(session_id: str, request: Request, db=Db, principal=Identity):
        result = accounts.revoke_session(db, principal, session_id, request.state.trace_id)
        db.commit()
        return result

    @app.get("/api/v1/account/export")
    def export(db=Db, principal=Identity):
        return accounts.export_data(db, principal)

    @app.post("/api/v1/account/deletion", status_code=202)
    def deletion(body: DeleteInput, request: Request, db=Db, principal=Identity):
        result = accounts.request_deletion(db, principal, request.state.trace_id)
        db.commit()
        return result

    @app.get("/api/v1/account/deletion/{request_id}")
    def deletion_status(
        request_id: str, x_deletion_receipt: Annotated[str, Header(max_length=200)], db=Db
    ):
        row = db.get(DeletionRequest, request_id)
        digest = hashlib.sha256(x_deletion_receipt.encode()).hexdigest()
        if row is None or not hmac.compare_digest(digest, row.receipt_digest):
            raise DomainError("NOT_FOUND", "删除请求不存在", 404)
        return {"id": row.id, "status": row.status, "completed_at": row.completed_at}

    @app.get("/api/v1/catalogue/{kind}")
    def catalogue_list(kind: Literal["skill", "career", "resource"], db=Db, principal=Identity):
        return catalogue.entries(db, kind)

    @app.put("/api/v1/catalogue/skill/{skill_id}/dependencies")
    def dependencies(
        skill_id: str, body: DependenciesInput, if_match: Version, db=Db, principal=Identity
    ):
        if principal.role not in {"operator", "admin"}:
            raise DomainError("FORBIDDEN", "此操作需要词库运营权限", 403)
        catalogue.update_dependencies(db, skill_id, body.requires, int(if_match.strip('"')))
        db.rollback()
        raise DomainError(
            "PUBLIC_REVIEW_REQUIRED", "校验通过；请通过公共变更单独立审核、发布依赖修改", 409
        )

    @app.get("/api/v1/skills")
    def skills(db=Db, principal=Identity):
        return learning.snapshots(db, principal)

    @app.get("/api/v1/skill-evidence")
    def evidence(db=Db, principal=Identity):
        return learning.ledger(db, principal)

    @app.post("/api/v1/skill-evidence/{evidence_id}/revoke")
    def revoke(evidence_id: str, body: RevokeInput, request: Request, db=Db, principal=Identity):
        row = db.scalar(
            learning.rows(db, principal, SkillEvidence).where(SkillEvidence.id == evidence_id)
        )
        if row is None:
            raise DomainError("NOT_FOUND", "证据不存在", 404)
        if db.get(EvidenceRevocation, evidence_id) is None:
            db.add(
                EvidenceRevocation(
                    evidence_id=evidence_id,
                    user_id=principal.user_id,
                    tenant_id=principal.tenant_id,
                    reason=body.reason,
                )
            )
            db.flush()
        preview = learning.auto_preview(db, principal, "EvidenceChanged", request.state.trace_id)
        db.commit()
        return {"revoked": True, "replan_preview": preview}

    @app.post("/api/v1/gap-analysis")
    def gaps(body: GapInput, db=Db, principal=Identity):
        return learning.gap(db, principal, body.job_ids)

    @app.post("/api/v1/plans", status_code=201)
    def create_plan(body: PlanInput, request: Request, db=Db, principal=Identity):
        result = learning.create_plan(
            db,
            principal,
            body.job_ids,
            body.start_date.isoformat(),
            body.deadline.isoformat() if body.deadline else None,
            request.state.trace_id,
        )
        db.commit()
        return result

    @app.get("/api/v1/plans")
    def plans(db=Db, principal=Identity):
        return [
            growth.data(row)
            for row in db.scalars(
                growth.owner_query(principal, "plan").order_by(
                    growth.GrowthRecord.created_at.desc()
                )
            )
        ]

    @app.get("/api/v1/plans/{plan_id}")
    def plan(plan_id: str, db=Db, principal=Identity):
        return growth.data(growth.owned(db, principal, plan_id, "plan"))

    @app.get("/api/v1/plans/{plan_id}/history")
    def plan_history(plan_id: str, db=Db, principal=Identity):
        return growth.history(db, principal, plan_id, "plan")

    @app.post("/api/v1/plans/{plan_id}/publish")
    def publish(
        plan_id: str,
        body: ConfirmInput,
        if_match: Version,
        request: Request,
        db=Db,
        principal=Identity,
    ):
        result = learning.publish_plan(
            db, principal, plan_id, int(if_match.strip('"')), request.state.trace_id
        )
        db.commit()
        return result

    @app.post("/api/v1/plans/{plan_id}/tasks/{task_id}")
    def action(
        plan_id: str,
        task_id: str,
        body: TaskAction,
        if_match: Version,
        request: Request,
        db=Db,
        principal=Identity,
    ):
        scan_upload(body.outcome.encode(), "outcome.txt")
        result = learning.task_action(
            db,
            principal,
            plan_id,
            task_id,
            body.model_dump(mode="json"),
            int(if_match.strip('"')),
            request.state.trace_id,
        )
        db.commit()
        return result

    @app.post("/api/v1/plans/{plan_id}/replan")
    def replan(plan_id: str, request: Request, response: Response, db=Db, principal=Identity):
        result = learning.replan_preview(
            db, principal, plan_id, "UserRequested", request.state.trace_id
        )
        db.commit()
        locale = i18n.negotiate(request.headers.get("accept-language"))
        response.headers.update({"Content-Language": locale, "Vary": "Accept-Language"})
        return i18n.plan_preview(result, locale)

    @app.post("/api/v1/plan-previews/{preview_id}/{decision}")
    def decide(
        preview_id: str,
        decision: Literal["accept", "reject"],
        body: ConfirmInput,
        if_match: Version,
        request: Request,
        db=Db,
        principal=Identity,
    ):
        result = learning.decide_preview(
            db,
            principal,
            preview_id,
            decision == "accept",
            int(if_match.strip('"')),
            request.state.trace_id,
        )
        db.commit()
        return result

    @app.get("/api/v1/plan-previews")
    def previews(request: Request, response: Response, db=Db, principal=Identity):
        result = [
            growth.data(r)
            for r in db.scalars(
                growth.owner_query(principal, "plan_preview")
                .order_by(growth.GrowthRecord.created_at.desc())
                .limit(20)
            )
        ]
        active = learning.active_plan(db, principal)
        current_hash = learning.context_hash(db, principal) if active else None
        for item in result:
            item["stale"] = (
                not active
                or item["plan_id"] != active.id
                or item["base_version"] != active.version
                or item["context_hash"] != current_hash
            )
            if source := item.get("interview_source"):
                review = db.scalar(
                    growth.owner_query(principal, "interview_review").where(
                        growth.GrowthRecord.id == source["review_id"]
                    )
                )
                item["stale"] = (
                    item["stale"]
                    or review is None
                    or review.version != source["review_version"]
                    or growth.data(review)["status"] != "confirmed"
                )
            if source := item.get("project_source"):
                from careerpilot.project_workflow import source_context

                try:
                    item["stale"] = (
                        item["stale"]
                        or source_context(db, principal, source["project_id"])
                        != source["source_context"]
                    )
                except DomainError:
                    item["stale"] = True
        locale = i18n.negotiate(request.headers.get("accept-language"))
        response.headers.update({"Content-Language": locale, "Vary": "Accept-Language"})
        return [i18n.plan_preview(item, locale) for item in result]

    @app.post("/api/v1/plans/{plan_id}/restore")
    def restore(
        plan_id: str,
        body: RestorePlanInput,
        if_match: Version,
        request: Request,
        db=Db,
        principal=Identity,
    ):
        result = learning.restore_plan(
            db,
            principal,
            plan_id,
            body.target_version,
            int(if_match.strip('"')),
            request.state.trace_id,
        )
        db.commit()
        return result

    @app.post("/api/v1/assessments", status_code=201)
    def new_assessment(body: SkillInput, request: Request, db=Db, principal=Identity):
        result = assessment.create(db, principal, body.skill_id, request.state.trace_id)
        db.commit()
        return result

    @app.get("/api/v1/assessments/{assessment_id}")
    def get_assessment(assessment_id: str, db=Db, principal=Identity):
        return growth.data(growth.owned(db, principal, assessment_id, "assessment"))

    @app.post("/api/v1/assessments/{assessment_id}/submit")
    def submit(
        assessment_id: str,
        body: AnswersInput,
        if_match: Version,
        request: Request,
        db=Db,
        principal=Identity,
    ):
        result = assessment.submit(
            db,
            principal,
            assessment_id,
            body.answers,
            int(if_match.strip('"')),
            request.state.trace_id,
        )
        db.commit()
        return result

    @app.post("/api/v1/job-search")
    def job_search(
        body: FilterInput,
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
        cursor: str | None = None,
        descending: bool = False,
        db=Db,
        principal=Identity,
    ):
        return market.search(db, principal, body.model_dump(mode="json"), limit, cursor, descending)

    @app.get("/api/v1/job-filters")
    def filters(db=Db, principal=Identity):
        return [growth.data(r) for r in db.scalars(growth.owner_query(principal, "job_filter"))]

    @app.post("/api/v1/job-filters", status_code=201)
    def save_filter(body: SaveFilterInput, request: Request, db=Db, principal=Identity):
        result = growth.save(
            db, principal, "job_filter", body.model_dump(mode="json"), request.state.trace_id
        )
        db.commit()
        return result

    @app.post("/api/v1/jobs/{job_id}/merge")
    def merge(
        job_id: str,
        body: MergeInput,
        if_match: Version,
        request: Request,
        db=Db,
        principal=Identity,
    ):
        result = market.merge(
            db, principal, job_id, body.source_id, int(if_match.strip('"')), request.state.trace_id
        )
        db.commit()
        return result

    @app.post("/api/v1/jobs/{job_id}/status")
    def job_status(
        job_id: str,
        body: JobStatusInput,
        if_match: Version,
        request: Request,
        db=Db,
        principal=Identity,
    ):
        result = market.lifecycle(
            db,
            principal,
            job_id,
            body.status,
            body.reason,
            int(if_match.strip('"')),
            request.state.trace_id,
        )
        db.commit()
        return result

    @app.get("/api/v1/web/search")
    def web_search(
        request: Request,
        response: Response,
        q: Annotated[str, Query(max_length=200)] = "",
        db=Db,
        principal=Identity,
    ):
        result = search(db, q, principal)
        locale = i18n.negotiate(request.headers.get("accept-language"))
        response.headers.update({"Content-Language": locale, "Vary": "Accept-Language"})
        if locale == "en-US":
            result["notice"] = i18n.GROWTH_MESSAGES_EN["source_search"]
        return result

    @app.post("/api/v1/web/fetch")
    def web_fetch(body: UrlInput, db=Db, principal=Identity):
        result = fetch(db, principal, body.url)
        db.commit()
        return result

    @app.post("/api/v1/assistant/runs", status_code=202)
    def assistant_run(
        body: AssistantInput,
        request: Request,
        idempotency_key: Annotated[str, Header(min_length=1, max_length=128)],
        db=Db,
        principal=Identity,
    ):
        scan_upload(body.message.encode(), "message.txt")
        result = assistant.enqueue(
            db, principal, body.model_dump(), idempotency_key, request.state.trace_id
        )
        db.commit()
        return result

    @app.get("/api/v1/assistant/runs/{run_id}")
    def assistant_result(
        run_id: str, request: Request, response: Response, db=Db, principal=Identity
    ):
        result = growth.data(growth.owned(db, principal, run_id, "agent_run"))
        task = db.get(BackgroundTask, run_id)
        if task and task.status in {"failed", "cancelled"} and result["status"] != "success":
            result.update(
                status=task.status,
                summary="助手任务未完成，请重新发起。",
                result={"code": task.last_error_class or "TASK_CANCELLED"},
            )
        locale = i18n.negotiate(request.headers.get("accept-language"))
        response.headers.update({"Content-Language": locale, "Vary": "Accept-Language"})
        return i18n.assistant_result(result, locale)

    @app.get("/api/v1/memory-conflicts")
    def conflicts(db=Db, principal=Identity):
        memories = [growth.data(r) for r in db.scalars(growth.owner_query(principal, "memory"))]
        result = []
        for skill in catalogue.entries(db, "skill"):
            candidates = [
                m
                for m in memories
                if not m.get("superseded_by")
                and any(a.casefold() in m["text"].casefold() for a in skill["aliases"])
            ]
            if len({m["text"] for m in candidates}) > 1:
                result.append(
                    {
                        "skill_id": skill["id"],
                        "status": "requires_user_review",
                        "memories": candidates,
                    }
                )
        return result

    @app.post("/api/v1/memory-conflicts/resolve")
    def resolve(
        body: MemoryResolution, if_match: Version, request: Request, db=Db, principal=Identity
    ):
        growth.lock_owner(db, principal)
        preferred = growth.owned(db, principal, body.preferred_id, "memory")
        superseded = growth.owned(db, principal, body.superseded_id, "memory")
        if preferred.id == superseded.id:
            raise DomainError("INVALID_RESOLUTION", "请选择两条不同记录", 422)
        payload = json.loads(superseded.payload_json)
        result = growth.save(
            db,
            principal,
            "memory",
            {**payload, "superseded_by": preferred.id},
            request.state.trace_id,
            superseded,
            int(if_match.strip('"')),
        )
        db.commit()
        return result
