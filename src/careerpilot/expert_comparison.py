"""Paired generalist/specialist handoffs on reviewed, closed-world next-step cases."""

import json
import statistics
import time
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from careerpilot.domain import DomainError
from careerpilot.expert_protocol import Case, Decision, digest, score, validate_decision
from careerpilot.gateway import structured_envelope
from careerpilot.platform_models import ModelUsage
from careerpilot.safety import scan_upload

VERSION = "paired-specialist-next-step.v1"
CODES = {
    "NOT_RUN",
    "BUDGET_LIMIT",
    "MODEL_TIMEOUT",
    "MODEL_PROVIDER_ERROR",
    "MODEL_SCHEMA_ERROR",
    "CONSENT_REQUIRED",
    "CONSENT_REVOKED",
    "QUOTA_EXCEEDED",
    "MODEL_LIMIT",
    "INVALID_SCHEMA",
    "MODEL_GROUNDING_ERROR",
}


class Step(BaseModel):
    model_config = ConfigDict(extra="forbid")
    stage: str
    status: str
    decision: Decision | None = None


class Observation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    repeat: int = Field(strict=True, ge=0, lt=10)
    case: int = Field(strict=True, ge=0, lt=100)
    arm: str
    decision: Decision | None = None
    error: str | None = "NOT_RUN"
    elapsed_ms: float | None = Field(default=None, strict=True, ge=0, le=120000)
    estimated_cost_micros: int = Field(default=0, strict=True, ge=0, le=1000000000000)
    cost_complete: bool = Field(default=False, strict=True)
    input_tokens: int = Field(default=0, strict=True, ge=0, le=1000000)
    output_tokens: int = Field(default=0, strict=True, ge=0, le=1000000)
    metered_attempts: int = Field(default=0, strict=True, ge=0, le=2)
    steps: list[Step] = Field(default_factory=list, max_length=2)


