"""Private practice projects and trusted, lease-fenced evaluation receipts.

No function in this module executes submitted code. Only the operator runner can
complete evaluations; there is deliberately no HTTP endpoint for writing a verdict.
"""

import hashlib
import json
import time
from datetime import datetime, timedelta, timezone
from uuid import NAMESPACE_URL, uuid4, uuid5

from sqlalchemy import select

from careerpilot import growth, learning, project_evidence
from careerpilot.domain import DomainError, Principal
from careerpilot.safety import scan_upload
from careerpilot.v1_models import SkillEvidence

TEMPLATE = {
    "id": "python-record-summary.v1",
    "title": "Python 岗位数据汇总练习",
    "skill_ids": ["python"],
    "requires": [],
    "estimated_minutes": 180,
    "test_version": "record-summary.tests.v1",
    "instructions": (
        "实现 solve(records)，records 是含 city 和 salary 的字典列表。"
        "仅统计 city 为非空字符串、salary 为非负整数（不含布尔值）的记录；"
        "返回 {city: {count: 数量, total_salary: 工资之和}}。空输入返回 {}。"
        "提交单个 Python 源文件，使用标准库，无网络和第三方依赖。"
    ),
    "starter": "def solve(records):\n    return {}\n",
    "milestones": [
        {"id": "design", "title": "梳理输入与边界", "minutes": 30, "verification": "self_report"},
        {
            "id": "implementation",
            "title": "实现与隔离测试",
            "minutes": 120,
            "verification": "sandbox_tests",
        },
        {
            "id": "reflection",
            "title": "复盘与成果陈述",
            "minutes": 30,
            "verification": "self_report",
        },
    ],
    "scope_notice": "有限测试仅验证此练习的 Python 数据处理行为，不证明岗位整体胜任或项目原创性。",
}

# Expected values stay with the trusted runner, outside the user-code container.
CASES = [
    ("empty", [], {}),
    (
        "aggregate",
        [{"city": "上海", "salary": 10}, {"city": "上海", "salary": 20}],
        {"上海": {"count": 2, "total_salary": 30}},
    ),
    (
        "cities",
        [{"city": "北京", "salary": 0}, {"city": "上海", "salary": 7}],
        {"北京": {"count": 1, "total_salary": 0}, "上海": {"count": 1, "total_salary": 7}},
    ),
    (
        "invalid",
        [
            {"city": "", "salary": 2},
            {"city": "上海", "salary": -1},
            {"city": "上海", "salary": True},
            {"city": 1, "salary": 2},
            {"city": "上海", "salary": "3"},
            {},
            {"city": "上海", "salary": 3},
        ],
        {"上海": {"count": 1, "total_salary": 3}},
    ),
    (
        "unicode",
        [{"city": "深圳", "salary": 999999999999}, {"city": "深圳", "salary": 1}],
        {"深圳": {"count": 2, "total_salary": 1000000000000}},
    ),
]


def payload(row):
    return json.loads(row.payload_json)


def once(db, principal, kind, key, args, trace, make):
    growth.lock_owner(db, principal)
    record_id = str(uuid5(NAMESPACE_URL, f"{principal.tenant_id}:{principal.user_id}:{kind}:{key}"))
    try:
        digest = growth.payload_hash(args)
    except UnicodeEncodeError:
        raise DomainError("INVALID_TEXT", "请使用有效 UTF-8 文本", 422) from None
    row = db.scalar(growth.owner_query(principal, kind).where(growth.GrowthRecord.id == record_id))
    if row:
        if payload(row)["request_hash"] != digest:
            raise DomainError("IDEMPOTENCY_CONFLICT", "幂等键已用于不同内容", 409)
        return growth.data(row)
    return growth.save(
        db, principal, kind, {**make(), "request_hash": digest}, trace, record_id=record_id
    )


def recommend(db, principal, job_id):
    job = growth.data(growth.owned(db, principal, job_id, "job"))
    if job.get("status") in {"closed", "merged"}:
        raise DomainError("JOB_UNAVAILABLE", "请选择当前有效岗位", 409)
    skills = {s["skill_id"] for s in job["skills"]}
    profile = learning.profile_context(db, principal)
    return (
        [
            {
                **TEMPLATE,
                "job_id": job_id,
                "job_version": job["version"],
                "weekly_budget_minutes": profile["weekly_minutes"],
                "estimated_weeks": (180 + profile["weekly_minutes"] - 1)
                // profile["weekly_minutes"],
                "scheduling": "项目单独估算，不会自动占用或修改学习计划",
            }
        ]
        if "python" in skills
        else []
    )


