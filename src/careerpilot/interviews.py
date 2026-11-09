"""Local structured practice and user-confirmed review; never capability verification."""

import copy
import json
import re
from datetime import datetime
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo

from careerpilot import assessment, catalogue, growth, learning, project_evidence, resumes
from careerpilot.domain import DomainError
from careerpilot.persistence import IdempotencyRecord, new_id
from careerpilot.safety import payload_hash, scan_upload

RULE = "interview-rules.v1"
NOTICE = "本地题库与结构规则练习，非招聘面试或岗位胜任力认证；开放题技术正确性需人工核对。"


def body(item):
    return {k: v for k, v in item.items() if k not in {"id", "version", "created_at"}}


def remember(db, principal, key, request, kind, make):
    digest = payload_hash(request)
    previous = db.get(IdempotencyRecord, (principal.user_id, principal.tenant_id, key))
    if previous:
        if previous.request_hash != digest:
            raise DomainError("IDEMPOTENCY_CONFLICT", "幂等键已用于不同请求", 409)
        return growth.data(
            growth.owned(db, principal, json.loads(previous.response_json)["id"], kind)
        )
    result = make()
    db.add(
        IdempotencyRecord(
            user_id=principal.user_id,
            tenant_id=principal.tenant_id,
            key=key,
            request_hash=digest,
            response_json=json.dumps({"id": result["id"]}),
        )
    )
    return result


def questions(db, job, facts):
    skills = list(
        dict.fromkeys(s["skill_id"] for s in job["skills"] if s["skill_id"] in assessment.BANK)
    )
    key = skills[0] if skills else None
    refs = [
        {"type": "job", "id": job["id"], "version": job["version"], "quote": s["evidence"]["quote"]}
        for s in job["skills"]
        if s["skill_id"] == key
    ]
    if key:
        entry = catalogue.require_skill(db, key)
        question = assessment.BANK[key][0]
        basic = {
            "id": "foundation",
            "kind": "foundation",
            "prompt": f"{entry['name']} 基础：{question[0]}",
            "choices": question[1],
            "expected_choice": question[2],
            "explanation": question[3],
            "related_skills": [key],
            "references": refs
            + [
                {
                    "type": "catalogue_skill",
                    "id": key,
                    "version": entry["version"],
                    "url": entry["source_url"],
                }
            ],
            "bank_version": assessment.BANK_VERSION,
        }
    else:
        basic = {
            "id": "foundation",
            "kind": "open_foundation",
            "choices": [],
            "prompt": "请结合岗位原文说明你理解的基础知识要求，并指出还需核对哪些信息。",
            "related_skills": [],
            "references": [{"type": "job", "id": job["id"], "version": job["version"]}],
        }
    project_refs = [
        {
            "type": "resume_fact",
            "id": f["id"],
            "version": f["version"],
            "quote": f["text"],
            "source": f["source"],
        }
        for f in facts
    ]
    project_prompt = (
        f"围绕你选择的陈述『{facts[0]['text'][:180]}』，说明背景、你个人的行动、结果与验证依据。"
        if facts
        else (
            "你尚未选择项目陈述。请描述一项真实练习，说明个人行动和可核查材料；"
            "也可以明确暂无相关经历。"
        )
    )
    return [
        basic,
        {
            "id": "project",
            "kind": "project",
            "prompt": project_prompt,
            "choices": [],
            "related_skills": skills[:3],
            "references": project_refs or basic["references"],
        },
        {
            "id": "scenario",
            "kind": "scenario",
            "choices": [],
            "related_skills": skills[:3],
            "prompt": (
                f"针对『{job['title'] or '该岗位'}』的任务，如果结果与预期不一致，"
                "你会怎样定位原因、验证假设并说明局限？"
            ),
            "references": basic["references"],
        },
        {
            "id": "behavior",
            "kind": "behavior",
            "choices": [],
            "related_skills": skills[:3],
            "prompt": (
                "请用真实经历说明：进度受阻时你如何沟通、调整行动并复盘结果？没有经历时请明确说明。"
            ),
            "references": project_refs or basic["references"],
        },
    ]


