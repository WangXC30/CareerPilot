"""Versioned, objective foundation checks. Learning completion never grants verification."""

import hashlib
import json
import random
from pathlib import Path

from careerpilot import catalogue, growth, learning, question_bank_v3
from careerpilot.domain import DomainError

BANK_VERSION = "foundation.v3"
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

# Keep the original rubric available for assessments created before this release.
LEGACY_BANK = {skill: list(questions) for skill, questions in BANK.items()}
EXTRA = {
    "python": [
        (
            "列表 a=[1, 2]，执行 a.append(3) 后 a 的长度是？",
            ["2", "3", "4"],
            1,
            "append 在列表末尾增加一个元素。",
        ),
        (
            "遍历字典的键，通常可以使用？",
            ["for key in d", "for key in d.values()", "d.append(key)"],
            0,
            "直接迭代字典会依次得到键。",
        ),
        (
            "with open(...) 的主要好处是？",
            ["自动把文件内容排序", "保证文件永远存在", "退出代码块时关闭文件"],
            2,
            "上下文管理器退出时会释放文件资源。",
        ),
    ],
    "sql": [
        (
            "COUNT(column) 会统计 NULL 值吗？",
            ["会", "不会", "仅统计 NULL"],
            1,
            "COUNT(column) 统计该列非 NULL 的行。",
        ),
        (
            "对聚合结果过滤通常使用哪个子句？",
            ["HAVING", "WHERE", "ORDER BY"],
            0,
            "HAVING 用于过滤分组后的聚合结果。",
        ),
        (
            "LEFT JOIN 未匹配的右表列通常是什么？",
            ["0", "空字符串", "NULL"],
            2,
            "未匹配的右表列以 NULL 补齐。",
        ),
    ],
    "git": [
        ("查看提交历史通常使用？", ["git add", "git log", "git init"], 1, "git log 展示提交历史。"),
        (
            "创建并切换到新分支可以使用？",
            ["git switch -c 分支名", "git status 分支名", "git fetch -c"],
            0,
            "git switch -c 创建分支并切换。",
        ),
        (
            "git diff 默认展示什么？",
            ["远端所有分支", "已提交的历史", "工作区相对暂存区的差异"],
            2,
            "默认 diff 比较工作区和暂存区。",
        ),
    ],
    "javascript": [
        (
            "数组的 map 通常返回什么？",
            ["原数组长度", "映射后的新数组", "一个布尔值"],
            1,
            "map 对每个元素调用函数并返回新数组。",
        ),
        (
            "JSON.parse 的用途是？",
            ["将 JSON 字符串解析为值", "将对象写入数据库", "比较对象引用"],
            0,
            "JSON.parse 解析合法的 JSON 字符串。",
        ),
        (
            "Promise 被拒绝时可使用什么处理？",
            [".thenOnly()", ".finallyError()", ".catch()"],
            2,
            "catch 可处理 Promise 的拒绝。",
        ),
    ],
    "docker": [
        (
            "查看运行中容器通常使用？",
            ["docker build", "docker ps", "docker image save"],
            1,
            "docker ps 默认列出运行中的容器。",
        ),
        (
            "镜像和容器的关系是？",
            ["容器是镜像的运行实例", "镜像是运行中的容器", "二者是同一个进程"],
            0,
            "镜像提供创建容器的模板。",
        ),
        (
            "映射容器端口时常用哪个选项？",
            ["-v", "--name", "-p"],
            2,
            "-p 可将宿主机端口映射到容器端口。",
        ),
    ],
    "fastapi": [
        (
            "路径参数 /items/{item_id} 中 item_id 来自哪里？",
            ["请求头", "URL 路径", "响应体"],
            1,
            "花括号中的名称对应 URL 路径参数。",
        ),
        (
            "请求数据不符合声明的模型时通常会怎样？",
            ["返回校验错误", "自动忽略全部错误并成功", "重启服务"],
            0,
            "FastAPI 会返回请求校验错误。",
        ),
        (
            "表示资源不存在通常使用哪个 HTTP 状态码？",
            ["200", "201", "404"],
            2,
            "404 表示请求的资源未找到。",
        ),
    ],
    "pytorch": [
        (
            "model.eval() 主要用于？",
            ["清空参数", "切换模块到评估模式", "自动更新权重"],
            1,
            "eval 切换 Dropout 等模块的行为；它本身不关闭梯度。",
        ),
        (
            "optimizer.step() 通常用于？",
            ["根据梯度更新参数", "读取数据文件", "定义模型结构"],
            0,
            "优化器使用当前梯度更新参数。",
        ),
        (
            "训练循环中 optimizer.zero_grad() 通常用于？",
            ["保存模型", "关闭 GPU", "清除已有梯度"],
            2,
            "PyTorch 梯度默认累积，通常需要在下一次反向传播前清除。",
        ),
    ],
    "react": [
        (
            "父组件向子组件传递数据通常使用？",
            ["数据库迁移", "props", "CSS 选择器"],
            1,
            "props 是父组件向子组件传递数据的方式。",
        ),
        (
            "事件处理函数应如何传给 onClick？",
            ["传递函数", "渲染时立即调用并传递结果", "传递 CSS 类名"],
            0,
            "传递函数使 React 在事件发生时调用它。",
        ),
        (
            "useEffect 的清理函数通常用于？",
            ["修改 JSX 语法", "替代所有状态", "取消订阅或清理定时器"],
            2,
            "清理函数用于撤销 effect 建立的订阅等资源。",
        ),
    ],
}
CURRENT_BANK = {skill: questions + EXTRA[skill] for skill, questions in LEGACY_BANK.items()}
V3_BANK = {
    skill: questions + question_bank_v3.QUESTIONS[skill]
    for skill, questions in CURRENT_BANK.items()
}
GENERATED_VERSION = "foundation.v4"
GENERATED_SHA256 = "d01037aa4314ca5db91feb30d57412600643190d6dc9507ac0ea5d3aff54202b"
generated_bytes = (Path(__file__).parent / "generated_foundation_v4.json").read_bytes()
if hashlib.sha256(generated_bytes).hexdigest() != GENERATED_SHA256:
    raise RuntimeError("Frozen foundation.v4 question bank has changed")
