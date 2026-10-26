"""V2 matching HTTP adapter; all private reads use the authenticated owner."""

from typing import Annotated, Literal

from fastapi import Depends, Header, Query, Request, Response
from pydantic import Field, field_validator

from careerpilot import catalogue, growth, i18n, job_policy, matching, matching_i18n, subscriptions
from careerpilot.domain import DomainError
from careerpilot.growth_api import ConfirmInput, Input, ProfileInput
from careerpilot.platform_models import BackgroundTask
from careerpilot.v1_api import FilterInput


class MatchingConstraints(ConfirmInput):
    cities_hard: bool = True
    available_months: int | None = Field(default=None, ge=1, le=36)
    work_authorizations: list[str] | None = Field(default=None, max_length=20)
    qualifications: list[str] | None = Field(default=None, max_length=20)
    preferred_career_ids: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("work_authorizations", "qualifications", "preferred_career_ids")
    @classmethod
    def names(cls, value):
        if value is None:
            return None
        if any(not s.strip() or len(s) > 100 for s in value):
            raise ValueError("Invalid constraint")
        return list(dict.fromkeys(s.strip() for s in value))


class MatchInput(Input):
    job_ids: list[str] = Field(default_factory=list, max_length=100)


Mode = Literal["best", "stretch", "entry", "explore"]


def default_modes() -> list[Mode]:
    return ["best", "stretch", "entry", "explore"]


class PreferencesInput(ConfirmInput):
    favorite_ids: list[str] = Field(default_factory=list, max_length=500)
    blocked_companies: list[str] = Field(default_factory=list, max_length=100)
    excluded_keywords: list[str] = Field(default_factory=list, max_length=100)
    enabled_modes: list[Mode] = Field(default_factory=default_modes, max_length=4)

    @field_validator("favorite_ids", "blocked_companies", "excluded_keywords", "enabled_modes")
    @classmethod
    def values(cls, value):
        if any(not v.strip() or len(v) > 100 for v in value):
            raise ValueError("Invalid preference")
        return list(dict.fromkeys(v.strip() for v in value))


class FeedbackInput(ConfirmInput):
    label: Literal["like", "not_interested", "misclassified", "applied"]
    reason: str = Field(default="", max_length=1000)


class CompareInput(Input):
    job_ids: list[str] = Field(min_length=2, max_length=4)

    @field_validator("job_ids")
    @classmethod
    def distinct(cls, value):
        if len(value) != len(set(value)):
            raise ValueError("Choose distinct jobs")
        return value


class SubscriptionInput(ConfirmInput):
    name: str = Field(min_length=1, max_length=100)
    filters: FilterInput = Field(default_factory=FilterInput)
    favorites_only: bool = False
    frequency: Literal["daily", "weekly"] = "weekly"
    hour: int = Field(default=9, ge=0, le=23)
    weekday: int = Field(default=0, ge=0, le=6)
    timezone: str = Field(default="Asia/Shanghai", max_length=100)
    quiet_start: int = Field(default=22, ge=0, le=23)
    quiet_end: int = Field(default=8, ge=0, le=23)
    source: Literal["local_library"] = "local_library"

    @field_validator("timezone")
    @classmethod
    def timezone_name(cls, value):
        return ProfileInput.valid_timezone(value)


class SubscriptionState(ConfirmInput):
    status: Literal["active", "paused", "cancelled"]


class NotificationState(ConfirmInput):
    read: bool = True