def create(db, principal, args, key, trace):
    growth.lock_owner(db, principal)

    def make():
        version = resumes.owned_version(db, principal, args["resume_version_id"])
        resumes.valid_version_facts(db, principal, version)
        job = resumes.job_context(db, principal, args["job_id"])
        if version["job"] and (
            version["job"]["id"] != job["id"]
            or version["job"]["content_hash"] != job["content_hash"]
        ):
            raise DomainError("JOB_MISMATCH", "请使用该岗位当前定制版本或通用简历", 409)
        selected = [f for f in version["facts"] if f["id"] in args["fact_ids"]]
        if len(selected) != len(args["fact_ids"]) or any(
            f["kind"] not in {"project", "experience"} for f in selected
        ):
            raise DomainError(
                "INVALID_PROJECT_CONTEXT", "只能选择此简历版本中的项目或经历陈述", 422
            )
        return growth.save(
            db,
            principal,
            "interview_session",
            {
                "job": job,
                "resume_version_id": version["id"],
                "resume_sequence": version["sequence"],
                "resume_content_hash": version["content_hash"],
                "facts": selected,
                "questions": questions(db, job, selected),
                "answers": [],
                "status": "running",
                "completed_at": None,
                "rule_version": RULE,
                "notice": NOTICE,
            },
            trace,
        )

    return remember(
        db, principal, key, {"action": "interview.create", **args}, "interview_session", make
    )


def public(item):
    result = body(item)
    questions = result.pop("questions")
    result.pop("facts")
    result.update(
        id=item["id"],
        version=item["version"],
        created_at=item["created_at"],
        question_count=len(questions),
        answered_count=len(item["answers"]),
    )
    # Reference answers remain private until the completed evaluation is requested.
    visible = [
        {k: v for k, v in q.items() if k not in {"expected_choice", "explanation"}}
        for q in questions
    ]
    index = len(item["answers"])
    result["current_question"] = visible[index] if item["status"] == "running" else None
    result["answered_questions"] = visible[:index]
    return result


def answer(db, principal, session_id, args, expected, trace):
    growth.lock_owner(db, principal)
    row = growth.owned(db, principal, session_id, "interview_session")
    current = growth.data(row)
    if row.version != expected:
        raise DomainError("VERSION_CONFLICT", "面试会话已更新，请刷新后继续", 412)
    if current["status"] != "running":
        raise DomainError("INTERVIEW_FINISHED", "此会话已结束", 409)
    resumes.valid_version_facts(db, principal, current)
    q = current["questions"][len(current["answers"])]
    if q["id"] != args["question_id"]:
        raise DomainError("QUESTION_MISMATCH", "请回答当前问题", 409)
    if not args["skipped"]:
        if q["kind"] == "foundation" and args["choice"] is None:
            raise DomainError("CHOICE_REQUIRED", "请先选择基础题答案", 422)
        if q["kind"] != "foundation" and (not args["text"] or args["choice"] is not None):
            raise DomainError("ANSWER_REQUIRED", "请填写文字回答", 422)
    scan_upload(args["text"].encode(), "interview-answer.txt")
    result = body(current)
    result["answers"].append(
        {**args, "submitted_at": growth.utc_now(), "trust": "practice_self_report"}
    )
    if q["kind"] in {"project", "scenario"} and not args["skipped"]:
        followup = {
            "id": q["id"] + ".followup",
            "kind": "followup",
            "choices": [],
            "related_skills": q["related_skills"],
            "references": [
                {
                    "type": "interview_answer",
                    "session_id": session_id,
                    "question_id": q["id"],
                    "quote": args["text"][:160],
                }
            ],
            "prompt": (
                f"你刚才写道『{args['text'][:160]}』。请选择一个具体细节，"
                "说明验证步骤、可检查的证据及尚不确定之处。"
            ),
        }
        result["questions"].insert(len(result["answers"]), followup)
    if len(result["answers"]) == len(result["questions"]):
        result.update(status="completed", completed_at=growth.utc_now())
    return growth.save(db, principal, "interview_session", result, trace, row, expected)


