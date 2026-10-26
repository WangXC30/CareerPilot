"""Reproducible local single/dual-role baseline; no external agents or provider calls."""

import hashlib
import json
import re
import time

from careerpilot import growth
from careerpilot.domain import DomainError

CASES = [
    ("需先交费后入职", True),
    ("要求提供银行卡密码", True),
    ("保录用无需面试", True),
    ("Python 后端开发", False),
    ("不收取押金", False),
    ("无需缴纳保证金", False),
    ("不会索取验证码", False),
    ("请勿提供银行卡密码", False),
]
PATTERN = r"先交费|银行卡密码|保录用|押金|保证金|验证码"


def single(text):
    return bool(re.search(PATTERN, text))


def dual(text):
    findings = list(re.finditer(PATTERN, text))
    # Second local reviewer checks bounded negation context. This is not an LLM benchmark.
    return any(
        not re.search(r"不收取|无需缴纳|不会索取|请勿提供", text[max(0, m.start() - 8) : m.start()])
        for m in findings
    )


def dataset_digest():
    """Bind the ordered public fixtures and labels; never hash private model inputs."""
    raw = json.dumps(CASES, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def evaluate(db, principal, trace):
    results = []
    for name, implementation in [("single_rule_role", single), ("two_rule_roles", dual)]:
        started = time.perf_counter()
        verdicts = [implementation(text) == expected for text, expected in CASES]
        results.append(
            {
                "mode": name,
                "correct": sum(verdicts),
                "cases": len(CASES),
                "accuracy": sum(verdicts) / len(CASES),
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
                "provider_calls": 0,
                "model_tokens": 0,
                "predictions": [implementation(text) for text, _ in CASES],
            }
        )
    return growth.save(
        db,
        principal,
        "agent_experiment",
        {
            "dataset_version": "local-risk-fixtures.v1",
            "dataset_sha256": dataset_digest(),
            "implementation_version": "bounded-negation.v1",
            "results": results,
            "quality_gain": results[1]["accuracy"] - results[0]["accuracy"],
            "enabled": False,
            "gate": "insufficient_representative_examples",
            "gate_requirements": [
                "代表性冻结测试集",
                "实际模型与提示版本",
                "重复运行质量改善",
                "成本与延迟预算",
                "人工独立复核",
            ],
            "steps": [
                {"role": "extractor", "operation": "find risk terms"},
                {"role": "reviewer", "operation": "check bounded negation"},
                {"role": "evaluator", "operation": "compare frozen labels"},
            ],
            "notice": (
                "本地规则角色实验，8 条编辑样例不代表真实模型收益；"
                "多模型协作保持关闭。耗时仅为本次微基准。"
            ),
        },
        trace,
    )


def replay(db, principal, key):
    value = growth.data(growth.owned(db, principal, key, "agent_experiment"))
    if value["dataset_version"] != "local-risk-fixtures.v1":
        raise DomainError("REPLAY_UNAVAILABLE", "此实验的固定数据版本不可重放", 409)
    if value.get("dataset_sha256", dataset_digest()) != dataset_digest():
        raise DomainError("REPLAY_UNAVAILABLE", "固定数据内容已变化，不能重放此实验", 409)
    if value.get("implementation_version", "bounded-negation.v1") != "bounded-negation.v1":
        raise DomainError("REPLAY_UNAVAILABLE", "此规则实现版本不可重放", 409)
    entries = value.get("results")
    implementations = {"single_rule_role": single, "two_rule_roles": dual}
    if (
        not isinstance(entries, list)
        or len(entries) != len(implementations)
        or any(not isinstance(entry, dict) for entry in entries)
        or sorted(str(entry.get("mode")) for entry in entries) != sorted(implementations)
    ):
        raise DomainError("REPLAY_UNAVAILABLE", "实验角色记录不完整", 409)
    results = []
    for entry in entries:
        predictions = entry.get("predictions")
        if (
            not isinstance(predictions, list)
            or len(predictions) != len(CASES)
            or any(type(prediction) is not bool for prediction in predictions)
            or type(entry.get("correct")) is not int
        ):
            raise DomainError("REPLAY_UNAVAILABLE", "逐项预测记录不完整，请新建本地对照", 409)
        expected_predictions = [implementations[entry["mode"]](text) for text, _ in CASES]
        correct = sum(p == expected for p, (_, expected) in zip(predictions, CASES, strict=True))
        results.append(
            {
                "mode": entry["mode"],
                "correct": correct,
                "identical": predictions == expected_predictions and correct == entry["correct"],
            }
        )
    return {
        "results": results,
        "enabled": False,
        "dataset_version": value["dataset_version"],
        "notice": "仅重算冻结的本地规则预测，不调用外部工具，不重放申请操作。",
    }


def review(db, principal, key, note, expected, trace):
    growth.lock_owner(db, principal)
    row = growth.owned(db, principal, key, "agent_experiment")
    growth.scan_upload(note.encode(), "experiment-review.txt")
    return growth.save(
        db,
        principal,
        row.kind,
        {**json.loads(row.payload_json), "human_review": {"note": note}, "enabled": False},
        trace,
        row,
        expected,
    )
