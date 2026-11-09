"""Action-specific, read-only checks; unknown information remains unknown."""

from careerpilot import catalogue, growth, learning, resumes
from careerpilot.domain import DomainError


def requirements(db, principal, action, job_id=None):
    profile_row = (
        growth.profile(db, principal) if action in {"explore", "plan", "match", "resume"} else None
    )
    profile = growth.data(profile_row) if profile_row else {}
    jobs = []
    if job_id and action in {"plan", "match", "interview"}:
        job = growth.data(growth.owned(db, principal, job_id, "job"))
        if job.get("status") not in {"closed", "merged", "possibly_expired"}:
            jobs = [job_id]
    active_plan = learning.active_plan(db, principal) if action in {"plan", "adjust_plan"} else None
    active_budget = growth.data(active_plan).get("budget_cap_minutes", 0) if active_plan else 0
    facts = (
        [growth.data(row) for row in db.scalars(growth.owner_query(principal, "resume_fact"))]
        if action in {"resume", "interview"}
        else []
    )
    valid_facts = [f for f in facts if resumes.fact_status(db, principal, f) == "current"]
    versions = (
        [growth.data(row) for row in db.scalars(growth.owner_query(principal, "resume_version"))]
        if action == "interview"
        else []
    )
    valid_resume = any(
        resumes.version_status(db, principal, version)["valid"] for version in versions
    )
    memories = (
        [growth.data(row) for row in db.scalars(growth.owner_query(principal, "memory"))]
        if action in {"explore", "resume", "interview"}
        else []
    )
    experiences = [
        item
        for item in memories
        if item.get("structured_experience")
        and item.get("trust") == "user_confirmed"
        and not item.get("deleted")
        and not item.get("superseded_by")
    ]
    goal_available = False
    applicable = set()
    if action == "plan":
        try:
            gap = learning.gap(db, principal, jobs)
            applicable = {item["skill_id"] for item in gap["skills"]}
            goal_available = bool(applicable)
        except DomainError as exc:
            if exc.code != "REQUIREMENTS_REQUIRED":
                raise
    elif action == "match" and jobs:
        applicable = {item["skill_id"] for item in job["skills"]}
    known_skills = False
    if action in {"plan", "match"}:
        now = growth.utc_now()
        known_skills = any(
            evidence["skill_id"] in applicable
            and not evidence["revoked"]
            and evidence["expires_at"] > now
            for evidence in learning.ledger(db, principal)
        )
        if not known_skills:
            skills = [
                skill for skill in catalogue.entries(db, "skill") if skill["id"] in applicable
            ]
            for row in db.scalars(growth.owner_query(principal, "memory")):
                memory = growth.data(row)
                if (
                    memory.get("trust") != "user_confirmed"
                    or memory.get("deleted")
                    or memory.get("revoked")
                    or memory.get("superseded_by")
                    or memory.get("status") in {"revoked", "deleted", "user_archived"}
                ):
                    continue
                text = memory.get("text", "").casefold()
                if any(alias.casefold() in text for skill in skills for alias in skill["aliases"]):
                    known_skills = True
                    break
    checks = {
        "explore": [("stage_or_experience", bool(profile or experiences), False)],
        "plan": [
            ("goal_or_job_requirements", goal_available, True),
            ("confirmed_profile", bool(profile), False),
            (
                "weekly_budget",
                bool(profile.get("weekly_minutes", 0) > 0 if profile else active_budget > 0),
                True,
            ),
            ("skill_start", known_skills, False),
        ],
        "match": [
            ("selected_job", bool(jobs), True),
            ("constraints", bool(profile.get("preferred_cities")), False),
            ("ability_evidence", known_skills, False),
        ],
        "resume": [
            ("current_facts", bool(valid_facts), True),
            ("education_or_experience", bool(profile or experiences), False),
        ],
        "interview": [
            ("selected_job", bool(jobs), True),
            (
                "resume_or_project",
                bool(
                    valid_resume
                    or any(
                        item.get("experience", {}).get("kind") == "project" for item in experiences
                    )
                ),
                False,
            ),
            ("preparation_focus", False, False),
        ],
        "adjust_plan": [
            ("active_plan", bool(active_plan), True),
            ("changed_constraints", False, False),
        ],
    }[action]
    return {
        "action": action,
        "items": [
            {"field": name, "state": "available" if present else "unknown", "required": required}
            for name, present, required in checks
        ],
        "profile_version": profile.get("version", 0),
        "note": "未知不等于没有；按当前操作询问，最终写入和业务校验仍由原服务执行。",
    }
