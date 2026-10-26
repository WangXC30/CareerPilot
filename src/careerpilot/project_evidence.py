"""Trusted project receipt consumer; submitted materials alone never grant verification."""

import re
from datetime import datetime, timezone

from careerpilot import growth, learning

EVALUATOR = "careerpilot.project-evaluator.v1"
RUBRIC = "project-evidence.v1"


def current(db, principal):
    records = [
        e
        for e in learning.ledger(db, principal)
        if e["scope"] == "project" and e["kind"] == "assessment_result"
    ]
    # A newer failure, revocation, expiry or stale source must not resurrect an older pass.
    latest = {e["skill_id"]: e for e in records}
    result = []
    for skill_id, evidence in latest.items():
        try:
            expires = datetime.fromisoformat(evidence["expires_at"])
            valid_date = expires.tzinfo is not None and expires > datetime.now(timezone.utc)
        except ValueError:
            valid_date = False
        if (
            evidence["revoked"]
            or not valid_date
            or evidence["conclusion"] != "passed"
            or evidence["rubric_version"] != RUBRIC
        ):
            continue
        source = db.scalar(
            growth.owner_query(principal, "project_assessment").where(
                growth.GrowthRecord.id == evidence["source_id"]
            )
        )
        if source is None or source.version != evidence["source_version"]:
            continue
        receipt = growth.data(source)
        if (
            receipt.get("evaluator_version") != EVALUATOR
            or receipt.get("rubric_version") != RUBRIC
            or receipt.get("status") != "passed"
            or skill_id not in receipt.get("skill_ids", [])
        ):
            continue
        artifact = db.scalar(
            growth.owner_query(principal, "project_submission").where(
                growth.GrowthRecord.id == receipt.get("artifact_id")
            )
        )
        if artifact is None or artifact.version != receipt.get("artifact_version"):
            continue
        submission = growth.data(artifact)
        if (
            submission.get("status") != "submitted"
            or not re.fullmatch(r"[0-9a-f]{64}", submission.get("content_hash", ""))
            or submission["content_hash"] != receipt.get("artifact_hash")
            or skill_id not in submission.get("skill_ids", [])
        ):
            continue
        result.append(
            {
                "skill_id": skill_id,
                "evidence_id": evidence["id"],
                "assessment_id": source.id,
                "assessment_version": source.version,
                "artifact_id": artifact.id,
                "artifact_version": artifact.version,
                "artifact_hash": receipt["artifact_hash"],
                "expires_at": evidence["expires_at"],
                "resume_fact_ids": receipt.get("resume_fact_ids", []),
                "rubric_version": RUBRIC,
            }
        )
    # Facts are separate immutable confirmations: linking one must not change the
    # assessed receipt version or invalidate its evidence source binding.
    from careerpilot.resumes import revoke_id

    for receipt in result:
        linked = []
        for row in db.scalars(growth.owner_query(principal, "resume_fact")):
            fact = growth.data(row)
            source = fact.get("source", {})
            if source.get("narrative_id"):
                from careerpilot.project_workflow import narrative_source_status

                if narrative_source_status(db, principal, source) != "current":
                    continue
            if (
                source.get("type") == "project"
                and source.get("id") == receipt["artifact_id"]
                and source.get("artifact_hash") == receipt["artifact_hash"]
                and source.get("assessment_id") == receipt["assessment_id"]
                and source.get("assessment_version") == receipt["assessment_version"]
                and db.get(growth.GrowthRecord, revoke_id(principal, row.id)) is None
            ):
                linked.append(row.id)
        receipt["resume_fact_ids"] = sorted(set(receipt["resume_fact_ids"]) | set(linked))
    return result


def component(job, evidence):
    required = sorted({s["skill_id"] for s in job["skills"] if s["requirement"] == "required"})
    matched = [e for e in evidence if e["skill_id"] in required]
    verified = sorted({e["skill_id"] for e in matched})
    return {
        "ratio": len(verified) / len(required) if required and matched else None,
        "basis": {
            "project_ids": sorted({e["artifact_id"] for e in matched}),
            "skills": required,
            "verified": verified,
            "denominator": len(required),
            "receipts": matched,
            "scope": "project",
            "reason": (
                "仅计算当前有效的受信项目验收记录覆盖；缺少证据不代表已通过"
                if matched
                else "无当前有效的受信项目验收证据，保持未知；材料/简历/面试不补分"
            ),
        },
    }
