"""Versioned deterministic state policies shared by API, tools and workers."""

from careerpilot.domain import DomainError

RULE_VERSION = "states.v1"
TRANSITIONS = {
    "goal": {
        "draft": {"active", "abandoned"},
        "active": {"paused", "achieved", "abandoned"},
        "paused": {"active", "achieved", "abandoned"},
        "achieved": set(),
        "abandoned": set(),
    },
    "skill": {
        "unassessed": {"declared"},
        "declared": {"learning", "evidence_supported"},
        "learning": {"evidence_supported"},
        "evidence_supported": {"verified", "learning"},
        "verified": {"needs_reassessment"},
        "needs_reassessment": {"learning", "evidence_supported"},
    },
    "learning_task": {
        "pending": {"in_progress", "skipped", "expired"},
        "in_progress": {"pending_verification", "skipped", "expired"},
        "pending_verification": {"passed", "needs_review"},
        "needs_review": {"in_progress", "skipped"},
        "passed": set(),
        "skipped": set(),
        "expired": set(),
    },
    "application": {
        "draft": {"prepared", "withdrawn"},
        "prepared": {"awaiting_user_submission", "withdrawn"},
        "awaiting_user_submission": {"submitted", "withdrawn"},
        "submitted": {"assessment", "interviewing", "rejected", "withdrawn", "no_response"},
        "assessment": {"interviewing", "rejected", "withdrawn"},
        "interviewing": {"offer", "rejected", "withdrawn"},
        "offer": set(),
        "rejected": set(),
        "withdrawn": set(),
        "no_response": set(),
    },
    "agent_task": {
        "queued": {"running", "cancelled"},
        "running": {
            "queued",
            "awaiting_approval",
            "success",
            "partial_success",
            "failed",
            "cancelled",
        },
        "awaiting_approval": {"queued", "cancelled"},
        "failed": {"queued"},
        "success": set(),
        "partial_success": set(),
        "cancelled": set(),
    },
}


def validate_transition(kind: str, current: str, target: str, proof: dict | None = None):
    if target not in TRANSITIONS.get(kind, {}).get(current, set()):
        raise DomainError("INVALID_TRANSITION", "状态转换不被允许")
    proof = proof or {}
    if kind == "skill" and target == "verified":
        if not (
            proof.get("type") in {"assessment_result", "human_verified"}
            and proof.get("passed") is True
            and proof.get("reference")
            and proof.get("rubric_version")
            and proof.get("valid") is True
        ):
            raise DomainError("EVIDENCE_REQUIRED", "技能验证需要有效考核或人工证据")
    if kind == "application" and target == "submitted":
        if not (proof.get("confirmed") is True and proof.get("receipt")):
            raise DomainError("RECEIPT_REQUIRED", "投递状态需要确认的回执")


def authorize(principal, action: str, owner: tuple[str, str] | None = None):
    if action == "operations.read":
        if principal.role not in {"operator", "admin"}:
            raise DomainError("FORBIDDEN", "需要运营权限", 403)
    elif owner != (principal.tenant_id, principal.user_id):
        # Operators and administrators receive no implicit private-data bypass.
        raise DomainError("NOT_FOUND", "资源不存在", 404)
