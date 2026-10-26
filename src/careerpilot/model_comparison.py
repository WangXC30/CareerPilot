"""Bounded paired model workflows. Callers keep this evaluation in temporary storage."""

import hashlib
import json
import re
import statistics
import time
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from careerpilot.agent_experiments import PATTERN
from careerpilot.domain import DomainError
from careerpilot.gateway import Gateway, structured_envelope
from careerpilot.platform_models import ModelUsage
from careerpilot.safety import scan_upload

VERSION = "paired-risk-models.v1"
PROMPT = "risk-decision.v1"
REVIEW_PROMPT = "risk-review.v1"
SCHEMA = {
    "type": "object",
    "properties": {"risk": {"type": "boolean"}},
    "required": ["risk"],
    "additionalProperties": False,
}


class Example(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,60}$")
    group: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,60}$")
    text: str = Field(min_length=1, max_length=8000)
    expected: StrictBool


class Correction(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    repeat: int = Field(strict=True, ge=0, lt=10)
    case: int = Field(strict=True, ge=0, lt=100)
    arm: Literal["single", "reviewed"]
    prediction: StrictBool
    note: str = Field(min_length=1, max_length=1000)


class Dataset(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: str = Field(pattern=r"^[a-zA-Z0-9_.-]{1,80}$")
    source: Literal["synthetic", "owner_authorized"]
    rights_confirmed: Literal[True]
    examples: list[Example] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_examples(self):
        if len({item.id for item in self.examples}) != len(self.examples):
            raise ValueError("duplicate example ids")
        scan_upload(self.model_dump_json().encode(), "model-dataset.json")
        return self

    def digest(self):
        raw = json.dumps(self.model_dump(), sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(raw.encode()).hexdigest()


def summarize(dataset, rows, repeats):
    """Failed and unrun cases stay in the denominator; no complete-case filtering."""
    summaries = {}
    for arm in ("single", "reviewed"):
        entries = [row for row in rows if row["arm"] == arm]
        correct = sum(
            row["error"] is None and row["prediction"] == dataset.examples[row["case"]].expected
            for row in entries
        )
        errors = sum(row["error"] is not None for row in entries)
        complete = all(row["error"] is None for row in entries)
        latencies = [row["elapsed_ms"] for row in entries if row["elapsed_ms"] is not None]
        known_cost = sum(row["estimated_cost_micros"] for row in entries)
        summaries[arm] = {
            "correct": correct,
            "cases": repeats * len(dataset.examples),
            "accuracy": correct / (repeats * len(dataset.examples)),
            "errors": errors,
            "complete": complete,
            "mean_latency_ms": statistics.mean(latencies) if latencies else None,
            "latency_samples": len(latencies),
            "latency_complete": complete,
            "estimated_cost_micros": known_cost,
            "cost_complete": all(row["cost_complete"] for row in entries),
            "input_tokens": sum(row["input_tokens"] for row in entries),
            "output_tokens": sum(row["output_tokens"] for row in entries),
            "metered_attempts": sum(row["metered_attempts"] for row in entries),
        }
    gains = []
    for repeat in range(repeats):
        part = [row for row in rows if row["repeat"] == repeat]
        scores = {
            arm: sum(
                row["error"] is None and row["prediction"] == dataset.examples[row["case"]].expected
                for row in part
                if row["arm"] == arm
            )
            for arm in ("single", "reviewed")
        }
        gains.append((scores["reviewed"] - scores["single"]) / len(dataset.examples))
    groups: dict[str, dict] = {}
    for group in sorted({example.group for example in dataset.examples}):
        indices = {n for n, example in enumerate(dataset.examples) if example.group == group}
        groups[group] = {}
        for arm in ("single", "reviewed"):
            entries = [row for row in rows if row["arm"] == arm and row["case"] in indices]
            groups[group][arm] = {
                "cases": len(indices) * repeats,
                "correct": sum(
                    row["error"] is None
                    and row["prediction"] == dataset.examples[row["case"]].expected
                    for row in entries
                ),
                "errors": sum(row["error"] is not None for row in entries),
            }
    return {"arms": summaries, "repeat_quality_gains": gains, "groups": groups}


async def compare(
    dataset: Dataset,
    principal,
    primary: Gateway,
    reviewer: Gateway,
    *,
    repeats=3,
    budget_micros=1_000_000,
    allow_external=False,
):
    dataset = Dataset.model_validate(dataset.model_dump())
    if (
        type(repeats) is not int
        or not 2 <= repeats <= 10
        or type(budget_micros) is not int
        or not 1 <= budget_micros <= 1_000_000_000_000
    ):
        raise ValueError("invalid comparison limits")
    if primary.engine is not reviewer.engine:
        raise ValueError("both gateways must share the temporary usage store")
    for gateway in (primary, reviewer):
        if gateway.provider.external and not allow_external:
            raise DomainError("CONSENT_REQUIRED", "本次评测未授权外部模型调用", 403)
        if (
            type(gateway.micros_per_token) is not int
            or not 0 <= gateway.micros_per_token <= 1_000_000_000
            or not 0 < gateway.timeout <= 30
        ):
            raise ValueError("invalid gateway price or timeout")
        scan_upload(gateway.provider.model_id.encode(), "model-id.txt")
        if not re.fullmatch(r"[a-zA-Z0-9_.:/-]{1,120}", gateway.provider.model_id):
            raise ValueError("invalid model version identifier")
    experiment = str(uuid4())
    spent, stopped = 0, None
    rows: list[dict] = []
    for repeat in range(repeats):
        for number, example in enumerate(dataset.examples):
            # Alternate arm order for each pair and repeat to reduce ordering bias.
            for arm in (
                ("single", "reviewed") if (repeat + number) % 2 == 0 else ("reviewed", "single")
            ):
                row: dict = {
                    "repeat": repeat,
                    "case": number,
                    "arm": arm,
                    "prediction": None,
                    "error": "NOT_RUN",
                    "elapsed_ms": None,
                    "estimated_cost_micros": 0,
                    "cost_complete": False,
                    "steps": [],
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "metered_attempts": 0,
                }
                rows.append(row)
                if stopped:
                    continue
                started = time.perf_counter()
                row["error"], row["cost_complete"] = None, True
                payload: dict = {
                    "text": example.text,
                    "tool_terms": re.findall(PATTERN, example.text)[:20],
                    "task": (
                        "Determine whether this text requests risky recruitment conduct. "
                        "Account for negation and quotations."
                    ),
                }
                for stage, gateway, prompt in [("decision", primary, PROMPT)] + (
                    [("review", reviewer, REVIEW_PROMPT)] if arm == "reviewed" else []
                ):
                    if stage == "review":
                        payload["candidate_risk"] = row["prediction"]
                        payload["task"] = (
                            "Independently check the candidate against the text and tool terms; "
                            "return your final decision."
                        )
                    data = json.dumps(payload, ensure_ascii=False)
                    bound = (
                        gateway.reservation(structured_envelope(data, SCHEMA), 256)
                        * gateway.micros_per_token
                    )
                    if spent + bound > budget_micros:
                        stopped, row["error"] = "BUDGET_LIMIT", "BUDGET_LIMIT"
                        row["cost_complete"] = False
                        break
                    trace = str(uuid4())
                    error = None
                    try:
                        result = await gateway.generate(
                            principal,
                            experiment,
                            data,
                            SCHEMA,
                            prompt_version=prompt,
                            schema_version="risk-label.v1",
                            trace_id=trace,
                            max_tokens=256,
                            retries=0,
                        )
                        row["prediction"] = result["risk"]
                    except DomainError as failure:
                        error = failure.code
                    with Session(gateway.engine) as db:
                        usages = list(
                            db.scalars(select(ModelUsage).where(ModelUsage.trace_id == trace))
                        )
                        cost = sum(item.estimated_cost_micros for item in usages)
                        row["metered_attempts"] += len(usages)
                        row["input_tokens"] += sum(item.input_tokens for item in usages)
                        row["output_tokens"] += sum(item.output_tokens for item in usages)
                    spent += cost
                    row["estimated_cost_micros"] += cost
                    row["steps"].append({"stage": stage, "status": error or "success"})
                    if error:
                        row["prediction"], row["error"] = None, error
                        if error in {"MODEL_TIMEOUT", "MODEL_PROVIDER_ERROR", "MODEL_SCHEMA_ERROR"}:
                            row["cost_complete"] = False
                            stopped = "COST_UNKNOWN"
                        if error in {"CONSENT_REQUIRED", "CONSENT_REVOKED", "QUOTA_EXCEEDED"}:
                            row["cost_complete"] = False
                            stopped = error
                        break
                row["elapsed_ms"] = (
                    round((time.perf_counter() - started) * 1000, 3) if row["steps"] else None
                )
    return {
        "evaluator_version": VERSION,
        "dataset_version": dataset.version,
        "dataset_sha256": dataset.digest(),
        "source": dataset.source,
        "primary_model": primary.provider.model_id,
        "reviewer_model": reviewer.provider.model_id,
        "prompt_versions": [PROMPT, REVIEW_PROMPT],
        "repeats": repeats,
        "rows": rows,
        "summary": summarize(dataset, rows, repeats),
        "stopped": stopped,
        "enabled": False,
        "gate": "real_model_representativeness_and_independent_review_required",
        "cost_basis": "gateway token-price estimates, not supplier billing",
        "execution_scope": (
            "configured provider adapters; model provenance not independently verified"
        ),
        "task_scope": "binary recruitment-risk labeling only",
        "replay_scope": "rescore frozen observations without model calls",
        "budget_micros": budget_micros,
        "max_tokens_per_call": 256,
        "token_prices_micros": {
            "primary": primary.micros_per_token,
            "reviewer": reviewer.micros_per_token,
        },
        "timeouts_seconds": {"primary": primary.timeout, "reviewer": reviewer.timeout},
        "provider_retries": 0,
    }


def replay(dataset: Dataset, report):
    if (
        report.get("evaluator_version") != VERSION
        or report.get("dataset_sha256") != dataset.digest()
    ):
        raise ValueError("dataset or evaluator changed")
    repeats, rows = report.get("repeats"), report.get("rows")
    if type(repeats) is not int or not 2 <= repeats <= 10 or not isinstance(rows, list):
        raise ValueError("incomplete comparison")
    expected = {
        (r, n, arm)
        for r in range(repeats)
        for n in range(len(dataset.examples))
        for arm in ("single", "reviewed")
    }
    actual = set()
    for row in rows:
        if (
            not isinstance(row, dict)
            or type(row.get("repeat")) is not int
            or type(row.get("case")) is not int
            or type(row.get("arm")) is not str
            or row.get("arm") not in {"single", "reviewed"}
            or (row.get("error") is None and type(row.get("prediction")) is not bool)
            or type(row.get("estimated_cost_micros")) is not int
            or not 0 <= row["estimated_cost_micros"] <= 1_000_000_000_000
            or type(row.get("metered_attempts")) is not int
            or not 0 <= row["metered_attempts"] <= 2
            or type(row.get("input_tokens")) is not int
            or not 0 <= row["input_tokens"] <= 1_000_000
            or type(row.get("output_tokens")) is not int
            or not 0 <= row["output_tokens"] <= 1_000_000
            or type(row.get("steps")) is not list
            or not (row.get("error") is None or isinstance(row.get("error"), str))
            or type(row.get("cost_complete")) is not bool
            or (
                row.get("elapsed_ms") is not None
                and (
                    type(row["elapsed_ms"]) not in {float, int}
                    or not 0 <= row["elapsed_ms"] <= 120000
                )
            )
        ):
            raise ValueError("invalid frozen observation")
        key = (row["repeat"], row["case"], row["arm"])
        if key in actual:
            raise ValueError("duplicate frozen observation")
        actual.add(key)
    if actual != expected:
        raise ValueError("missing frozen observation")
    return {
        "identical": summarize(dataset, rows, repeats) == report.get("summary"),
        "enabled": False,
    }


def preview(dataset: Dataset, report, corrections=()):
    """An authenticated, transient rescore; imported traces are not verified evidence."""
    scan_upload(
        json.dumps(report, ensure_ascii=False, allow_nan=False).encode(), "model-trace.json"
    )
    result = replay(dataset, report)
    for key in ("primary_model", "reviewer_model"):
        if not isinstance(report.get(key), str) or not re.fullmatch(
            r"[a-zA-Z0-9_.:/-]{1,120}", report[key]
        ):
            raise ValueError("invalid model version identifier")
    codes = {
        "success",
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
    }
    rows = []
    for row in report["rows"]:
        if len(row["steps"]) > 2:
            raise ValueError("too many steps")
        steps = []
        for step in row["steps"]:
            if (
                not isinstance(step, dict)
                or step.get("stage") not in ("decision", "review")
                or not isinstance(step.get("status"), str)
                or step.get("status") not in codes
            ):
                raise ValueError("invalid structured step")
            steps.append({"stage": step["stage"], "status": step["status"]})
        expected_stages = ["decision", "review"] if row["arm"] == "reviewed" else ["decision"]
        stages = [step["stage"] for step in steps]
        if stages != expected_stages[: len(stages)]:
            raise ValueError("invalid step order")
        if row["error"] is None and (
            stages != expected_stages or any(step["status"] != "success" for step in steps)
        ):
            raise ValueError("incomplete successful workflow")
        if row["error"] == "NOT_RUN" and steps:
            raise ValueError("unrun workflow has steps")
        rows.append(
            {key: row[key] for key in ("repeat", "case", "arm", "elapsed_ms")}
            | {
                "status": (
                    "success"
                    if row["error"] is None
                    else row["error"]
                    if row["error"] in codes
                    else "unknown"
                ),
                "steps": steps,
                "prediction": row["prediction"],
            }
        )
    manual = corrected_quality(dataset, report, corrections)
    return {
        "identical": result["identical"],
        "enabled": False,
        "dataset_version": dataset.version,
        "dataset_sha256": dataset.digest(),
        "primary_model": report["primary_model"],
        "reviewer_model": report["reviewer_model"],
        "summary": summarize(dataset, report["rows"], report["repeats"]),
        "rows": rows,
        "human_assisted": manual,
    }


def corrected_quality(dataset: Dataset, report, corrections):
    """Human interventions are an overlay, never a change to frozen model evidence."""
    if len(corrections) > 100:
        raise ValueError("too many manual corrections")
    lookup = {(row["repeat"], row["case"], row["arm"]): row for row in report["rows"]}
    changes: dict[tuple, bool] = {}
    for item in corrections:
        correction = Correction.model_validate(item)
        scan_upload(correction.note.encode(), "manual-correction.txt")
        key = (correction.repeat, correction.case, correction.arm)
        if key not in lookup or key in changes:
            raise ValueError("unknown or repeated manual correction")
        changes[key] = correction.prediction
    arms: dict[str, dict] = {}
    for arm in ("single", "reviewed"):
        entries = [(key, row) for key, row in lookup.items() if row["arm"] == arm]
        correct = sum(
            (changes[key] == dataset.examples[row["case"]].expected)
            if key in changes
            else (
                row["error"] is None and row["prediction"] == dataset.examples[row["case"]].expected
            )
            for key, row in entries
        )
        arms[arm] = {
            "correct": correct,
            "cases": len(entries),
            "corrected": sum(key in changes for key, _ in entries),
        }
    return {
        "arms": arms,
        "correction_count": len(changes),
        "enabled": False,
        "basis": "human_interventions_only_not_model_quality",
    }
