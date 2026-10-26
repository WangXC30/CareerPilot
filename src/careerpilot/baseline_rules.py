"""Small deterministic regression baselines, not a complete JD/AI product engine."""

import re
from datetime import date

ALIASES = {
    "python": ["Python", "python"],
    "pytorch": ["PyTorch", "pytorch"],
    "sql": ["SQL", "sql"],
    "javascript": ["JavaScript", "JS"],
}


def extract_jd(text: str) -> dict:
    skills = []
    for skill, aliases in ALIASES.items():
        for alias in aliases:
            match = re.search(r"(?<![A-Za-z])" + re.escape(alias) + r"(?![A-Za-z])", text)
            if match:
                skills.append({"skill_id": skill, "evidence_span": match.group()})
                break
    graduation = re.search(r"(20\d{2})届", text)
    education = next(
        (
            value
            for word, value in [("博士", "doctor"), ("硕士", "master"), ("本科", "bachelor")]
            if word in text
        ),
        None,
    )
    return {
        "education": education,
        "graduation_cohort": int(graduation[1]) if graduation else None,
        "skills": skills,
        "state": "known" if education else "unknown",
    }


def canonical_skill(value: str) -> str:
    for skill, aliases in ALIASES.items():
        if value.lower() in {a.lower() for a in aliases}:
            return skill
    return value.lower()


def hard_match(actual, required) -> str:
    if actual is None or required is None:
        return "unknown"
    return "pass" if actual == required else "fail"


def job_validity(text: str, as_of: str) -> str:
    deadline = re.search(r"截止(?:日期)?[:：\s]*(20\d{2}-\d{2}-\d{2})", text)
    if not deadline:
        return "unknown"
    try:
        return (
            "possibly_expired"
            if date.fromisoformat(deadline[1]) < date.fromisoformat(as_of)
            else "unverified"
        )
    except ValueError:
        return "unknown"


def verified_evidence(records: list[dict]) -> str:
    if any(r.get("revoked") for r in records):
        return "needs_reassessment"
    if any(
        r.get("type") in {"assessment_result", "human_verified"}
        and r.get("passed") is True
        and r.get("rubric_version")
        and r.get("reference")
        and r.get("valid") is True
        for r in records
    ):
        return "verified"
    return "supported" if records else "unknown"


def plan_feasible(tasks: list[dict], budget: int) -> bool:
    known: set[str] = set()
    total = 0
    for task in tasks:
        if (
            task["id"] in known
            or task["minutes"] <= 0
            or not set(task.get("requires", [])) <= known
        ):
            return False
        total += task["minutes"]
        known.add(task["id"])
    return total <= budget