def abandon(db, principal, session_id, expected, trace):
    growth.lock_owner(db, principal)
    row = growth.owned(db, principal, session_id, "interview_session")
    item = growth.data(row)
    if item["status"] != "running":
        raise DomainError("INTERVIEW_FINISHED", "此会话已结束", 409)
    return growth.save(
        db,
        principal,
        "interview_session",
        {**body(item), "status": "abandoned"},
        trace,
        row,
        expected,
    )


def turn_feedback(question, response):
    if response["skipped"]:
        return {
            "question_id": question["id"],
            "correctness": None,
            "completeness": None,
            "expression": None,
            "spans": [],
            "suggestion": "本题跳过，未作能力判断。",
        }
    if question["kind"] == "foundation":
        correct = response["choice"] == question["expected_choice"]
        return {
            "question_id": question["id"],
            "correctness": 4 if correct else 0,
            "completeness": None,
            "expression": None,
            "spans": [],
            "expected_choice": question["expected_choice"],
            "explanation": question["explanation"],
            "suggestion": "结合参考解释复查该知识点；本次练习不写入技能验证账本。",
        }
    text = response["text"]
    spans = []
    for label, pattern in [
        ("背景/目标", r"背景|任务|目标|场景"),
        ("行动", r"行动|步骤|实现|处理|我先|首先|然后"),
        ("结果", r"结果|输出|发现|完成|失败|效果"),
        ("验证/局限", r"验证|测试|证据|记录|日志|局限|未知|不确定"),
    ]:
        if match := re.search(pattern, text):
            spans.append(
                {"label": label, "start": match.start(), "end": match.end(), "quote": match.group()}
            )
    length = len(text.strip())
    expression = 1 if length < 40 else 4 if length <= 800 else 3
    return {
        "question_id": question["id"],
        "correctness": None,
        "completeness": len(spans),
        "expression": expression,
        "spans": spans,
        "suggestion": (
            "核对背景、个人行动、结果与验证依据，明确未知；结构标记和长度不证明技术正确或经历真实。"
        ),
    }


def evaluate(db, principal, session_id, expected, trace):
    growth.lock_owner(db, principal)
    row = growth.owned(db, principal, session_id, "interview_session")
    session = growth.data(row)
    if row.version != expected:
        raise DomainError("VERSION_CONFLICT", "会话版本已改变", 412)
    if session["status"] != "completed":
        raise DomainError("INTERVIEW_INCOMPLETE", "请先完成会话；跳过题目也会明确保留", 409)
    key = str(uuid5(NAMESPACE_URL, f"interview-evaluation:{row.id}:{row.version}:{RULE}"))
    existing = db.get(growth.GrowthRecord, key)
    if existing:
        return growth.data(existing)
    feedback = [
        turn_feedback(q, a) for q, a in zip(session["questions"], session["answers"], strict=True)
    ]
    dimensions = {}
    for dimension in ("correctness", "completeness", "expression"):
        scores = [f[dimension] for f in feedback if f[dimension] is not None]
        dimensions[dimension] = {
            "score": round(sum(scores) / len(scores), 2) if scores else None,
            "max_score": 4,
            "evaluated_questions": len(scores),
            "total_questions": len(feedback),
            "basis": "仅基础选择题客观评分"
            if dimension == "correctness"
            else "文字结构标记代理指标"
            if dimension == "completeness"
            else "回答长度代理指标",
            "status": "rule_checked" if scores else "unknown",
        }
    fact_ids = {f["id"] for f in session["facts"]}
    receipts = [
        e for e in project_evidence.current(db, principal) if fact_ids & set(e["resume_fact_ids"])
    ]
    dimensions["project_evidence"] = {
        "score": 4 if receipts else None,
        "max_score": 4,
        "status": "receipt_supported" if receipts else "unknown",
        "receipts": receipts,
        "basis": "仅受信项目验收回执；简历自述、材料链接和本次回答不作为已验证项目证据",
    }
    suggestions: list[dict] = []
    for question, result in zip(session["questions"], feedback, strict=True):
        if result["correctness"] == 4:
            continue
        for skill_id in question["related_skills"]:
            if not any(s["skill_id"] == skill_id for s in suggestions):
                suggestions.append(
                    {
                        "question_id": question["id"],
                        "skill_id": skill_id,
                        "minutes": 30,
                        "note": "本次练习的待复查知识点，需本人确认，不改变已验证技能状态",
                    }
                )
    return growth.save(
        db,
        principal,
        "interview_evaluation",
        {
            "session_id": row.id,
            "session_version": row.version,
            "session_hash": payload_hash(body(session)),
            "rule_version": RULE,
            "dimensions": dimensions,
            "feedback": feedback,
            "suggested_review": suggestions,
            "notice": NOTICE,
        },
        trace,
        record_id=key,
    )


