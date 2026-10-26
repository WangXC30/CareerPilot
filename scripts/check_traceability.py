"""Validate authoritative requirement mappings without inventing acceptance evidence."""

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def build():
    tasks = (ROOT / "TASKS.md").read_text(encoding="utf-8")
    prd = (ROOT / "PRD.md").read_text(encoding="utf-8")
    plan = (ROOT / "PLAN.md").read_text(encoding="utf-8")
    pattern = r"(?:FR-[A-Z]+-\d{3}|NFR-\d{3}|E2E-\d{2})"
    expected = set(re.findall(pattern, prd))
    records = {}
    for line in tasks.splitlines():
        match = re.match(r"\| `(" + pattern + r")` \|", line)
        if not match:
            continue
        requirement = match.group(1)
        assert requirement not in records, f"Duplicate mapping: {requirement}"
        owners = re.findall(r"CP-\d{3}", line)
        assert owners, f"No task: {requirement}"
        records[requirement] = {
            "tasks": owners,
            "acceptance_owner": "Codex（自动技术验收）；项目维护者（交付复核）",
            "status": "未验收",
            "test_evidence": ["tests/test_foundation.py::test_requirement_traceability"],
        }
    assert records.keys() == expected, f"Requirement mismatch: {records.keys() ^ expected}"
    counts = {
        prefix: sum(k.startswith(prefix) for k in records) for prefix in ("FR-", "NFR-", "E2E-")
    }
    assert list(counts.values()) == [110, 18, 18], counts
    task_ids = set(re.findall(r"^### (CP-\d{3})｜|^\| \*\*(CP-30\d) ·", tasks, re.M))
    task_ids = {a or b for a, b in task_ids}
    board = re.findall(r"^\| (CP-\d{3}) \|", plan, re.M)
    assert len(board) == len(set(board)) == 84
    assert set(board) == task_ids, set(board) ^ task_ids
    assert all(set(r["tasks"]) <= task_ids for r in records.values())
    records["FR-OPS-001"]["status"] = "V0 本地技术验证通过"
    records["FR-OPS-001"]["test_evidence"] = [
        "tests/test_governance.py::test_operator_can_only_see_aggregate_metadata",
        "scripts/browser_smoke.py",
    ]
    v1_section = tasks.split("## 4. V1", 1)[1].split("## 5. V2", 1)[0]
    v1_tasks = set(re.findall(r"^### (CP-\d{3})｜", v1_section, re.M))
    for requirement, record in records.items():
        if requirement.startswith("FR-") and set(record["tasks"]) & v1_tasks:
            record["status"] = "V1 本地技术验证通过（范围见 docs/V1-EXIT.md）"
            record["test_evidence"] = [
                "tests/test_growth.py",
                "tests/test_v1_release.py",
                "docs/v1-release/v1-pytest.xml",
            ]
        if requirement.startswith("E2E-") and int(requirement[-2:]) <= 7:
            record["status"] = "V1 阶段范围通过"
            if requirement == "E2E-05":
                record["status"] += "；客观测验子场景，V3 代码子场景未执行"
            if requirement == "E2E-06":
                record["status"] += "；证据/差距/计划，V2 岗位匹配分项未执行"
            record["test_evidence"] = [
                "scripts/browser_v1.py",
                "docs/v1-release/ui-v1/browser-report.json",
            ]
    for requirement in ("FR-CAR-002", "FR-CAR-003", "FR-MAT-001", "FR-MAT-002"):
        records[requirement]["status"] = (
            "V2 首批本地技术验证通过（软件基础目录/规则，见 docs/verification-v2-batch1.md）"
            if requirement.startswith("FR-CAR")
            else "V2 首批规则/技能分项通过；项目验收分项未完成，CP-190 仍开发中"
        )
        records[requirement]["test_evidence"] = [
            "tests/test_v2_matching.py",
            "scripts/evaluate_v2.py",
            "scripts/browser_v2.py",
            "docs/v2-batch1/evaluation-v2.json",
            "docs/v2-batch1/ui-v2/browser-report.json",
        ]
    records["E2E-06"]["status"] = (
        "V1 证据/差距/计划通过；V2 首批基础技能匹配分项追加通过，项目分项待后续"
    )
    records["E2E-06"]["test_evidence"].append("tests/test_v2_matching.py")
    for requirement in ("FR-JOB-005", "FR-JOB-006", "FR-MAT-003", "FR-MAT-004"):
        records[requirement]["status"] = (
            "V2 第二批本地岗位库功能验证通过；不包含外部招聘源，CP-191 依赖 CP-190 尚未全部退出"
        )
        records[requirement]["test_evidence"] = [
            "tests/test_v2_opportunities.py",
            "scripts/browser_v2_discovery.py",
            "docs/v2-batch2/ui-discovery/browser-report.json",
            "docs/verification-v2-batch2.md",
        ]
    records["E2E-12"]["status"] = "V2 本地岗位库订阅子场景通过；外部源增量未执行"
    records["E2E-12"]["test_evidence"] = [
        "scripts/browser_v2_discovery.py",
        "tests/test_v2_opportunities.py",
    ]
    for requirement in (
        "FR-CV-001",
        "FR-CV-002",
        "FR-CV-003",
        "FR-CV-004",
        "FR-CV-005",
        "FR-APP-001",
        "FR-APP-002",
        "FR-APP-003",
        "FR-APP-004",
    ):
        records[requirement]["status"] = (
            "V2 第三批本地文本/人工回执/站内渠道专项验证通过；边界见第三批记录"
        )
        records[requirement]["test_evidence"] = [
            "tests/test_v2_hiring.py",
            "scripts/browser_v2_hiring.py",
            "scripts/check_resume_exports.py",
            "docs/verification-v2-batch3.md",
            "docs/v2-batch3/ui-hiring/browser-report.json",
            "docs/v2-batch3/export-qa/content-report.json",
        ]
    for requirement in ("E2E-09", "E2E-11", "E2E-16"):
        records[requirement]["status"] = (
            "V2 第三批文本简历/人工投递记录/新增私有数据隔离子场景通过；完整质量包未验收"
        )
        records[requirement]["test_evidence"] = [
            "tests/test_v2_hiring.py",
            "scripts/browser_v2_hiring.py",
            "docs/verification-v2-batch3.md",
        ]
    for requirement in ("FR-INT-001", "FR-INT-002", "FR-INT-003", "FR-INT-004"):
        records[requirement]["status"] = (
            "V2 第四批本地文字练习/规则代理评价/确认复盘范围通过；开放题正确性未知"
        )
        records[requirement]["test_evidence"] = [
            "tests/test_v2_interviews.py",
            "scripts/evaluate_interviews.py",
            "scripts/browser_v2_interviews.py",
            "docs/v2-batch4/evaluation-interviews.json",
            "docs/v2-batch4/ui-interviews/browser-report.json",
            "docs/verification-v2-batch4.md",
        ]
    records["E2E-10"]["status"] = (
        "V2 第四批本地文字练习到复盘/计划确认子场景通过；完整 V2 质量包未验收"
    )
    records["E2E-10"]["test_evidence"] = [
        "tests/test_v2_interviews.py",
        "scripts/browser_v2_interviews.py",
        "docs/verification-v2-batch4.md",
    ]
    records["E2E-16"]["test_evidence"].append("tests/test_v2_interviews.py")
    for requirement in ("FR-MAT-001", "FR-MAT-002"):
        records[requirement]["status"] = (
            "V2 匹配规则/基础证据通过；第四批受信项目回执消费合同通过；"
            "V3 生产端未接入，CP-190 仍开发中"
        )
        records[requirement]["test_evidence"].extend(
            ["tests/test_v2_interviews.py", "docs/ADR-017-v2-interviews-project-contract.md"]
        )
    for requirement in ("FR-SRC-003", "FR-SRC-004", "FR-SRC-005", "FR-SRC-006"):
        records[requirement]["status"] = (
            "V2 第五批本地索引/公开仓库元数据/用户导入范围通过；受限平台直接搜索关闭"
        )
        records[requirement]["test_evidence"] = [
            "tests/test_v2_sources.py",
            "scripts/browser_v2_sources.py",
            "docs/v2-batch5/ui-sources/browser-report.json",
            "docs/v2-batch5/live-github.json",
            "docs/verification-v2-batch5.md",
            "docs/source-review-v2.md",
        ]
    for requirement in ("E2E-13", "E2E-17"):
        records[requirement]["status"] = (
            "V2 第五批公开元数据故障/受限平台手动回退子场景通过；完整质量包未验收"
        )
        records[requirement]["test_evidence"] = [
            "tests/test_v2_sources.py",
            "scripts/browser_v2_sources.py",
            "docs/verification-v2-batch5.md",
        ]
    records["E2E-16"]["test_evidence"].append("tests/test_v2_sources.py")
    for requirement in (
        "FR-AGT-004",
        "FR-AGT-005",
        "FR-AGT-006",
        "FR-NOT-001",
        "FR-NOT-002",
        "FR-NOT-003",
        "FR-OPS-002",
        "FR-OPS-003",
        "FR-OPS-004",
        "FR-OPS-005",
    ):
        records[requirement]["status"] = (
            "V2 第六批本地受控三步/站内渠道/本人记录报表/公共治理和元数据工单通过；"
            "邮件及原文共享未启用"
        )
        records[requirement]["test_evidence"] = [
            "tests/test_v2_progress.py",
            "tests/test_v2_recovery.py",
            "tests/test_v2_operations.py",
            "scripts/browser_v2_progress.py",
            "scripts/progress_contract.py",
            "scripts/maintenance_contract.py",
            "docs/v2-batch6/ui-progress/browser-report.json",
            "docs/verification-v2-batch6.md",
        ]
    for requirement in (
        "E2E-08",
        "E2E-09",
        "E2E-10",
        "E2E-11",
        "E2E-12",
        "E2E-13",
        "E2E-17",
        "E2E-18",
    ):
        records[requirement]["status"] = (
            "V2 本地支持范围技术验证通过；合成资料/人工回执/受限平台关闭及受控三步，"
            "不代表外部生产渠道或完整阶段退出"
        )
        records[requirement]["test_evidence"].extend(
            [
                "tests/test_v2_progress.py",
                "tests/test_v2_recovery.py",
                "scripts/browser_v2_progress.py",
                "docs/V2-ACCEPTANCE.md",
                "docs/verification-v2-batch6.md",
            ]
        )
    records["E2E-16"]["test_evidence"].extend(
        ["tests/test_v2_operations.py", "tests/test_v2_progress.py"]
    )
    for requirement in (
        "FR-EVA-003",
        "FR-EVA-004",
        "FR-PRJ-001",
        "FR-PRJ-002",
        "FR-PRJ-003",
        "FR-PRJ-004",
    ):
        records[requirement]["status"] = (
            "V3 首批本地 Python 项目评测生产链通过；预算独立估算/固定里程碑/确认陈述，"
            "完整任务仍开发或测试中，边界见首批记录"
        )
        records[requirement]["test_evidence"] = [
            "tests/test_v3_projects.py",
            "scripts/project_drill.py",
            "docs/v3-batch1/sandbox-report.json",
            "docs/verification-v3-batch1.md",
        ]
    for requirement in ("FR-MAT-001", "FR-MAT-002", "FR-MAT-003", "FR-MAT-004"):
        records[requirement]["status"] = (
            "V2 既有范围通过，V3 首批真实本地项目回执已接入；完整 CP-190/191 出口复核中"
        )
        records[requirement]["test_evidence"].extend(
            [
                "tests/test_v3_projects.py",
                "docs/v3-batch1/sandbox-report.json",
            ]
        )
    for requirement in ("E2E-05", "E2E-06"):
        records[requirement]["status"] += "；V3 首批追加真实 Python 沙箱/项目匹配子场景通过"
        records[requirement]["test_evidence"].append("docs/v3-batch1/sandbox-report.json")
    for requirement in (
        "FR-EVA-003",
        "FR-EVA-004",
        "FR-EVA-005",
        "FR-PRJ-001",
        "FR-PRJ-002",
        "FR-PRJ-003",
        "FR-PRJ-004",
    ):
        records[requirement]["status"] = (
            "V3 第二批本地 Python 项目闭环技术验证通过；阶段/私人文本/确认排程/STAR/复核，"
            "面试来源失效拒绝继续；不代表生产隔离或经历真实性审核"
        )
        records[requirement]["test_evidence"].extend(
            [
                "tests/test_v3_workflow.py",
                "docs/verification-v3-batch2.md",
                "docs/v3-batch2/sandbox-report.json",
                "docs/v3-batch2/postgres.log",
            ]
        )
    for requirement in ("FR-MAT-001", "FR-MAT-002", "FR-MAT-003", "FR-MAT-004"):
        records[requirement]["status"] = (
            "V2 已配置本地范围项目依赖出口复核通过；外部生产渠道边界保留"
        )
        records[requirement]["test_evidence"].append("docs/verification-v3-batch2.md")
    for requirement in ("FR-SKL-005", "FR-SKL-006"):
        records[requirement]["status"] = (
            "V3 第三批多岗位交集/先修图/来源展开与基础测验入口通过；有限词库和本地范围"
        )
        records[requirement]["test_evidence"] = [
            "tests/test_v3_skill_graph.py",
            "scripts/browser_v3_graph.py",
            "docs/v3-batch3/ui-graph/browser-report.json",
            "docs/verification-v3-batch3.md",
        ]
    for requirement in ("FR-LRN-006", "FR-LRN-007", "FR-LRN-008"):
        records[requirement]["status"] = (
            "V3 第四批模式预算确认、规则间隔复习/关闭站内提醒、个人资源反馈与替代入口通过"
        )
        records[requirement]["test_evidence"] = [
            "tests/test_v3_study.py",
            "scripts/browser_v3_study.py",
            "scripts/study_contract.py",
            "docs/v3-batch4/ui-study/browser-report.json",
            "docs/v3-batch4/postgres.log",
            "docs/verification-v3-batch4.md",
        ]
    for requirement in ("FR-PRO-005", "FR-PRO-006"):
        records[requirement]["status"] = (
            "V3 时间线与 GitHub 绑定本地合同通过；真实绑定/同步/解绑由用户确认，见 W-021"
        )
        records[requirement]["test_evidence"] = [
            "tests/test_v3_profile_history.py",
            "scripts/browser_v3_profile_history.py",
            "scripts/profile_history_contract.py",
        ]
    v4_local = {
        "FR-ACC-004": [
            "tests/test_v4_sessions.py",
            "scripts/browser_v3_profile_history.py",
        ],
        "FR-ACC-005": [
            "tests/test_v4_collaboration.py",
            "scripts/browser_v4_collaboration.py",
            "scripts/v4_contract.py",
        ],
        "FR-PRO-007": [
            "tests/test_v4_growth.py",
            "scripts/browser_v4.py",
            "scripts/v4_contract.py",
        ],
        "FR-CAR-005": ["tests/test_v4_growth.py", "scripts/browser_v4.py"],
        "FR-CAR-006": [
            "tests/test_v4_growth.py",
            "scripts/browser_v4.py",
            "scripts/v4_contract.py",
        ],
        "FR-PRJ-005": ["tests/test_v4_collaboration.py", "scripts/browser_v4.py"],
        "FR-PRJ-006": [
            "tests/test_v4_collaboration.py",
            "scripts/browser_v4_collaboration.py",
            "scripts/v4_contract.py",
        ],
    }
    for requirement, evidence in v4_local.items():
        records[requirement]["status"] = (
            "V4 本地范围技术验收；有限词库/本人岗位样本/显式协作，生产与外部渠道未代验"
        )
        records[requirement]["test_evidence"] = evidence
    for requirement in ("FR-APP-005", "FR-APP-006", "FR-APP-007"):
        records[requirement]["status"] = (
            "V4 本地断网申请审批/撤销/未知核对及 Offer 本人决定技术验收；真实平台 API 关闭、未代验"
        )
        records[requirement]["test_evidence"] = [
            "tests/test_v4_application_lab.py",
            "scripts/browser_v4_application.py",
            "scripts/v4_contract.py",
        ]
    records["FR-SKL-007"]["status"] = (
        "V4 本地公共词库独立审核/diff/权重灰度回滚与冻结历史规则重放技术验收；不代验市场准确率"
    )
    records["FR-SKL-007"]["test_evidence"] = [
        "tests/test_v4_matching_history.py",
        "tests/test_v4_growth.py",
        "scripts/browser_v4_governance.py",
        "scripts/v4_contract.py",
    ]
    for requirement in ("FR-NOT-004", "FR-NOT-005", "FR-NOT-006"):
        records[requirement]["status"] = (
            "V4 本地逐项 ICS/撤销、空间字段进度评论和复盘快照/显式目标确认技术验收；"
            "不连接第三方日历"
        )
        records[requirement]["test_evidence"] = [
            "tests/test_v4_calendar.py",
            "tests/test_v4_collaboration.py",
            "scripts/browser_v4_calendar.py",
            "scripts/browser_v4_collaboration.py",
            "scripts/v4_contract.py",
        ]
    for requirement in ("FR-LRN-009", "FR-LRN-010", "FR-EVA-006", "FR-EVA-007"):
        records[requirement]["status"] = (
            "V4 本地编辑路线/错题规则会话/分层练习与显式同伴复核授权技术验收；"
            "不判断自由文本正确性或作弊，不自动改变能力"
        )
        records[requirement]["test_evidence"] = [
            "tests/test_v4_learning.py",
            "tests/test_v4_growth.py",
            "scripts/browser_v4_learning.py",
            "scripts/learning_contract.py",
        ]
    for requirement in (
        "FR-JOB-007",
        "FR-JOB-008",
        "FR-SRC-007",
        "FR-SRC-008",
        "FR-MAT-005",
        "FR-MAT-006",
        "FR-CV-006",
        "FR-CV-007",
        "FR-INT-005",
        "FR-INT-006",
        "FR-OPS-006",
    ):
        records[requirement]["status"] = (
            "V4 本地来源降级/授权索引订阅/截止规则/双语分享/英文实验草稿/练习追踪/"
            "同意后服务端统计技术验收；不代验真实渠道、识别质量或生产匿名化保证"
        )
        records[requirement]["test_evidence"] = [
            "tests/test_v4_remaining.py",
            "tests/test_v4_media.py",
            "tests/test_v4_local_tools.py",
            "scripts/evaluate_v4_matching.py",
            "scripts/browser_v4_remaining.py",
            "scripts/browser_v4_voice.py",
            "scripts/remaining_contract.py",
        ]
    for requirement in ("FR-AGT-007", "FR-AGT-008"):
        records[requirement]["status"] = (
            "CP-222 部分交付：受控模型风险标注对照/重复质量成本延迟/冻结重算/临时步骤复核；"
            "当前合同使用模拟适配器，真实模型、代表性样本与独立收益未验收，协作关闭"
        )
        records[requirement]["test_evidence"] = [
            "tests/test_model_comparison.py",
            "tests/test_gateway.py",
            "scripts/compare_agent_models.py",
            "scripts/browser_model_comparison.py",
        ]
    records["NFR-008"]["status"] = (
        "本人本地使用工程范围；来源授权、数据告知和依赖审核；正式发布法律签核未验收"
    )
    records["NFR-008"]["test_evidence"] = [
        "tests/test_source_review.py",
        "tests/test_governance.py",
        "scripts/dependency_report.py",
        "scripts/browser_v4_locale.py",
    ]
    records["NFR-018"]["test_evidence"].extend(
        ["tests/test_source_review.py", "tests/test_governance.py"]
    )
    # Requirement mappings point to maintained sources, not disposable run output.
    for record in records.values():
        record["test_evidence"] = [
            entry for entry in record["test_evidence"] if not entry.startswith("docs/")
        ]
    return {"counts": counts, "requirements": records}


if __name__ == "__main__":
    from temporary_artifacts import temporary_artifacts

    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="Validate source mappings")
    parser.add_argument("--output", type=Path, help="Temporary filename; deleted on exit")
    args = parser.parse_args()
    result = build()
    if args.output and not args.check:
        with temporary_artifacts(args.output, filename=True) as target:
            target.write_text(
                json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
    print(f"Validated {result['counts']}; 84 tasks; no retained report")
