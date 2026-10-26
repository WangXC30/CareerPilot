"""Private, bounded local tutoring from an actual answered practice item."""

import json
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import delete

from careerpilot import catalogue, growth, reporting
from careerpilot.domain import DomainError
from careerpilot.gateway import reserve
from careerpilot.growth_models import GrowthRevision

RULE = "tutor.local.v1"
NOTICE = (
    "本地规则教学，按所选概念/条件/反例结构化追问，不判断自由文本的技术正确性；"
    "不修改能力、成绩或学习计划。"
)


def quota(db, principal):
    day = datetime.now(ZoneInfo(reporting.user_timezone(db, principal))).date().isoformat()
    reserve(db, principal, "teaching:" + day, 1, 60)


def start(db, principal, exam_id, answer_index, trace):
    growth.lock_owner(db, principal)
    exam = growth.data(growth.owned(db, principal, exam_id, "adaptive_exam"))
    if answer_index >= len(exam["answers"]) or exam["answers"][answer_index]["correct"]:
        raise DomainError("WRONG_ANSWER_REQUIRED", "请选择本人练习中已提交的一道错题", 422)
    answer = exam["answers"][answer_index]
    skill = catalogue.require_skill(db, answer["skill_id"])
    quota(db, principal)
    return growth.save(
        db,
        principal,
        "teaching_session",
        {
            "exam_id": exam_id,
            "exam_version": exam["version"],
            "answer_index": answer_index,
            "item": answer,
            "bank_version": exam["bank_version"],
            "rule_version": RULE,
            "source_url": skill["source_url"],
            "catalogue_version": skill["version"],
            "level": 1,
            "turns": [
                {
                    "focus": "concept",
                    "attempt": "",
                    "question": "",
                    "level": 1,
                    "hint": prompt(answer, 1, "concept"),
                }
            ],
            "notice": NOTICE,
        },
        trace,
    )


def prompt(item, level, focus):
    choices = "；".join(item["choices"])
    focus_text = {
        "concept": "先区分题目中的概念，再说明你的选择依据。",
        "condition": "改变题目的一个前提后，你的结论还成立吗？说明哪些条件影响选择。",
        "counterexample": "构造一个不符合原结论的反例，再用自己的话解释差异。",
    }[focus]
    if level == 1:
        return f"{item['prompt']} 你当时选择了「{item['choices'][item['choice']]}」。{focus_text}"
    if level == 2:
        return f"对照这些选项：{choices}。列出选项间一个关键差异，并回到原题条件。{focus_text}"
    return item["explanation"] + " " + focus_text


def turn(db, principal, key, body, expected, trace):
    growth.lock_owner(db, principal)
    row = growth.owned(db, principal, key, "teaching_session")
    value = json.loads(row.payload_json)
    if len(value["turns"]) >= 12:
        raise DomainError("SESSION_LIMIT", "每个教学会话最多 12 轮，请先整理所学", 429)
    if row.version != expected:
        raise DomainError("VERSION_CONFLICT", "会话已改变，请刷新后继续", 412)
    growth.scan_upload((body["attempt"] + "\n" + body["question"]).encode(), "tutoring.txt")
    quota(db, principal)
    level = min(3, value["level"] + (1 if body["action"] == "hint" else 0))
    reply = {
        "focus": body["focus"],
        "attempt": body["attempt"],
        "question": body["question"],
        "level": level,
        "hint": prompt(value["item"], level, body["focus"]),
    }
    return growth.save(
        db,
        principal,
        row.kind,
        {
            **value,
            "level": level,
            "turns": [*value["turns"], reply],
        },
        trace,
        row,
        expected,
    )


def remove(db, principal, key):
    growth.lock_owner(db, principal)
    row = growth.owned(db, principal, key, "teaching_session")
    db.execute(delete(GrowthRevision).where(GrowthRevision.record_id == key))
    db.delete(row)
    return {"status": "deleted"}