def save_review(db, principal, evaluation_id, args, expected, trace):
    growth.lock_owner(db, principal)
    evaluation = growth.data(growth.owned(db, principal, evaluation_id, "interview_evaluation"))
    session = growth.data(
        growth.owned(db, principal, evaluation["session_id"], "interview_session")
    )
    allowed = {q["id"]: q["related_skills"] for q in session["questions"]}
    for decision in args["decisions"]:
        if decision["skill_id"] not in allowed.get(decision["question_id"], []):
            raise DomainError("UNRELATED_REVIEW", "复习项需对应本场题目关联的已收录知识点", 422)
        catalogue.require_skill(db, decision["skill_id"])
    scan_upload(growth.dump(args).encode(), "interview-review.json")
    key = str(uuid5(NAMESPACE_URL, f"interview-review:{evaluation_id}"))
    row = db.get(growth.GrowthRecord, key)
    return growth.save(
        db,
        principal,
        "interview_review",
        {
            **args,
            "evaluation_id": evaluation_id,
            "session_id": session["id"],
            "basis": "user_confirmed_practice_review",
            "user_archived": growth.data(row).get("user_archived", False) if row else False,
        },
        trace,
        row,
        expected,
        record_id=key,
    )


def save_self_review(db, principal, args, trace, review_id=None, expected=0, key=None):
    """Save the person's account of an interview without inventing an evaluation."""
    growth.lock_owner(db, principal)
    row = growth.owned(db, principal, review_id, "interview_review") if review_id else None
    if row and (
        row.version != expected or growth.data(row).get("basis") != "user_reported_interview"
    ):
        raise DomainError("VERSION_CONFLICT", "复盘已改变或不是本人面试自述，请重新读取。", 412)
    app_id = args.get("application_id")
    app = growth.data(growth.owned(db, principal, app_id, "job_application")) if app_id else None
    for item in args["decisions"]:
        catalogue.require_skill(db, item["skill_id"])
    scan_upload(growth.dump(args).encode(), "interview-self-review.json")
    payload = {
        "status": args["status"],
        "summary": args["summary"],
        "decisions": args["decisions"],
        "application_id": app_id,
        "job_id": app["job"]["id"] if app else None,
        "resume_version_id": app["resume_version_id"] if app else None,
        "evaluation_id": None,
        "session_id": None,
        "basis": "user_reported_interview",
        "user_archived": growth.data(row).get("user_archived", False) if row else False,
    }
    if row:
        return growth.save(db, principal, "interview_review", payload, trace, row, expected)
    if not key:
        raise DomainError("VALIDATION_ERROR", "创建复盘需要幂等键。", 422)
    return remember(
        db,
        principal,
        key,
        {"action": "interview.self_review", "body": args},
        "interview_review",
        lambda: growth.save(db, principal, "interview_review", payload, trace),
    )