def create(db, principal, args, key, trace):
    def make():
        options = recommend(db, principal, args["job_id"])
        if not options or args["template_id"] != TEMPLATE["id"]:
            raise DomainError("PROJECT_UNAVAILABLE", "当前岗位没有该版本练习模板", 422)
        return {
            "job_id": args["job_id"],
            "job_version": options[0]["job_version"],
            "template": TEMPLATE,
            "visibility": "private",
            "latest_submission_id": None,
            "milestones": {"design": "pending", "reflection": "pending"},
        }

    return once(db, principal, "practice_project", key, args, trace, make)


def submit(db, principal, project_id, code, version, key, trace):
    args = {"project_id": project_id, "code": code, "project_version": version}

    def make():
        row = growth.owned(db, principal, project_id, "practice_project")
        project = payload(row)
        if project["template"]["id"] != TEMPLATE["id"]:
            raise DomainError("TEMPLATE_UNAVAILABLE", "模板版本不可用", 409)
        # Scan as plain text; the application never executes this source file.
        scan_upload(code.encode("utf-8"), "solution.py.txt")
        if not code.strip() or len(code.encode("utf-8")) > 32768:
            raise DomainError("INVALID_CODE", "源文件必须为 1–32768 字节", 422)
        active = [
            r
            for r in db.scalars(growth.owner_query(principal, "project_assessment"))
            if payload(r)["status"] in {"queued", "running"}
        ]
        if len(active) >= 3:
            raise DomainError("PROJECT_QUEUE_FULL", "最多同时保留三项待评测提交", 429)
        # Deterministic identity matches once() without persisting a partially-created artifact.
        submission_id = str(
            uuid5(
                NAMESPACE_URL, f"{principal.tenant_id}:{principal.user_id}:project_submission:{key}"
            )
        )
        project["latest_submission_id"] = submission_id
        growth.save(db, principal, "practice_project", project, trace, row, version)
        return {
            "project_id": project_id,
            "code": code,
            "status": "submitted",
            "content_hash": hashlib.sha256(code.encode()).hexdigest(),
            "skill_ids": TEMPLATE["skill_ids"],
            "test_version": TEMPLATE["test_version"],
        }

    artifact = once(db, principal, "project_submission", key, args, trace, make)
    evaluation_id = str(uuid5(NAMESPACE_URL, "project-assessment:" + artifact["id"]))
    if db.get(growth.GrowthRecord, evaluation_id) is None:
        growth.save(
            db,
            principal,
            "project_assessment",
            {
                "artifact_id": artifact["id"],
                "artifact_version": artifact["version"],
                "artifact_hash": artifact["content_hash"],
                "project_id": project_id,
                "skill_ids": artifact["skill_ids"],
                "test_version": artifact["test_version"],
                "evaluator_version": project_evidence.EVALUATOR,
                "rubric_version": project_evidence.RUBRIC,
                "status": "queued",
                "attempt": 0,
                "lease_until": 0,
                "resume_fact_ids": [],
                "results": [],
            },
            trace,
            record_id=evaluation_id,
        )
    return {**artifact, "assessment_id": evaluation_id}


def public_assessment(row):
    return {k: v for k, v in growth.data(row).items() if k != "lease_token_hash"}


def withdraw(db, principal, artifact_id, version, trace):
    growth.lock_owner(db, principal)
    row = growth.owned(db, principal, artifact_id, "project_submission")
    item = payload(row)
    item["status"] = "withdrawn"
    return growth.save(db, principal, "project_submission", item, trace, row, version)


def claim(db, now=None):
    """Operator-only. Expired leases can be retried, fenced by a fresh token/version."""
    now = time.time() if now is None else now
    for row in db.scalars(
        select(growth.GrowthRecord)
        .where(growth.GrowthRecord.kind == "project_assessment")
        .order_by(growth.GrowthRecord.created_at, growth.GrowthRecord.id)
    ):
        item = payload(row)
        if item["status"] != "queued" and not (
            item["status"] == "running" and item["lease_until"] < now
        ):
            continue
        principal = Principal(row.user_id, row.tenant_id, "student")
        growth.lock_owner(db, principal)
        db.refresh(row)
        item = payload(row)
        if item["status"] != "queued" and not (
            item["status"] == "running" and item["lease_until"] < now
        ):
            continue
        artifact = growth.owned(db, principal, item["artifact_id"], "project_submission")
        project = growth.owned(db, principal, item["project_id"], "practice_project")
        if (
            payload(artifact)["status"] != "submitted"
            or payload(project)["latest_submission_id"] != artifact.id
        ):
            item["status"] = "cancelled"
            growth.save(
                db, principal, "project_assessment", item, "project-runner", row, row.version
            )
            continue
        if item["attempt"] >= 3:
            item["status"] = "system_error"
            growth.save(
                db, principal, "project_assessment", item, "project-runner", row, row.version
            )
            continue
        token = str(uuid4())
        item.update(
            status="running",
            lease_token_hash=hashlib.sha256(token.encode()).hexdigest(),
            lease_until=now + 120,
            attempt=item["attempt"] + 1,
        )
        receipt = growth.save(
            db, principal, "project_assessment", item, "project-runner", row, row.version
        )
        return {"receipt": {**receipt, "lease_token": token}, "code": payload(artifact)["code"]}
    return None


