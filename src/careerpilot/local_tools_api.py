"""Authorized local media, calendar, application and replay experiments."""

import base64
import json
import time
from datetime import date
from typing import Annotated, Literal

from fastapi import Depends, Header, Query, Request, Response
from pydantic import Field

from careerpilot import (
    agent_experiments,
    application_lab,
    expert_comparison,
    expert_protocol,
    growth,
    media,
    model_comparison,
    personal_tools,
)
from careerpilot.domain import DomainError
from careerpilot.growth_api import ConfirmInput


class MediaInput(ConfirmInput):
    rights_confirmed: Literal[True]
    title: str = Field(min_length=1, max_length=200)
    kind: Literal["pdf", "image", "video"]
    source_url: str = Field(default="", max_length=2000)
    license_basis: str = Field(min_length=1, max_length=1000)
    content_base64: str = Field(min_length=1, max_length=11200000)
    retention_days: int = Field(default=7, ge=1, le=30)


class ModelPreviewInput(ConfirmInput):
    dataset: model_comparison.Dataset
    report: dict
    corrections: list[model_comparison.Correction] = Field(default_factory=list, max_length=100)


class ExpertPreviewInput(ConfirmInput):
    cases: list[expert_protocol.Case] = Field(min_length=4, max_length=100)
    report: dict
    corrections: list[expert_comparison.Correction] = Field(default_factory=list, max_length=100)


class PracticeInput(ConfirmInput):
    kind: Literal["written", "algorithm"]
    title: str = Field(min_length=1, max_length=200)
    difficulty: Literal["easy", "medium", "hard"]
    tags: list[Annotated[str, Field(min_length=1, max_length=40)]] = Field(max_length=10)
    outcome: Literal["passed", "failed", "unjudged"]
    practiced_on: date
    minutes: int = Field(ge=0, le=1440)
    reflection: str = Field(default="", max_length=4000)


class ContentSubscriptionInput(ConfirmInput):
    keyword: str = Field(min_length=1, max_length=100)
    interval_hours: Literal[6, 24, 168] = 24


class VoiceInput(ConfirmInput):
    recording_consent: Literal[True]
    interview_id: str = Field(min_length=36, max_length=36)
    audio_base64: str = Field(min_length=1, max_length=2800000)
    retention_days: int = Field(default=1, ge=1, le=7)


class TranscriptInput(ConfirmInput):
    text: str = Field(min_length=1, max_length=20000)


class OfflineTranscriptInput(ConfirmInput):
    transcription_consent: Literal[True]
    locale: Literal["en-US"]


class CalendarInput(ConfirmInput):
    event_ids: list[str] = Field(min_length=1, max_length=100)