def plan_preview(db, principal, review_id, plan_id, expected, key, trace):
    growth.lock_owner(db, principal)
    review = growth.data(growth.owned(db, principal, review_id, "interview_review"))
    if review["version"] != expected:
        raise DomainError("VERSION_CONFLICT", "复盘已更新，请刷新", 412)
    if review["status"] != "confirmed" or not review["decisions"]:
        raise DomainError("REVIEW_REQUIRED", "请先确认至少一项复习内容", 422)

    def make():
        row = growth.owned(db, principal, plan_id, "plan")
        current = growth.data(row)
        if current["status"] != "active":
            raise DomainError("ACTIVE_PLAN_REQUIRED", "请选择当前已发布计划", 409)
        if any(t.get("interview_review_id") == review_id for t in current["tasks"]):
            raise DomainError("REVIEW_ALREADY_PLANNED", "此复盘已加入该计划，不重复添加", 409)
        profile = learning.profile_context(db, principal, current)
        profile["weekly_minutes"] = learning.plan_budget(current, profile["weekly_minutes"])
        graph = catalogue.skill_map(db)
        candidate = copy.deepcopy(body(current))
        candidate["profile_version"] = profile["version"]
        candidate["catalogue_versions"] = {k: v["version"] for k, v in graph.items()}
        fixed = learning.protected_tasks(current)
        fixed_ids = {t["id"] for t in fixed}
        fixed += [
            copy.deepcopy(t)
            for t in current["tasks"]
            if t["status"] == "skipped" and t["id"] not in fixed_ids
        ]
        fixed_ids = {t["id"] for t in fixed}
        tasks = [copy.deepcopy(t) for t in current["tasks"] if t["id"] not in fixed_ids]
        all_tasks = fixed + tasks
        last = {t["skill_id"]: t["id"] for t in all_tasks if t["status"] != "skipped"}
        verified = {
            s["skill_id"] for s in learning.snapshots(db, principal) if s["status"] == "verified"
        }

        def add(skill_id, minutes, question_id, note):
            for parent in graph[skill_id]["requires"]:
                if parent not in verified and parent not in last:
                    add(parent, 30, question_id, "为复习项补充先修练习")
            dependencies = ([last[skill_id]] if skill_id in last else []) + [
                last[p] for p in graph[skill_id]["requires"] if p in last
            ]
            remaining = minutes
            while remaining:
                duration = min(remaining, profile["weekly_minutes"], 90)
                task = {
                    "id": new_id(),
                    "skill_id": skill_id,
                    "title": graph[skill_id]["name"] + " 面试复习",
                    "minutes": duration,
                    "requires": list(dict.fromkeys(dependencies)),
                    "locked": False,
                    "status": "pending",
                    "resources": ["resource-" + skill_id],
                    "outcome": "",
                    "definition_of_done": "结合参考材料复查该题，记录回答修订与仍不确定之处",
                    "verification": "practice_review_not_capability_verification",
                    "interview_review_id": review_id,
                    "interview_review_version": review["version"],
                    "interview_question_id": question_id,
                    "review_note": note,
                }
                tasks.append(task)
                last[skill_id] = task["id"]
                dependencies = [task["id"]]
                remaining -= duration

        for decision in review["decisions"]:
            add(
                decision["skill_id"],
                decision["minutes"],
                decision.get("question_id"),
                decision["note"],
            )
        today = datetime.now(ZoneInfo(profile["timezone"])).date().isoformat()
        candidate["tasks"] = learning.schedule(
            tasks, max(today, current["start_date"]), profile["weekly_minutes"], fixed
        )
        candidate["verified_skills"] = sorted(verified)
        error = None
        try:
            candidate["validation"] = learning.validate_plan(
                candidate, profile["weekly_minutes"], graph, fixed
            )
        except DomainError as exc:
            error = {"code": exc.code, "message": exc.message}
        before = {t["id"]: t for t in current["tasks"]}
        diff = {
            "added": [t for t in candidate["tasks"] if t["id"] not in before],
            "removed": [],
            "changed": [
                {"before": before[t["id"]], "after": t}
                for t in candidate["tasks"]
                if t["id"] in before and before[t["id"]] != t
            ],
        }
        return growth.save(
            db,
            principal,
            "plan_preview",
            {
                "plan_id": plan_id,
                "base_version": row.version,
                "reason": "InterviewReviewConfirmed",
                "status": "pending",
                "context_hash": learning.context_hash(db, principal),
                "candidate": candidate,
                "diff": diff,
                "validation_error": error,
                "interview_source": {
                    "review_id": review_id,
                    "review_version": review["version"],
                    "evaluation_id": review["evaluation_id"],
                },
            },
            trace,
        )

    return remember(
        db,
        principal,
        key,
        {
            "action": "interview.plan_preview",
            "review_id": review_id,
            "review_version": expected,
            "plan_id": plan_id,
        },
        "plan_preview",
        make,
    )