def complete(db, assessment_id, lease_token, result, now=None):
    now = time.time() if now is None else now
    row = db.get(growth.GrowthRecord, assessment_id)
    if row is None or row.kind != "project_assessment":
        raise DomainError("NOT_FOUND", "评测不存在", 404)
    principal = Principal(row.user_id, row.tenant_id, "student")
    growth.lock_owner(db, principal)
    db.refresh(row)
    item = payload(row)
    if (
        item["status"] != "running"
        or item["lease_token_hash"] != hashlib.sha256(lease_token.encode()).hexdigest()
        or item["lease_until"] < now
    ):
        raise DomainError("STALE_EVALUATOR", "评测租约已失效", 409)
    artifact = growth.owned(db, principal, item["artifact_id"], "project_submission")
    project = growth.owned(db, principal, item["project_id"], "practice_project")
    if (
        artifact.version != item["artifact_version"]
        or payload(artifact)["status"] != "submitted"
        or payload(project)["latest_submission_id"] != artifact.id
    ):
        item.update(status="cancelled", lease_until=0)
        return growth.save(
            db, principal, "project_assessment", item, "project-runner", row, row.version
        )
    status = result["status"]
    if status not in {"passed", "user_failed", "system_error", "timeout", "manual_review"}:
        raise DomainError("INVALID_VERDICT", "评测结果无效", 422)
    results = result.get("results", [])
    if status in {"passed", "user_failed"}:
        if (
            [r.get("id") for r in results] != [c[0] for c in CASES]
            or any(type(r.get("passed")) is not bool for r in results)
            or (status == "passed") != all(r["passed"] for r in results)
            or result.get("test_version") != item["test_version"]
            or not result.get("image_id", "").startswith("sha256:")
        ):
            raise DomainError("INVALID_VERDICT", "缺少完整测试或镜像指纹", 422)
    item.update(
        status=status,
        results=results,
        image_id=result.get("image_id"),
        test_version=result.get("test_version", item["test_version"]),
        completed_at=growth.utc_now(),
        lease_until=0,
    )
    receipt = growth.save(
        db, principal, "project_assessment", item, "project-runner", row, row.version
    )
    if status in {"passed", "user_failed"}:
        for skill_id in item["skill_ids"]:
            evidence = SkillEvidence(
                user_id=principal.user_id,
                tenant_id=principal.tenant_id,
                skill_id=skill_id,
                kind="assessment_result",
                scope="project",
                source_id=row.id,
                source_version=receipt["version"],
                conclusion="passed" if status == "passed" else "failed",
                rubric_version=project_evidence.RUBRIC,
                expires_at=(datetime.now(timezone.utc) + timedelta(days=90)).isoformat(),
            )
            db.add(evidence)
    if status == "passed":
        from careerpilot import analytics

        analytics.record(db, principal, "project_completed", "project-runner", trusted=True)
        analytics.verified_action(db, principal, set(item["skill_ids"]), "project-runner")
    return receipt


def statement(db, principal, artifact_id):
    artifact = growth.data(growth.owned(db, principal, artifact_id, "project_submission"))
    receipts = [
        r for r in project_evidence.current(db, principal) if r["artifact_id"] == artifact_id
    ]
    if not receipts:
        raise DomainError("PROJECT_RECEIPT_REQUIRED", "该提交没有当前有效的项目通过回执", 409)
    return {
        "text": "完成 Python 岗位数据汇总练习，通过 record-summary.tests.v1 的有限测试。",
        "artifact_id": artifact_id,
        "artifact_hash": artifact["content_hash"],
        "assessment_id": receipts[0]["assessment_id"],
        "assessment_version": receipts[0]["assessment_version"],
        "unknown": ["个人贡献比例", "真实业务收益", "性能指标", "原创性"],
        "notice": "确认后保存为简历陈述；未知指标不会补写。",
    }


def confirm_statement(db, principal, artifact_id, key, trace):
    def make():
        draft = statement(db, principal, artifact_id)
        return {
            "kind": "project",
            "text": draft["text"],
            "source": {
                "type": "project",
                "id": artifact_id,
                "version": 1,
                "assessment_id": draft["assessment_id"],
                "assessment_version": draft["assessment_version"],
                "artifact_hash": draft["artifact_hash"],
            },
            "verification": "limited_project_tests_user_confirmed",
            "confirmed_at": growth.utc_now(),
        }

    return once(db, principal, "resume_fact", key, {"artifact_id": artifact_id}, trace, make)