class Correction(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    repeat: int = Field(strict=True, ge=0, lt=10)
    case: int = Field(strict=True, ge=0, lt=100)
    arm: Literal["single", "reviewed"]
    decision: Decision
    note: str = Field(min_length=1, max_length=1000)


def snapshot(cases):
    frozen = [Case.model_validate(case.model_dump()) for case in cases]
    if not 4 <= len(frozen) <= 100 or len({case.id for case in frozen}) != len(frozen):
        raise ValueError("Invalid specialist case set")
    if {case.specialist for case in frozen} != {"career", "learning", "job", "interview"}:
        raise ValueError("All four specialist roles are required")
    return frozen


def summary(cases, rows, repeats):
    arms = {}
    groups: dict[str, dict] = {}
    for arm in ("single", "reviewed"):
        part = [row for row in rows if row.arm == arm]
        correct = sum(
            row.error is None and score(cases[row.case], row.decision.model_dump()) for row in part
        )
        latencies = [row.elapsed_ms for row in part if row.elapsed_ms is not None]
        arms[arm] = {
            "correct": correct,
            "cases": len(cases) * repeats,
            "errors": sum(row.error is not None for row in part),
            "estimated_cost_micros": sum(row.estimated_cost_micros for row in part),
            "cost_complete": all(row.cost_complete for row in part),
            "input_tokens": sum(row.input_tokens for row in part),
            "output_tokens": sum(row.output_tokens for row in part),
            "metered_attempts": sum(row.metered_attempts for row in part),
            "mean_latency_ms": statistics.mean(latencies) if latencies else None,
        }
        for role in ("career", "learning", "job", "interview"):
            role_rows = [row for row in part if cases[row.case].specialist == role]
            groups.setdefault(role, {})[arm] = {
                "correct": sum(
                    row.error is None and score(cases[row.case], row.decision.model_dump())
                    for row in role_rows
                ),
                "cases": sum(case.specialist == role for case in cases) * repeats,
            }
    gains = []
    for repeat in range(repeats):
        counts = {
            arm: sum(
                row.error is None and score(cases[row.case], row.decision.model_dump())
                for row in rows
                if row.repeat == repeat and row.arm == arm
            )
            for arm in ("single", "reviewed")
        }
        gains.append((counts["reviewed"] - counts["single"]) / len(cases))
    return {"arms": arms, "groups": groups, "repeat_quality_gains": gains}


async def compare(
    cases,
    reviewed_sha256,
    principal,
    primary,
    reviewer,
    *,
    repeats=3,
    budget_micros=1000000,
    allow_external=False,
    progress=None,
    source="synthetic_draft",
):
    cases = snapshot(cases)
    if reviewed_sha256 != digest(cases):
        raise ValueError("Review does not match the frozen cases and labels")
    if type(repeats) is not int or not 2 <= repeats <= 10:
        raise ValueError("Invalid repeats")
    if type(budget_micros) is not int or not 1 <= budget_micros <= 10000000:
        raise ValueError("Invalid experiment budget")
    if source not in {"synthetic_draft", "operator_supplied_unverified"}:
        raise ValueError("Invalid case provenance")
    if primary.engine is not reviewer.engine:
        raise ValueError("Gateways must share the disposable usage store")
    for gateway in (primary, reviewer):
        if gateway.provider.external and not allow_external:
            raise DomainError("CONSENT_REQUIRED", "本次评测未授权外部模型调用", 403)
        if (
            type(gateway.micros_per_token) is not int
            or not 0 <= gateway.micros_per_token <= 1000000000
        ):
            raise ValueError("Invalid price")
        if not 0 < gateway.timeout <= 30:
            raise ValueError("Invalid timeout")
    rows, spent, stopped = [], 0, None
    experiment = str(uuid4())
    for repeat in range(repeats):
        for index, case in enumerate(cases):
            order = ("single", "reviewed") if (repeat + index) % 2 == 0 else ("reviewed", "single")
            for arm in order:
                row = Observation(repeat=repeat, case=index, arm=arm)
                rows.append(row)
                if stopped:
                    continue
                row.error, row.cost_complete = None, True
                started = time.perf_counter()
                candidate = None
                for stage, gateway in [("generalist", primary)] + (
                    [("specialist", reviewer)] if arm == "reviewed" else []
                ):
                    payload = case.handoff()
                    # Both arms receive the same evidence and source-manifest tool result.
                    payload["tool_observations"] = {
                        "source_versions": [
                            {"id": source.id, "version": source.version} for source in case.sources
                        ]
                    }
                    payload["agent_role"] = (
                        "generalist" if stage == "generalist" else case.specialist
                    )
                    if stage == "specialist":
                        assert candidate is not None
                        payload["candidate_decision"] = candidate.model_dump()
                        payload["review_task"] = (
                            "Independently check and correct the candidate "
                            "using only supplied evidence."
                        )
                    data, schema = json.dumps(payload, ensure_ascii=False), case.output_schema()
                    bound = (
                        gateway.reservation(structured_envelope(data, schema), 256)
                        * gateway.micros_per_token
                    )
                    if spent + bound > budget_micros:
                        stopped = row.error = "BUDGET_LIMIT"
                        break
                    trace, error = str(uuid4()), None
                    try:
                        value = await gateway.generate(
                            principal,
                            experiment,
                            data,
                            schema,
                            prompt_version=VERSION + "." + stage,
                            schema_version="specialist-decision.v1",
                            trace_id=trace,
                            max_tokens=256,
                            retries=0,
                        )
                        try:
                            candidate = validate_decision(case, value)
                        except ValueError:
                            error = "MODEL_GROUNDING_ERROR"
                    except DomainError as failure:
                        error = failure.code if failure.code in CODES else "MODEL_PROVIDER_ERROR"
                    with Session(gateway.engine) as db:
                        usages = list(
                            db.scalars(select(ModelUsage).where(ModelUsage.trace_id == trace))
                        )
                        cost = sum(item.estimated_cost_micros for item in usages)
                        row.input_tokens += sum(item.input_tokens for item in usages)
                        row.output_tokens += sum(item.output_tokens for item in usages)
                        row.metered_attempts += len(usages)
                    row.estimated_cost_micros += cost
                    spent += cost
                    row.steps.append(
                        Step(
                            stage=stage,
                            status=error or "success",
                            decision=candidate if error is None else None,
                        )
                    )
                    if error:
                        row.error = error
                        if error != "MODEL_GROUNDING_ERROR":
                            row.cost_complete = False
                            stopped = (
                                error
                                if error
                                in {"CONSENT_REQUIRED", "CONSENT_REVOKED", "QUOTA_EXCEEDED"}
                                else "COST_UNKNOWN"
                            )
                        break
                if row.error is None:
                    row.decision = candidate
                row.elapsed_ms = (
                    round((time.perf_counter() - started) * 1000, 3)
                    if row.metered_attempts
                    else None
                )
                if progress:
                    progress(len(rows), repeats * len(cases) * 2)
    return {
        "evaluator_version": VERSION,
        "dataset_sha256": digest(cases),
        "source": source,
        "review_claim": "operator-confirmed labels, not independently authenticated provenance",
        "primary_model": primary.provider.model_id,
        "reviewer_model": reviewer.provider.model_id,
        "repeats": repeats,
        "rows": [row.model_dump() for row in rows],
        "summary": summary(cases, rows, repeats),
        "stopped": stopped,
        "budget_micros": budget_micros,
        "enabled": False,
        "gate": "representative_samples_and_open_ended_expert_review_required",
        "task_scope": "closed-world next-step actions and supplied-version citations only",
    }


def validated_observations(cases, report):
    cases = snapshot(cases)
    if report.get("evaluator_version") != VERSION or report.get("dataset_sha256") != digest(cases):
        raise ValueError("Frozen cases or evaluator changed")
    repeats = report.get("repeats")
    if type(repeats) is not int or not 2 <= repeats <= 10:
        raise ValueError("Invalid repeats")
    raw_rows = report.get("rows")
    if not isinstance(raw_rows, list) or len(raw_rows) != repeats * len(cases) * 2:
        raise ValueError("Incomplete observations")
    rows = [Observation.model_validate(row) for row in raw_rows]
    expected = {
        (r, c, arm)
        for r in range(repeats)
        for c in range(len(cases))
        for arm in ("single", "reviewed")
    }
    actual = set()
    for row in rows:
        key = row.repeat, row.case, row.arm
        if key in actual or key not in expected or row.error not in CODES | {None}:
            raise ValueError("Incomplete or duplicated observations")
        actual.add(key)
        stages = ["generalist", "specialist"] if row.arm == "reviewed" else ["generalist"]
        if [step.stage for step in row.steps] != stages[: len(row.steps)]:
            raise ValueError("Invalid handoff order")
        if any(step.status not in CODES | {"success"} for step in row.steps):
            raise ValueError("Invalid step status")
        for step in row.steps:
            if step.decision is not None:
                if step.status != "success":
                    raise ValueError("Failed step cannot publish a decision")
                validate_decision(cases[row.case], step.decision.model_dump())
        if row.error is None:
            if (
                len(row.steps) != len(stages)
                or any(step.status != "success" for step in row.steps)
                or row.decision is None
            ):
                raise ValueError("Incomplete successful handoff")
            validate_decision(cases[row.case], row.decision.model_dump())
            if row.steps[-1].decision is not None and row.steps[-1].decision != row.decision:
                raise ValueError("Final decision differs from the final step")
        elif row.decision is not None or (row.error == "NOT_RUN" and row.steps):
            raise ValueError("Failed/unrun handoff cannot supply a decision")
        if row.error == "NOT_RUN" and (
            row.metered_attempts
            or row.input_tokens
            or row.output_tokens
            or row.estimated_cost_micros
            or row.cost_complete
            or row.elapsed_ms is not None
        ):
            raise ValueError("Unrun observation cannot claim usage or latency")
        if row.error == "BUDGET_LIMIT" and any(step.status != "success" for step in row.steps):
            raise ValueError("Budget stop cannot claim a failed model step")
        if row.error not in {None, "NOT_RUN", "BUDGET_LIMIT"} and (
            not row.steps
            or row.steps[-1].status != row.error
            or any(step.status != "success" for step in row.steps[:-1])
        ):
            raise ValueError("Failure does not match the handoff")
    if actual != expected:
        raise ValueError("Missing observations")
    return rows


def replay(cases, report):
    cases = snapshot(cases)
    rows = validated_observations(cases, report)
    return {
        "identical": summary(cases, rows, report["repeats"]) == report.get("summary"),
        "enabled": False,
    }


def preview(cases, report, corrections=()):
    """Project typed observations only; never execute providers or save human notes."""
    cases = snapshot(cases)
    rows = validated_observations(cases, report)
    actual = summary(cases, rows, report["repeats"])
    lookup = {(row.repeat, row.case, row.arm): row for row in rows}
    changes = {}
    if len(corrections) > 100:
        raise ValueError("Too many corrections")
    for value in corrections:
        correction = Correction.model_validate(value)
        scan_upload(correction.note.encode(), "expert-review-note.txt")
        key = correction.repeat, correction.case, correction.arm
        if key not in lookup or key in changes:
            raise ValueError("Unknown or duplicate correction")
        changes[key] = validate_decision(cases[correction.case], correction.decision.model_dump())
    human = {}
    for arm in ("single", "reviewed"):
        part = [(key, row) for key, row in lookup.items() if row.arm == arm]
        human[arm] = {
            "correct": sum(
                score(cases[row.case], changes[key].model_dump())
                if key in changes
                else row.error is None and score(cases[row.case], row.decision.model_dump())
                for key, row in part
            ),
            "cases": len(part),
            "corrected": sum(key in changes for key, _ in part),
        }
    return {
        "identical": actual == report.get("summary"),
        "enabled": False,
        "dataset_sha256": digest(cases),
        "summary": actual,
        "cases": [
            {
                "id": case.id,
                "specialist": case.specialist,
                "allowed_actions": list(case.output_schema()["properties"]["action"]["enum"]),
                "sources": [
                    {"id": source.id, "version": source.version} for source in case.sources
                ],
            }
            for case in cases
        ],
        "rows": [row.model_dump() for row in rows],
        "human_assisted": {"arms": human, "correction_count": len(changes), "enabled": False},
    }
