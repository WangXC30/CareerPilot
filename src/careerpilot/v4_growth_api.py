"""Private V4 directions, scenarios and local intelligence routes."""

import json
from typing import Annotated, Literal

from fastapi import Depends, Header, Query, Request
from pydantic import Field

from careerpilot import adaptive, analytics, directions, growth, insights, practice_review, tutoring
from careerpilot.growth_api import ConfirmInput, Input


class DirectionInput(ConfirmInput):
    title: str = Field(min_length=1, max_length=100)
    fact_ids: list[str] = Field(default_factory=list, max_length=100)
    project_ids: list[str] = Field(default_factory=list, max_length=30)


class RestoreInput(ConfirmInput):
    target_version: int = Field(ge=1)


class BrandProfileInput(ConfirmInput):
    translation_id: str = Field(min_length=36, max_length=36)
    fact_ids: list[str] = Field(min_length=1, max_length=20)
    headline: str = Field(min_length=1, max_length=120)
    privacy_reviewed: Literal[True]


class TranslationMapping(Input):
    fact_id: str = Field(min_length=36, max_length=36)
    text: str = Field(min_length=1, max_length=4000)


class TranslationInput(ConfirmInput):
    resume_version_id: str = Field(min_length=36, max_length=36)
    locale: Literal["zh-CN", "en-US"]
    mappings: list[TranslationMapping] = Field(min_length=1, max_length=100)


class ScenarioInput(Input):
    career_ids: list[str] = Field(min_length=1, max_length=4)
    weekly_minutes: int = Field(ge=15, le=10080)


class TrendInput(ConfirmInput):
    keyword: str = Field(min_length=1, max_length=100)
    status: Literal["active", "paused"] = "active"
    interval_hours: Literal[6, 24, 168] = 24


class TeachingInput(ConfirmInput):
    skill_id: str = Field(min_length=1, max_length=80)
    question_index: int = Field(ge=0, le=20)
    level: int = Field(ge=1, le=3)
    attempt: str = Field(default="", max_length=2000)


class AdaptiveInput(ConfirmInput):
    skill_ids: list[str] = Field(min_length=1, max_length=8)


class AdaptiveAnswer(ConfirmInput):
    choice: int = Field(ge=0, le=2, strict=True)


class AppealInput(ConfirmInput):
    text: str = Field(min_length=1, max_length=2000)


class SnapshotInput(ConfirmInput):
    job_id: str = Field(min_length=36, max_length=36)


class TutorStart(ConfirmInput):
    exam_id: str = Field(min_length=36, max_length=36)
    answer_index: int = Field(ge=0, le=23)


class TutorTurn(ConfirmInput):
    action: Literal["reflect", "hint"] = "reflect"
    focus: Literal["concept", "condition", "counterexample"] = "concept"
    attempt: str = Field(min_length=1, max_length=2000)
    question: str = Field(default="", max_length=2000)


class PeerRequest(ConfirmInput):
    workspace_id: str = Field(min_length=36, max_length=36)
    reviewer_id: str = Field(min_length=36, max_length=36)


class PeerDecision(ConfirmInput):
    conclusion: Literal["context_explains_timing", "needs_followup", "inconclusive"]
    note: str = Field(min_length=1, max_length=2000)


class AnalyticsInput(ConfirmInput):
    event: Literal[
        "learning_completed", "assessment_completed", "project_completed", "application_recorded"
    ]