def register_v2(app, session_dependency, identity):
    Db, Identity = Depends(session_dependency), Depends(identity)
    Version = Annotated[str, Header(pattern=r'^"[0-9]+"$')]
    Key = Annotated[str, Header(min_length=1, max_length=80)]

    def localized(value, request, response, kind="matches"):
        locale = i18n.negotiate(request.headers.get("accept-language"))
        response.headers.update({"Content-Language": locale, "Vary": "Accept-Language"})
        return matching_i18n.present(value, locale, kind)

    @app.get("/api/v1/job-preferences")
    def preferences(db=Db, principal=Identity):
        return job_policy.preferences(db, principal)

    @app.put("/api/v1/job-preferences")
    def put_preferences(
        body: PreferencesInput, if_match: Version, request: Request, db=Db, principal=Identity
    ):
        result = job_policy.save_preferences(
            db,
            principal,
            body.model_dump(exclude={"confirmed"}),
            int(if_match.strip('"')),
            request.state.trace_id,
        )
        db.commit()
        return result

    @app.get("/api/v1/saved-jobs")
    def saved_jobs(db=Db, principal=Identity):
        policy = job_policy.preferences(db, principal)
        return [
            growth.data(growth.owned(db, principal, key, "job")) for key in policy["favorite_ids"]
        ]

    @app.get("/api/v1/job-feedback")
    def feedback(db=Db, principal=Identity):
        return list(job_policy.feedback_map(db, principal).values())

    @app.put("/api/v1/jobs/{job_id}/feedback")
    def put_feedback(
        job_id: str,
        body: FeedbackInput,
        if_match: Version,
        request: Request,
        db=Db,
        principal=Identity,
    ):
        result = job_policy.save_feedback(
            db,
            principal,
            job_id,
            body.label,
            body.reason,
            int(if_match.strip('"')),
            request.state.trace_id,
        )
        db.commit()
        return result

    @app.post("/api/v1/jobs/{job_id}/feedback/undo")
    def undo_feedback(
        job_id: str,
        body: ConfirmInput,
        if_match: Version,
        request: Request,
        db=Db,
        principal=Identity,
    ):
        result = job_policy.save_feedback(
            db,
            principal,
            job_id,
            None,
            "",
            int(if_match.strip('"')),
            request.state.trace_id,
            undo=True,
        )
        db.commit()
        return result

    @app.get("/api/v1/jobs/{job_id}/feedback/history")
    def feedback_history(job_id: str, db=Db, principal=Identity):
        growth.owned(db, principal, job_id, "job")
        return growth.history(
            db, principal, job_policy.feedback_id(principal, job_id), "job_feedback"
        )

    @app.get("/api/v1/job-recommendations")
    def recommended(
        request: Request, response: Response, mode: Mode = "best", db=Db, principal=Identity
    ):
        return localized(matching.recommendations(db, principal, mode), request, response)

    @app.post("/api/v1/jobs/compare")
    def compare(
        body: CompareInput, request: Request, response: Response, db=Db, principal=Identity
    ):
        return localized(
            matching.compare_jobs(db, principal, body.job_ids), request, response, "compare"
        )

    @app.get("/api/v1/job-subscriptions")
    def list_subscriptions(request: Request, response: Response, db=Db, principal=Identity):
        result = [
            subscriptions.public(db, principal, r)
            for r in db.scalars(growth.owner_query(principal, "job_subscription"))
        ]
        return localized(result, request, response, "subscription")

    @app.post("/api/v1/job-subscriptions", status_code=201)
    def create_subscription(
        body: SubscriptionInput, idempotency_key: Key, request: Request, db=Db, principal=Identity
    ):
        result = subscriptions.create(
            db,
            principal,
            body.model_dump(mode="json", exclude={"confirmed"}),
            "subscription:create:" + idempotency_key,
            request.state.trace_id,
        )
        db.commit()
        return subscriptions.public(
            db, principal, growth.owned(db, principal, result["id"], "job_subscription")
        )

    @app.put("/api/v1/job-subscriptions/{subscription_id}")
    def configure_subscription(
        subscription_id: str,
        body: SubscriptionInput,
        if_match: Version,
        request: Request,
        db=Db,
        principal=Identity,
    ):
        result = subscriptions.update_config(
            db,
            principal,
            subscription_id,
            body.model_dump(mode="json", exclude={"confirmed"}),
            int(if_match.strip('"')),
            request.state.trace_id,
        )
        db.commit()
        return subscriptions.public(
            db, principal, growth.owned(db, principal, result["id"], "job_subscription")
        )

    @app.patch("/api/v1/job-subscriptions/{subscription_id}")
    def subscription_state(
        subscription_id: str,
        body: SubscriptionState,
        if_match: Version,
        request: Request,
        db=Db,
        principal=Identity,
    ):
        result = subscriptions.update_config(
            db,
            principal,
            subscription_id,
            {"status": body.status},
            int(if_match.strip('"')),
            request.state.trace_id,
        )
        db.commit()
        return subscriptions.public(
            db, principal, growth.owned(db, principal, result["id"], "job_subscription")
        )

    @app.post("/api/v1/job-subscriptions/{subscription_id}/check", status_code=202)
    def check_subscription(
        subscription_id: str,
        body: ConfirmInput,
        idempotency_key: Key,
        request: Request,
        db=Db,
        principal=Identity,
    ):
        import time

        growth.lock_owner(db, principal)
        row = growth.owned(db, principal, subscription_id, "job_subscription")
        result = subscriptions.enqueue_run(
            db,
            principal,
            row,
            f"subscription:manual:{subscription_id}:{idempotency_key}",
            request.state.trace_id,
            int(time.time()),
        )
        db.commit()
        return result

    @app.get("/api/v1/job-subscriptions/{subscription_id}/runs")
    def subscription_runs(subscription_id: str, db=Db, principal=Identity):
        from careerpilot.runtime import task_data

        growth.owned(db, principal, subscription_id, "job_subscription")
        result = []
        for row in db.scalars(
            growth.owner_query(principal, "subscription_run").order_by(
                growth.GrowthRecord.created_at.desc(), growth.GrowthRecord.id.desc()
            )
        ):
            item = growth.data(row)
            if item["subscription_id"] != subscription_id:
                continue
            task = db.get(BackgroundTask, item["id"])
            result.append({**item, "task": task_data(task) if task else None})
            if len(result) >= 20:
                break
        return result

    @app.get("/api/v1/job-notifications")
    def notifications(
        request: Request,
        response: Response,
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
        db=Db,
        principal=Identity,
    ):
        result = [
            growth.data(r)
            for r in db.scalars(
                growth.owner_query(principal, "job_digest")
                .order_by(growth.GrowthRecord.created_at.desc(), growth.GrowthRecord.id.desc())
                .limit(limit)
            )
        ]
        return localized(result, request, response, "subscription")

    @app.patch("/api/v1/job-notifications/{notification_id}")
    def notification_state(
        notification_id: str,
        body: NotificationState,
        if_match: Version,
        request: Request,
        db=Db,
        principal=Identity,
    ):
        row = growth.owned(db, principal, notification_id, "job_digest")
        payload = growth.data(row)
        result = growth.save(
            db,
            principal,
            "job_digest",
            {
                **{k: v for k, v in payload.items() if k not in {"id", "version", "created_at"}},
                "read": body.read,
            },
            request.state.trace_id,
            row,
            int(if_match.strip('"')),
        )
        db.commit()
        return result

    @app.get("/api/v1/matching-constraints")
    def constraints(db=Db, principal=Identity):
        return matching.constraints(db, principal)

    @app.put("/api/v1/matching-constraints")
    def put_constraints(
        body: MatchingConstraints, if_match: Version, request: Request, db=Db, principal=Identity
    ):
        known = {c["id"] for c in catalogue.entries(db, "career")}
        if not set(body.preferred_career_ids) <= known:
            raise DomainError("UNKNOWN_CAREER", "职业方向尚未收录", 422)
        growth.lock_owner(db, principal)
        row = db.scalar(growth.owner_query(principal, "matching_constraints"))
        result = growth.save(
            db,
            principal,
            "matching_constraints",
            body.model_dump(exclude={"confirmed"}),
            request.state.trace_id,
            row,
            int(if_match.strip('"')),
        )
        db.commit()
        return result

    @app.get("/api/v1/job-matches")
    def matches(request: Request, response: Response, db=Db, principal=Identity):
        return localized(matching.job_matches(db, principal), request, response)

    @app.get("/api/v1/jobs/{job_id}/match")
    def match(job_id: str, request: Request, response: Response, db=Db, principal=Identity):
        result = matching.job_matches(db, principal, [job_id])
        item = next(
            item
            for key in ("eligible", "needs_review", "excluded", "hidden")
            for item in result[key]
        )
        return localized(item, request, response, "item")

    @app.post("/api/v1/job-matches/recompute", status_code=201)
    def recompute(
        body: MatchInput, request: Request, response: Response, db=Db, principal=Identity
    ):
        # Immutable snapshot records retain the original explanation and dependency versions.
        result = matching.job_matches(db, principal, body.job_ids)
        snapshot = growth.save(db, principal, "match_snapshot", result, request.state.trace_id)
        db.commit()
        return localized(snapshot, request, response)

    @app.get("/api/v1/job-match-snapshots/{snapshot_id}")
    def snapshot(snapshot_id: str, request: Request, response: Response, db=Db, principal=Identity):
        value = growth.data(growth.owned(db, principal, snapshot_id, "match_snapshot"))
        return localized(value, request, response)

    @app.get("/api/v1/career-comparison")
    def comparison(request: Request, response: Response, db=Db, principal=Identity):
        return localized(matching.career_comparison(db, principal), request, response, "career")
