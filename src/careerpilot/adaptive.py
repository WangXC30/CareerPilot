"""Bounded adaptive practice, with transparent local item ordering and appeal records."""

import json
import time

from careerpilot import assessment, catalogue, growth
from careerpilot.domain import DomainError
from careerpilot.gateway import reserve

# Difficulty is editorial, not a psychometrically calibrated proficiency score.
EXTRA = {
    "python": [
        (
            "a=[1]; b=a; b.append(2); len(a) 等于？",
            ["1", "2", "3"],
            1,
            "a 与 b 指向同一可变列表，因此长度变为 2。",
        ),
        (
            "生成器与列表的主要区别是？",
            ["生成器按需产生值", "生成器总是更快", "生成器不能迭代"],
            0,
            "生成器按需产生值，但性能取决于具体负载。",
        ),
        (
            "finally 中的 return 可能造成什么？",
            ["自动重试", "强制并发", "覆盖原先的返回或异常"],
            2,
            "finally 的 return 可以覆盖 try 中的返回值或待传播异常。",
        ),
    ],
    "sql": [
        (
            "COUNT(column) 是否计入 NULL？",
            ["不计入", "总是计入", "只计入 NULL"],
            0,
            "COUNT(column) 只统计非 NULL 值；COUNT(*) 统计行。",
        ),
        (
            "LEFT JOIN 后 WHERE 右表列=1 可能怎样？",
            ["保留全部左行", "排除未匹配行", "自动去重"],
            1,
            "未匹配行的右列为 NULL，WHERE 条件不为真，因而被排除。",
        ),
        (
            "两个事务并发读取后更新同一值，需重点防范？",
            ["图片压缩", "HTML 注入", "丢失更新"],
            2,
            "应结合事务隔离或乐观版本检查防止覆盖他人的更新。",
        ),
    ],
}
VERSION = "adaptive.editorial.v1"


def item(skill, index):
    questions = assessment.LEGACY_BANK[skill] + EXTRA.get(skill, [])
    q = questions[index]
    return {
        "skill_id": skill,
        "index": index,
        "prompt": q[0],
        "choices": q[1],
        "difficulty": "application" if index >= 3 else "foundation",
    }


def public(value):
    return {k: v for k, v in value.items() if k not in {"issued_at"}}


def start(db, principal, skills, trace):
    growth.lock_owner(db, principal)
    skills = list(dict.fromkeys(skills))
    for skill in skills:
        catalogue.require_skill(db, skill)
        if skill not in assessment.BANK:
            raise DomainError("ASSESSMENT_UNAVAILABLE", "技能题库尚不可用", 422)
    reserve(db, principal, "adaptive_sessions", 1, 100)
    result = growth.save(
        db,
        principal,
        "adaptive_exam",
        {
            "skills": skills,
            "bank_version": VERSION,
            "status": "pending",
            "current": item(skills[0], 1),
            "answers": [],
            "issued_at": int(time.time()),
            "review_flags": [],
            "appeal": None,
            "notice": "编辑分层的本地练习，不是标准化能力测量；不自动写入已验证能力或作弊认定。",
            "selection_rule": (
                "每技能三道不同题；首次基础题，答对优先应用题，答错回到基础；编辑难度未作统计标定。"
            ),
            "coverage": {"questions_per_skill": 3, "skill_count": len(skills)},
        },
        trace,
    )
    return public(result)


def answer(db, principal, key, choice, expected, trace):
    growth.lock_owner(db, principal)
    row = growth.owned(db, principal, key, "adaptive_exam")
    value = growth.data(row)
    if value["status"] != "pending":
        raise DomainError("ALREADY_SUBMITTED", "阶段练习已结束", 409)
    if value["bank_version"] != VERSION:
        raise DomainError("BANK_UNAVAILABLE", "题库版本已改变，请重新开始", 409)
    current = value["current"]
    skill, index = current["skill_id"], current["index"]
    q = (assessment.LEGACY_BANK[skill] + EXTRA.get(skill, []))[index]
    elapsed = max(0, int(time.time()) - value["issued_at"])
    correct = choice == q[2]
    answers = value["answers"] + [
        {
            **current,
            "choice": choice,
            "correct": correct,
            "explanation": q[3],
            "elapsed_seconds": elapsed,
        }
    ]
    seen = {a["index"] for a in answers if a["skill_id"] == skill}
    # Three distinct questions per skill, progressing to application only after a correct response.
    if len(seen) < 3:
        candidates = [3, 4, 5, 2, 0] if correct and skill in EXTRA else [0, 2, 3, 4, 5]
        next_index = next(
            i
            for i in candidates
            if i not in seen and i < len(assessment.LEGACY_BANK[skill] + EXTRA.get(skill, []))
        )
        current = item(skill, next_index)
    else:
        remaining = [s for s in value["skills"] if not any(a["skill_id"] == s for a in answers)]
        current = item(remaining[0], 1) if remaining else None
    fast = sum(a["elapsed_seconds"] < 2 for a in answers)
    flags = (
        [
            {
                "kind": "timing_review",
                "observations": fast,
                "answer_indices": [
                    i for i, answer in enumerate(answers) if answer["elapsed_seconds"] < 2
                ],
                "basis": "至少两题在服务器发题后不足两秒提交。",
                "notice": "可能是熟练作答或技术原因；仅供本人申请人工核对，不影响成绩。",
            }
        ]
        if fast >= 2
        else []
    )
    result = growth.save(
        db,
        principal,
        row.kind,
        {
            **{k: v for k, v in value.items() if k not in {"id", "version", "created_at"}},
            "answers": answers,
            "current": current,
            "status": "pending" if current else "completed",
            "issued_at": int(time.time()),
            "review_flags": flags,
            "summary": {"correct": sum(a["correct"] for a in answers), "answered": len(answers)},
        },
        trace,
        row,
        expected,
    )
    return public(result)


def appeal(db, principal, key, text, expected, trace):
    growth.lock_owner(db, principal)
    row = growth.owned(db, principal, key, "adaptive_exam")
    growth.scan_upload(text.encode(), "appeal.txt")
    result = growth.save(
        db,
        principal,
        row.kind,
        {
            **json.loads(row.payload_json),
            "appeal": {
                "text": text,
                "status": "pending_human_review",
                "submitted_at": growth.utc_now(),
            },
        },
        trace,
        row,
        expected,
    )
    return public(result)