def register_v4_growth(app, session_dependency, identity):
    Db, Identity = Depends(session_dependency), Depends(identity)
    Version = Annotated[str, Header(pattern=r'^"[1-9][0-9]*"$')]

    @app.get("/api/v1/direction-views")
    def directions_list(db=Db, principal=Identity):
        return [growth.data(r) for r in db.scalars(growth.owner_query(principal, "direction_view"))]

    @app.post("/api/v1/direction-views", status_code=201)
    def direction_create(body: DirectionInput, request: Request, db=Db, principal=Identity):
        result = directions.save_direction(
            db, principal, body.model_dump(exclude={"confirmed"}), request.state.trace_id
        )
        db.commit()
        return result

    @app.put("/api/v1/direction-views/{key}")
    def direction_update(
        key: str,
        body: DirectionInput,
        request: Request,
        if_match: Version,
        db=Db,
        principal=Identity,
    ):
        result = directions.save_direction(
            db,
            principal,
            body.model_dump(exclude={"confirmed"}),
            request.state.trace_id,
            key,
            int(if_match.strip('"')),
        )
        db.commit()
        return result

    @app.get("/api/v1/direction-views/{key}")
    def direction_get(key: str, db=Db, principal=Identity):
        return directions.direction_detail(db, principal, key)

    @app.post("/api/v1/direction-views/{key}/restore")
    def direction_restore(
        key: str, body: RestoreInput, request: Request, if_match: Version, db=Db, principal=Identity
    ):
        result = directions.restore_direction(
            db,
            principal,
            key,
            body.target_version,
            int(if_match.strip('"')),
            request.state.trace_id,
        )
        db.commit()
        return result

    @app.post("/api/v1/resume-translations", status_code=201)
    def translate(body: TranslationInput, request: Request, db=Db, principal=Identity):
        result = directions.translate(db, principal, body.model_dump(), request.state.trace_id)
        db.commit()
        return result

    @app.get("/api/v1/resume-translations")
    def translations(db=Db, principal=Identity):
        return [
            directions.translation_detail(db, principal, r.id)
            for r in db.scalars(growth.owner_query(principal, "resume_translation"))
        ]

    @app.post("/api/v1/career-scenarios")
    def scenarios(body: ScenarioInput, db=Db, principal=Identity):
        return insights.scenarios(db, principal, body.career_ids, body.weekly_minutes)

    @app.post("/api/v1/multi-target-jobs")
    def targets(body: ScenarioInput, db=Db, principal=Identity):
        return insights.multi_target(db, principal, body.career_ids)

    @app.get("/api/v1/recruitment-timeline")
    def timeline(db=Db, principal=Identity):
        return insights.timeline(db, principal)

    @app.get("/api/v1/trend-subscriptions")
    def trends(db=Db, principal=Identity):
        return [
            growth.data(r) for r in db.scalars(growth.owner_query(principal, "trend_subscription"))
        ]

    @app.post("/api/v1/trend-subscriptions", status_code=201)
    def trend_create(body: TrendInput, request: Request, db=Db, principal=Identity):
        result = insights.create_trend(
            db, principal, body.keyword, body.status, body.interval_hours, request.state.trace_id
        )
        db.commit()
        return result

    @app.post("/api/v1/trend-subscriptions/{key}/refresh")
    def trend_refresh(key: str, body: ConfirmInput, request: Request, db=Db, principal=Identity):
        result = insights.trend_refresh(db, principal, key, request.state.trace_id)
        db.commit()
        return result

    @app.delete("/api/v1/trend-subscriptions/{key}")
    def trend_pause(key: str, request: Request, db=Db, principal=Identity):
        growth.lock_owner(db, principal)
        row = growth.owned(db, principal, key, "trend_subscription")
        result = growth.save(
            db,
            principal,
            row.kind,
            {
                **json.loads(row.payload_json),
                "status": "paused",
                "next_check_at": None,
                "pending_notice": None,
            },
            request.state.trace_id,
            row,
            row.version,
        )
        db.commit()
        return result

    @app.post("/api/v1/teaching-turns", status_code=201)
    def teach(body: TeachingInput, request: Request, db=Db, principal=Identity):
        growth.scan_upload(body.attempt.encode(), "attempt.txt")
        result = insights.teach(db, principal, body.model_dump(), request.state.trace_id)
        db.commit()
        return result

    @app.post("/api/v1/adaptive-exams", status_code=201)
    def adaptive_create(body: AdaptiveInput, request: Request, db=Db, principal=Identity):
        result = adaptive.start(db, principal, body.skill_ids, request.state.trace_id)
        db.commit()
        return result

    @app.get("/api/v1/adaptive-exams")
    def adaptive_list(db=Db, principal=Identity):
        return [
            adaptive.public(growth.data(r))
            for r in db.scalars(growth.owner_query(principal, "adaptive_exam"))
        ]

    @app.post("/api/v1/adaptive-exams/{key}/answer")
    def adaptive_answer(
        key: str,
        body: AdaptiveAnswer,
        request: Request,
        if_match: Version,
        db=Db,
        principal=Identity,
    ):
        result = adaptive.answer(
            db, principal, key, body.choice, int(if_match.strip('"')), request.state.trace_id
        )
        db.commit()
        return result

    @app.post("/api/v1/adaptive-exams/{key}/appeal")
    def adaptive_appeal(
        key: str, body: AppealInput, request: Request, if_match: Version, db=Db, principal=Identity
    ):
        result = adaptive.appeal(
            db, principal, key, body.text, int(if_match.strip('"')), request.state.trace_id
        )
        db.commit()
        return result

    @app.post("/api/v1/matching-snapshots", status_code=201)
    def snapshot(body: SnapshotInput, request: Request, db=Db, principal=Identity):
        result = directions.matching_snapshot(db, principal, body.job_id, request.state.trace_id)
        db.commit()
        return result

    @app.get("/api/v1/matching-snapshots/{key}/replay")
    def replay(key: str, db=Db, principal=Identity):
        return directions.replay_matching(db, principal, key)

    @app.get("/api/v1/matching-snapshots")
    def matching_history(db=Db, principal=Identity):
        return [
            {
                "id": r.id,
                "created_at": r.created_at,
                "title": value["result"]["title"],
                "score": value["result"]["score"],
                "rule_version": value["rule_version"],
                "weight_version": value["result"]["weight_version"],
                "catalogue_versions": {
                    k: v["version"] for k, v in value["catalogue_snapshot"].items()
                },
                "as_of_date": value["as_of_date"],
            }
            for r in db.scalars(
                growth.owner_query(principal, "matching_snapshot")
                .order_by(growth.GrowthRecord.created_at.desc(), growth.GrowthRecord.id)
                .limit(50)
            )
            if (value := growth.data(r))
        ]

    @app.post("/api/v1/analytics-events")
    def event(body: AnalyticsInput, request: Request, db=Db, principal=Identity):
        result = analytics.record(db, principal, body.event, request.state.trace_id)
        db.commit()
        return result

    @app.get("/api/v1/analytics-summary")
    def analytics_summary(db=Db, principal=Identity):
        return analytics.owner_summary(db, principal)

    @app.post("/api/v1/brand-profiles", status_code=201)
    def brand_create(body: BrandProfileInput, request: Request, db=Db, principal=Identity):
        from careerpilot import brand_profile

        value = brand_profile.create(
            db, principal, body.translation_id, body.fact_ids, body.headline, request.state.trace_id
        )
        db.commit()
        return value

    @app.get("/api/v1/brand-profiles")
    def brand_list(db=Db, principal=Identity):
        return [growth.data(r) for r in db.scalars(growth.owner_query(principal, "brand_profile"))]

    @app.get("/api/v1/source-channels")
    def source_channels(q: str = Query(default="", max_length=200), db=Db, principal=Identity):
        from careerpilot import source_channels

        return source_channels.search(db, principal, q)

    @app.post("/api/v1/teaching-sessions", status_code=201)
    def tutor_start(body: TutorStart, request: Request, db=Db, principal=Identity):
        value = tutoring.start(
            db, principal, body.exam_id, body.answer_index, request.state.trace_id
        )
        db.commit()
        return value

    @app.get("/api/v1/teaching-sessions")
    def tutor_sessions(db=Db, principal=Identity):
        return [
            growth.data(r)
            for r in db.scalars(
                growth.owner_query(principal, "teaching_session")
                .order_by(growth.GrowthRecord.created_at.desc(), growth.GrowthRecord.id)
                .limit(50)
            )
        ]

    @app.get("/api/v1/teaching-sessions/{key}")
    def tutor_detail(key: str, db=Db, principal=Identity):
        return growth.data(growth.owned(db, principal, key, "teaching_session"))

    @app.post("/api/v1/teaching-sessions/{key}/turns")
    def tutor_turn(
        key: str, body: TutorTurn, if_match: Version, request: Request, db=Db, principal=Identity
    ):
        value = tutoring.turn(
            db,
            principal,
            key,
            body.model_dump(exclude={"confirmed"}),
            int(if_match.strip('"')),
            request.state.trace_id,
        )
        db.commit()
        return value

    @app.delete("/api/v1/teaching-sessions/{key}")
    def tutor_remove(key: str, db=Db, principal=Identity):
        value = tutoring.remove(db, principal, key)
        db.commit()
        return value

    @app.post("/api/v1/adaptive-exams/{key}/review-requests", status_code=201)
    def peer_request(key: str, body: PeerRequest, request: Request, db=Db, principal=Identity):
        value = practice_review.create(
            db, principal, key, body.workspace_id, body.reviewer_id, request.state.trace_id
        )
        db.commit()
        return value

    @app.get("/api/v1/practice-reviews")
    def peer_reviews(db=Db, principal=Identity):
        return practice_review.listing(db, principal)

    @app.get("/api/v1/practice-reviews/{key}")
    def peer_detail(key: str, db=Db, principal=Identity):
        return practice_review.view(db, principal, practice_review.visible(db, principal, key))

    @app.post("/api/v1/practice-reviews/{key}/decision")
    def peer_decision(
        key: str, body: PeerDecision, if_match: Version, request: Request, db=Db, principal=Identity
    ):
        value = practice_review.decide(
            db,
            principal,
            key,
            body.conclusion,
            body.note,
            int(if_match.strip('"')),
            request.state.trace_id,
        )
        db.commit()
        return value

    @app.delete("/api/v1/practice-reviews/{key}")
    def peer_revoke(key: str, if_match: Version, request: Request, db=Db, principal=Identity):
        value = practice_review.revoke(
            db, principal, key, int(if_match.strip('"')), request.state.trace_id
        )
        db.commit()
        return value

    @app.get("/api/v1/admin/analytics-aggregate")
    def aggregate(db=Db, principal=Identity):
        return analytics.aggregate(db, principal)