generated_bank = json.loads(generated_bytes)
V4_BANK = {skill: V3_BANK[skill] + questions for skill, questions in generated_bank.items()}
QUESTIONS_PER_ASSESSMENT = 6


def create(db, principal, skill_id, trace):
    catalogue.require_skill(db, skill_id)
    if skill_id not in BANK:
        raise DomainError("ASSESSMENT_UNAVAILABLE", "此技能尚无当前版本测验", 422)
    rng = random.SystemRandom()
    version = GENERATED_VERSION if skill_id in V4_BANK else BANK_VERSION
    bank = V4_BANK if version == GENERATED_VERSION else V3_BANK
    if version == GENERATED_VERSION:
        # Mix editorial coverage with distinct executable exercise families.
        start = len(V3_BANK[skill_id])
        families = rng.sample(range(10), 3)
        indices = rng.sample(range(start), 3) + [
            start + family * 500 + rng.randrange(500) for family in families
        ]
        rng.shuffle(indices)
    else:
        indices = rng.sample(range(len(bank[skill_id])), QUESTIONS_PER_ASSESSMENT)
    questions = [
        {"id": f"{skill_id}.{i}", "prompt": q[0], "choices": q[1]}
        for i in indices
        for q in [bank[skill_id][i]]
    ]
    return growth.save(
        db,
        principal,
        "assessment",
        {
            "skill_id": skill_id,
            "bank_version": version,
            "rubric_version": version + ":" + skill_id,
            "scope": "foundation",
            "pass_count": len(questions),
            "questions": questions,
            "status": "pending",
            "result": None,
        },
        trace,
    )


def grade(skill_id, answers, bank_version, question_ids=None):
    bank = (
        LEGACY_BANK
        if bank_version == "foundation.v1"
        else CURRENT_BANK
        if bank_version == "foundation.v2"
        else V3_BANK
        if bank_version == "foundation.v3"
        else V4_BANK
        if bank_version == GENERATED_VERSION
        else {}
    )
    if skill_id not in bank:
        raise ValueError("Question bank version unavailable")
    indices = list(range(len(bank[skill_id])))
    if bank_version in {"foundation.v3", GENERATED_VERSION}:
        prefix = skill_id + "."
        if not isinstance(question_ids, list) or len(question_ids) != QUESTIONS_PER_ASSESSMENT:
            raise ValueError("Invalid question selection")
        if any(
            not isinstance(q, str) or not q.startswith(prefix) or not q[len(prefix) :].isdigit()
            for q in question_ids
        ):
            raise ValueError("Invalid question identifier")
        indices = [int(q[len(prefix) :]) for q in question_ids]
        if len(set(indices)) != len(indices) or any(i >= len(bank[skill_id]) for i in indices):
            raise ValueError("Invalid question selection")
    expected = [bank[skill_id][i] for i in indices]
    if len(answers) != len(expected):
        raise DomainError("ANSWERS_REQUIRED", "请回答全部题目", 422)
    if any(
        type(answer) is not int or not 0 <= answer < len(q[1])
        for answer, q in zip(answers, expected, strict=True)
    ):
        raise DomainError("ANSWER_INVALID", "请选择题目中提供的选项", 422)
    feedback = [
        {
            "question_id": f"{skill_id}.{index}",
            "correct": answer == q[2],
            "explanation": q[3],
            "expected_choice": q[2],
        }
        for index, answer, q in zip(indices, answers, expected, strict=True)
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
        if payload["bank_version"] in {"foundation.v3", GENERATED_VERSION}:
            result = grade(
                payload["skill_id"],
                answers,
                payload["bank_version"],
                [q["id"] for q in payload["questions"]],
            )
        else:
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
