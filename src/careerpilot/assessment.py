"""Versioned, objective foundation checks. Learning completion never grants verification."""

import json

from careerpilot import catalogue, growth, learning
from careerpilot.domain import DomainError

BANK_VERSION = "foundation.v1"
# Locally authored questions; explanations are editorial, linked to the skill's official material.
BANK = {
    "python": [
        (
            "哪个表达式创建可变列表？",
            ["(1, 2)", "[1, 2]", "{1, 2}"],
            1,
            "方括号创建列表；元组不可变。",
        ),
        ("哪个语句捕获异常？", ["except", "repeat", "package"], 0, "try/except 处理异常。"),
        ("len({'a': 1, 'b': 2}) 的值是？", ["1", "4", "2"], 2, "字典长度是键的数量。"),
    ],
    "sql": [
        ("哪个子句过滤行？", ["ORDER BY", "WHERE", "SELECT"], 1, "WHERE 在聚合前过滤行。"),
        (
            "哪个操作保留两表匹配的行？",
            ["INNER JOIN", "DROP", "COMMIT"],
            0,
            "INNER JOIN 根据连接条件匹配行。",
        ),
        (
            "对每个部门统计人数通常使用？",
            ["DELETE", "LIMIT", "GROUP BY"],
            2,
            "GROUP BY 定义聚合分组。",
        ),
    ],
    "git": [
        (
            "哪个命令查看工作区状态？",
            ["git push", "git status", "git init"],
            1,
            "status 展示修改和暂存状态。",
        ),
        (
            "提交前将文件加入暂存区的命令是？",
            ["git add", "git clone", "git fetch"],
            0,
            "add 将变更暂存。",
        ),
        (
            "哪个操作获取远端提交但不自动合并？",
            ["commit", "push", "fetch"],
            2,
            "fetch 更新远端跟踪引用。",
        ),
    ],
    "javascript": [
        (
            "声明块级且可重新赋值的变量用？",
            ["const", "let", "import"],
            1,
            "let 是块级绑定且允许重新赋值。",
        ),
        (
            "等待 Promise 的语法是？",
            ["await", "yielded", "waitfor"],
            0,
            "await 等待 Promise 结果。",
        ),
        ("严格相等运算符是？", ["=", "==", "==="], 2, "=== 不进行隐式类型转换。"),
    ],
    "docker": [
        (
            "构建镜像通常读取哪个文件？",
            ["package.txt", "Dockerfile", "README"],
            1,
            "Dockerfile 定义镜像构建步骤。",
        ),
        (
            "停止容器后持久化数据通常使用？",
            ["volume", "临时进程内变量", "只读镜像层"],
            0,
            "volume 的生命周期独立于容器。",
        ),
        (
            "Compose 的服务编排通常使用？",
            ["CSV", "PNG", "YAML"],
            2,
            "Compose 文件使用 YAML 定义服务。",
        ),
    ],
    "fastapi": [
        (
            "定义 GET 路由常用哪个装饰器？",
            ["@app.put", "@app.get", "@app.delete"],
            1,
            "get 对应 HTTP GET 方法。",
        ),
        (
            "FastAPI 通常用什么校验请求模型？",
            ["Pydantic", "CSS", "Dockerfile"],
            0,
            "Pydantic 根据模型契约校验输入。",
        ),
        ("将共享逻辑注入路由常用？", ["print", "global", "Depends"], 2, "Depends 支持依赖注入。"),
    ],
    "pytorch": [
        (
            "PyTorch 的基本多维数组类型是？",
            ["DataFrame", "Tensor", "DOM"],
            1,
            "Tensor 承载多维数值数据。",
        ),
        (
            "计算梯度通常调用哪个方法？",
            ["backward", "render", "mount"],
            0,
            "backward 沿计算图计算梯度。",
        ),
        (
            "推理时常用哪个上下文避免记录梯度？",
            ["grad_enabled", "train_only", "no_grad"],
            2,
            "no_grad 停止记录求导操作。",
        ),
    ],
    "react": [
        (
            "维护组件局部状态通常使用？",
            ["useStyle", "useState", "useRouteFile"],
            1,
            "useState 声明状态。",
        ),
        (
            "渲染列表时稳定标识元素的属性是？",
            ["key", "nameOnly", "cache"],
            0,
            "稳定 key 帮助 React 跟踪元素。",
        ),
        (
            "更新对象状态应采用？",
            ["直接修改旧对象且不更新", "刷新浏览器", "创建新对象并调用更新函数"],
            2,
            "不可变更新让状态变更可追踪。",
        ),
    ],
}


def create(db, principal, skill_id, trace):
    catalogue.require_skill(db, skill_id)
    if skill_id not in BANK:
        raise DomainError("ASSESSMENT_UNAVAILABLE", "此技能尚无当前版本测验", 422)
    questions = [
        {"id": f"{skill_id}.{i}", "prompt": q[0], "choices": q[1]}
        for i, q in enumerate(BANK[skill_id])
    ]
    return growth.save(
        db,
        principal,
        "assessment",
        {
            "skill_id": skill_id,
            "bank_version": BANK_VERSION,
            "rubric_version": BANK_VERSION + ":" + skill_id,
            "scope": "foundation",
            "pass_count": len(questions),
            "questions": questions,
            "status": "pending",
            "result": None,
        },
        trace,
    )


def grade(skill_id, answers, bank_version):
    if bank_version != BANK_VERSION or skill_id not in BANK:
        raise ValueError("Question bank version unavailable")
    expected = BANK[skill_id]
    if len(answers) != len(expected):
        raise DomainError("ANSWERS_REQUIRED", "请回答全部题目", 422)
    feedback = [
        {
            "question_id": f"{skill_id}.{i}",
            "correct": answer == q[2],
            "explanation": q[3],
            "expected_choice": q[2],
        }
        for i, (answer, q) in enumerate(zip(answers, expected, strict=True))
    ]
    score = sum(f["correct"] for f in feedback)
    return {
        "score": score,
        "total": len(expected),
        "passed": score == len(expected),
        "feedback": feedback,
    }


def submit(db, principal, assessment_id, answers, version, trace):
    row = growth.owned(db, principal, assessment_id, "assessment")
    payload = json.loads(row.payload_json)
    digest = growth.payload_hash({"answers": answers})
    if payload["status"] != "pending":
        if payload.get("submission_hash") == digest:
            return growth.data(row)
        raise DomainError("ALREADY_SUBMITTED", "测验已提交，请新建一次测验")
    try:
        result = grade(payload["skill_id"], answers, payload["bank_version"])
    except DomainError:
        raise
    except Exception:
        # An evaluator outage is neither a learner failure nor capability evidence.
        payload.update(
            status="system_error", result={"code": "EVALUATOR_UNAVAILABLE"}, submission_hash=digest
        )
        return growth.save(db, principal, "assessment", payload, trace, row, version)
    payload.update(
        status="passed" if result["passed"] else "failed",
        result=result,
        answers=answers,
        submission_hash=digest,
    )
    saved = growth.save(db, principal, "assessment", payload, trace, row, version)
    evidence = learning.add_evidence(
        db,
        principal,
        payload["skill_id"],
        "assessment_result",
        row.id,
        saved["version"],
        payload["status"],
        trace,
        payload["rubric_version"],
    )
    saved["evidence"] = evidence
    from careerpilot import analytics

    analytics.record(db, principal, "assessment_completed", trace, trusted=True)
    if result["passed"]:
        analytics.verified_action(db, principal, {payload["skill_id"]}, trace)
    saved["replan_preview"] = learning.auto_preview(db, principal, "EvidenceChanged", trace)
    return saved