class CalendarEventInput(ConfirmInput):
    title: str = Field(min_length=1, max_length=200)
    kind: Literal["interview", "deadline"]
    date: date
    time: str | None = Field(default=None, pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    timezone: str = Field(default="Asia/Shanghai", min_length=1, max_length=100)
    preparation_note: str = Field(default="", max_length=2000)


class ApplicationInput(ConfirmInput):
    application_id: str = Field(min_length=36, max_length=36)


class ApprovalInput(ApplicationInput):
    preview_hash: str = Field(min_length=64, max_length=64)


class SubmissionInput(ApplicationInput):
    approval_id: str = Field(min_length=36, max_length=36)


class OfferItem(ConfirmInput):
    name: str = Field(min_length=1, max_length=100)
    facts: dict[
        Literal["compensation", "location", "contract_terms"],
        Annotated[str, Field(max_length=2000)] | None,
    ] = Field(default_factory=dict)
    source_basis: str = Field(default="本人提供，未独立核实", min_length=1, max_length=1000)
    ratings: dict[
        Literal["compensation", "growth", "location", "stability", "work_life"], int | None
    ]


class OffersInput(ConfirmInput):
    offers: list[OfferItem] = Field(min_length=2, max_length=10)
    weights: dict[Literal["compensation", "growth", "location", "stability", "work_life"], int]


class ReviewInput(ConfirmInput):
    start: date
    end: date
    reflection: str = Field(min_length=1, max_length=4000)


class ManualReviewInput(ConfirmInput):
    note: str = Field(min_length=1, max_length=2000)


class OfferDecisionInput(ManualReviewInput):
    offer_index: int | None = Field(default=None, ge=0, le=9, strict=True)


def register_local_tools(app, session_dependency, identity):
    Db, Identity = Depends(session_dependency), Depends(identity)
    Version = Annotated[str, Header(pattern=r'^"[0-9]+"$')]

    @app.get("/api/v1/content-subscriptions")
    def content_subscriptions_list(db=Db, principal=Identity):
        return [
            growth.data(r)
            for r in db.scalars(growth.owner_query(principal, "content_subscription"))
        ]

    @app.post("/api/v1/content-subscriptions", status_code=201)
    def content_subscribe(
        body: ContentSubscriptionInput, request: Request, db=Db, principal=Identity
    ):
        from careerpilot import content_subscriptions

        value = content_subscriptions.create(
            db, principal, body.keyword, body.interval_hours, request.state.trace_id
        )
        db.commit()
        return value

    @app.delete("/api/v1/content-subscriptions/{key}")
    def content_cancel(key: str, if_match: Version, request: Request, db=Db, principal=Identity):
        from careerpilot import content_subscriptions

        value = content_subscriptions.cancel(
            db, principal, key, int(if_match.strip('"')), request.state.trace_id
        )
        db.commit()
        return value

    @app.get("/api/v1/practice-attempts")
    def practice_list(db=Db, principal=Identity):
        from careerpilot import practice_tracking

        return {
            "attempts": practice_tracking.listing(db, principal),
            "summary": practice_tracking.summary(db, principal),
        }

    @app.post("/api/v1/practice-attempts", status_code=201)
    def practice_create(body: PracticeInput, request: Request, db=Db, principal=Identity):
        from careerpilot import practice_tracking

        value = practice_tracking.save(
            db,
            principal,
            body.model_dump(mode="json", exclude={"confirmed"}),
            request.state.trace_id,
        )
        db.commit()
        return value

    @app.put("/api/v1/practice-attempts/{key}")
    def practice_update(
        key: str,
        body: PracticeInput,
        if_match: Version,
        request: Request,
        db=Db,
        principal=Identity,
    ):
        from careerpilot import practice_tracking

        value = practice_tracking.save(
            db,
            principal,
            body.model_dump(mode="json", exclude={"confirmed"}),
            request.state.trace_id,
            key,
            int(if_match.strip('"')),
        )
        db.commit()
        return value

    @app.delete("/api/v1/practice-attempts/{key}")
    def practice_delete(key: str, db=Db, principal=Identity):
        from careerpilot import practice_tracking

        value = practice_tracking.remove(db, principal, key)
        db.commit()
        return value

    @app.post("/api/v1/media-index", status_code=201)
    def store_media(body: MediaInput, request: Request, db=Db, principal=Identity):
        value = media.store(db, principal, body.model_dump(), request.state.trace_id)
        db.commit()
        return value

    @app.get("/api/v1/media-index")
    def media_list(q: str = Query(default="", max_length=200), db=Db, principal=Identity):
        return media.listing(db, principal, q)

    @app.delete("/api/v1/media-index/{key}")
    def media_delete(key: str, db=Db, principal=Identity):
        value = media.remove(db, principal, key)
        db.commit()
        return value

    @app.post("/api/v1/voice-records", status_code=201)
    def voice(body: VoiceInput, request: Request, db=Db, principal=Identity):
        value = personal_tools.recording(db, principal, body.model_dump(), request.state.trace_id)
        db.commit()
        return value

    @app.get("/api/v1/voice-records")
    def voice_list(db=Db, principal=Identity):
        return [
            personal_tools.voice_view(row)
            for row in db.scalars(growth.owner_query(principal, "voice_record"))
            if growth.data(row)["expires_at"] > int(time.time())
        ]

    @app.get("/api/v1/voice-records/{key}/audio")
    def audio(key: str, db=Db, principal=Identity):
        value = growth.data(growth.owned(db, principal, key, "voice_record"))
        if value["audio_deleted"] or value["expires_at"] <= int(time.time()):
            raise DomainError("NOT_FOUND", "录音已删除或到期", 404)
        return Response(
            base64.b64decode(value["audio_base64"]),
            media_type=value["mime_type"],
            headers={"Cache-Control": "no-store"},
        )

    @app.put("/api/v1/voice-records/{key}/transcript")
    def transcribe(key: str, body: TranscriptInput, request: Request, db=Db, principal=Identity):
        value = personal_tools.transcript(db, principal, key, body.text, request.state.trace_id)
        db.commit()
        return value

    @app.post("/api/v1/voice-records/{key}/draft")
    def offline_transcript(
        key: str,
        body: OfflineTranscriptInput,
        if_match: Version,
        request: Request,
        db=Db,
        principal=Identity,
    ):
        from careerpilot import voice_asr

        value = voice_asr.transcribe(
            db, principal, key, int(if_match.strip('"')), request.state.trace_id
        )
        db.commit()
        return value

    @app.delete("/api/v1/voice-records/{key}/audio")
    def delete_audio(key: str, request: Request, db=Db, principal=Identity):
        value = personal_tools.remove_audio(db, principal, key, request.state.trace_id)
        db.commit()
        return value

    @app.delete("/api/v1/voice-records/{key}")
    def delete_recording(key: str, db=Db, principal=Identity):
        value = personal_tools.remove_recording(db, principal, key)
        db.commit()
        return value

    @app.get("/api/v1/calendar-events")
    def calendar_events(db=Db, principal=Identity):
        return personal_tools.calendar_events(db, principal)

    @app.post("/api/v1/calendar-events", status_code=201)
    def calendar_create(body: CalendarEventInput, request: Request, db=Db, principal=Identity):
        value = personal_tools.save_calendar_event(
            db,
            principal,
            body.model_dump(mode="json", exclude={"confirmed"}),
            request.state.trace_id,
        )
        db.commit()
        return value

    @app.put("/api/v1/calendar-events/{key}")
    def calendar_update(
        key: str,
        body: CalendarEventInput,
        if_match: Version,
        request: Request,
        db=Db,
        principal=Identity,
    ):
        value = personal_tools.save_calendar_event(
            db,
            principal,
            body.model_dump(mode="json", exclude={"confirmed"}, exclude_unset=True),
            request.state.trace_id,
            key,
            int(if_match.strip('"')),
        )
        db.commit()
        return value

    @app.delete("/api/v1/calendar-events/{key}")
    def calendar_remove(key: str, if_match: Version, request: Request, db=Db, principal=Identity):
        value = personal_tools.remove_calendar_event(
            db,
            principal,
            key,
            int(if_match.strip('"')),
            request.state.trace_id,
        )
        db.commit()
        return value

    @app.post("/api/v1/calendar-syncs", status_code=201)
    def calendar_sync(body: CalendarInput, request: Request, db=Db, principal=Identity):
        value = personal_tools.calendar_sync(db, principal, body.event_ids, request.state.trace_id)
        db.commit()
        return value

    @app.get("/api/v1/calendar-syncs")
    def calendar_list(db=Db, principal=Identity):
        return [growth.data(r) for r in db.scalars(growth.owner_query(principal, "calendar_sync"))]

    @app.get("/api/v1/calendar-syncs/{key}/export")
    def calendar_export(key: str, db=Db, principal=Identity):
        return Response(
            personal_tools.ical(db, principal, key),
            media_type="text/calendar",
            headers={
                "Content-Disposition": 'attachment; filename="careerpilot.ics"',
                "Cache-Control": "no-store",
            },
        )

    @app.delete("/api/v1/calendar-syncs/{key}")
    def calendar_revoke(key: str, request: Request, db=Db, principal=Identity):
        value = personal_tools.revoke_calendar(db, principal, key, request.state.trace_id)
        db.commit()
        return value

    @app.delete("/api/v1/calendar-syncs/{key}/events/{event_id}")
    def calendar_remove_selected(
        key: str, event_id: str, if_match: Version, request: Request, db=Db, principal=Identity
    ):
        value = personal_tools.remove_synced_event(
            db,
            principal,
            key,
            event_id,
            int(if_match.strip('"')),
            request.state.trace_id,
        )
        db.commit()
        return value

    @app.post("/api/v1/application-lab/preview")
    def preview(body: ApplicationInput, db=Db, principal=Identity):
        value = application_lab.preview(db, principal, body.application_id)
        return {"preview": value, "preview_hash": growth.payload_hash(value)}

    @app.get("/api/v1/application-lab/runs")
    def simulation_runs(db=Db, principal=Identity):
        return [
            growth.data(row)
            for row in db.scalars(
                growth.owner_query(principal, "application_simulation").order_by(
                    growth.GrowthRecord.created_at.desc(), growth.GrowthRecord.id
                )
            )
        ]

    @app.post("/api/v1/application-lab/runs/{key}/review")
    def simulation_review(
        key: str,
        body: ManualReviewInput,
        if_match: Version,
        request: Request,
        db=Db,
        principal=Identity,
    ):
        value = application_lab.review_unknown(
            db, principal, key, body.note, int(if_match.strip('"')), request.state.trace_id
        )
        db.commit()
        return value

    @app.post("/api/v1/application-lab/approve")
    def approve(body: ApprovalInput, request: Request, db=Db, principal=Identity):
        value = application_lab.approve(
            db, principal, body.application_id, body.preview_hash, request.state.trace_id
        )
        db.commit()
        return value

    @app.post("/api/v1/application-lab/submit")
    def submit(body: SubmissionInput, request: Request, db=Db, principal=Identity):
        value = application_lab.submit(
            db, principal, body.application_id, body.approval_id, request.state.trace_id
        )
        db.commit()
        return value

    @app.delete("/api/v1/application-lab/approvals/{key}")
    def revoke_approval(key: str, request: Request, db=Db, principal=Identity):
        value = application_lab.revoke_approval(db, principal, key, request.state.trace_id)
        db.commit()
        return value

    @app.post("/api/v1/offer-comparisons", status_code=201)
    def offers(body: OffersInput, request: Request, db=Db, principal=Identity):
        if (
            not body.weights
            or any(v < 0 or v > 100 for v in body.weights.values())
            or any(
                v is not None and (v < 0 or v > 5)
                for offer in body.offers
                for v in offer.ratings.values()
            )
        ):
            raise DomainError("INVALID_RATING", "权重为 0–100，评分为 0–5 或未知", 422)
        value = application_lab.compare_offers(
            db, principal, body.model_dump(), request.state.trace_id
        )
        db.commit()
        return value

    @app.get("/api/v1/offer-comparisons")
    def offer_comparisons(db=Db, principal=Identity):
        return [
            growth.data(row)
            for row in db.scalars(growth.owner_query(principal, "offer_comparison"))
        ]

    @app.post("/api/v1/offer-comparisons/{key}/decision")
    def offer_decision(
        key: str,
        body: OfferDecisionInput,
        if_match: Version,
        request: Request,
        db=Db,
        principal=Identity,
    ):
        value = application_lab.decide_offer(
            db,
            principal,
            key,
            body.offer_index,
            body.note,
            int(if_match.strip('"')),
            request.state.trace_id,
        )
        db.commit()
        return value

    @app.post("/api/v1/quarter-reviews", status_code=201)
    def review(body: ReviewInput, request: Request, db=Db, principal=Identity):
        value = personal_tools.quarter_review(
            db,
            principal,
            body.start.isoformat(),
            body.end.isoformat(),
            body.reflection,
            request.state.trace_id,
        )
        db.commit()
        return value

    @app.get("/api/v1/quarter-reviews")
    def reviews(limit: int = Query(default=50, ge=1, le=100), db=Db, principal=Identity):
        return [
            growth.data(r)
            for r in db.scalars(
                growth.owner_query(principal, "quarter_review")
                .order_by(growth.GrowthRecord.created_at.desc(), growth.GrowthRecord.id)
                .limit(limit)
            )
        ]

    @app.get("/api/v1/quarter-reviews/{key}")
    def review_detail(key: str, db=Db, principal=Identity):
        return growth.data(growth.owned(db, principal, key, "quarter_review"))

    @app.post("/api/v1/agent-experiments", status_code=201)
    def experiment(body: ConfirmInput, request: Request, db=Db, principal=Identity):
        value = agent_experiments.evaluate(db, principal, request.state.trace_id)
        db.commit()
        return value

    @app.get("/api/v1/agent-experiments")
    def experiments(db=Db, principal=Identity):
        return [
            growth.data(r) for r in db.scalars(growth.owner_query(principal, "agent_experiment"))
        ]

    @app.post("/api/v1/agent-experiments/model-preview")
    def model_preview(body: ModelPreviewInput, principal=Identity):
        try:
            growth.scan_upload(
                json.dumps(body.model_dump(), allow_nan=False).encode(), "preview.json"
            )
            return model_comparison.preview(body.dataset, body.report, body.corrections)
        except (ValueError, TypeError, KeyError, OverflowError):
            raise DomainError(
                "MODEL_PREVIEW_INVALID", "数据集或冻结观察无效，请核对版本与完整步骤", 422
            ) from None

    @app.post("/api/v1/agent-experiments/expert-preview")
    def expert_preview(body: ExpertPreviewInput, principal=Identity):
        try:
            raw = json.dumps(body.model_dump(), allow_nan=False).encode()
            if len(raw) > 1048576:
                raise ValueError("Expert preview exceeds limit")
            growth.scan_upload(raw, "expert-preview.json")
            return expert_comparison.preview(body.cases, body.report, body.corrections)
        except (ValueError, TypeError, KeyError, OverflowError):
            raise DomainError(
                "MODEL_PREVIEW_INVALID", "数据集或冻结观察无效，请核对版本与完整步骤", 422
            ) from None

    @app.get("/api/v1/agent-experiments/{key}/replay")
    def experiment_replay(key: str, db=Db, principal=Identity):
        return agent_experiments.replay(db, principal, key)

    @app.post("/api/v1/agent-experiments/{key}/review")
    def experiment_review(
        key: str,
        body: ManualReviewInput,
        if_match: Version,
        request: Request,
        db=Db,
        principal=Identity,
    ):
        value = agent_experiments.review(
            db, principal, key, body.note, int(if_match.strip('"')), request.state.trace_id
        )
        db.commit()
        return value
