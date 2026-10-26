"""Presentation-only English explanations for matching and local job discovery.

Canonical fields, scores, versions, quotations and frozen rule contexts are untouched.
Only known system-explanation paths receive an optional `_ui` overlay.
"""

import copy
import re

TEXT = dict(
    [
        (
            ("实验性解释分，不是录用概率。基础测验不代表完整岗位胜任力。"),
            (
                "Experimental explanation score, not a hiring probability. Basic assessments "
                "do not establish full job readiness."
            ),
        ),
        (
            ("各组独立排序；未知与不符合不会进入明确符合列表"),
            (
                "Groups are ranked separately. Unknown and failed eligibility never enter the"
                " eligible group."
            ),
        ),
        (
            ("学历或档案未明确，请核实"),
            ("Education or profile information is unclear. Check it."),
        ),
        (
            ("岗位明确不限学历"),
            ("The job explicitly has no education restriction."),
        ),
        (
            ("学历条件存在偏好、并列或附加限制，请核实"),
            (
                "Education includes preferences, alternatives or extra restrictions. Check th"
                "e original requirements."
            ),
        ),
        (
            ("已确认学历达到最低要求"),
            ("Confirmed education meets the minimum requirement."),
        ),
        (
            ("已确认学历未达到最低要求"),
            ("Confirmed education does not meet the minimum requirement."),
        ),
        (
            ("毕业年份与岗位届别一致"),
            ("Graduation year matches the job cohort."),
        ),
        (
            ("毕业年份与岗位届别不符"),
            ("Graduation year does not match the job cohort."),
        ),
        (
            ("岗位明确不限届别"),
            ("The job explicitly has no graduation cohort restriction."),
        ),
        (
            ("届别未明确或含并列/附加条件，请核实"),
            ("Cohort is unclear or includes alternatives or extra conditions. Check it."),
        ),
        (
            ("用户将城市设置为软偏好"),
            ("You chose to treat cities as a soft preference."),
        ),
        (
            ("地点未明确或无法与期望城市精确比较"),
            ("Location is unclear or cannot be compared exactly with preferred cities."),
        ),
        (
            ("工作地符合城市约束"),
            ("The work location meets the city constraint."),
        ),
        (
            ("工作地不在允许城市中"),
            ("The work location is outside the allowed cities."),
        ),
        (
            ("可实习时长达到要求"),
            ("Available internship duration meets the requirement."),
        ),
        (
            ("可实习时长不足"),
            ("Available internship duration is too short."),
        ),
        (
            ("明确全职岗位，实习时长不适用"),
            ("This is explicitly a full-time job; internship duration does not apply."),
        ),
        (
            ("实习期限或可用时长未明确，请核实"),
            ("Required or available internship duration is unclear. Check it."),
        ),
        (
            ("岗位明确无限制或不适用"),
            ("The job explicitly states no restriction or not applicable."),
        ),
        (
            ("岗位或用户资格信息未明确，请核实"),
            ("Job requirements or your qualifications are unclear. Check them."),
        ),
        (
            ("用户已确认持有所列资格；仍需源站核验"),
            ("You confirmed the listed qualification. Verification at the source is still needed."),
        ),
        (
            ("用户已确认无所需资格"),
            ("You confirmed that you do not hold the required qualification."),
        ),
        (
            ("资格名称未精确对应，请核实等效性与附加要求"),
            ("Qualification names do not match exactly. Check equivalence and extra requirements."),
        ),
        (
            ("岗位已关闭或合并，不再推荐申请"),
            ("The job is closed or merged; applying is no longer recommended."),
        ),
        (
            ("岗位已超过明确截止日期"),
            ("The job has passed its explicit deadline."),
        ),
        (
            ("岗位可能失效，请核实来源"),
            ("The job may be unavailable. Check the source."),
        ),
        (
            ("用户核对为有效；仍应在投递前复核"),
            ("You checked that the job is active. Recheck before applying."),
        ),
        (
            ("岗位有效性尚待核实"),
            ("Job availability still needs verification."),
        ),
        (
            ("城市与明确元/月薪资各占一半；缺失项不折算日薪、不补齐得分"),
            (
                "City and explicit CNY/month salary each account for half. Missing values are"
                " not filled in, and daily pay is not converted."
            ),
        ),
        (
            ("仅使用 JD 发布日期，导入日期不代表发布"),
            (
                "Only the JD publication date is used. Import date does not establish publica"
                "tion date."
            ),
        ),
        (
            ("仅计算当前有效的受信项目验收记录覆盖；缺少证据不代表已通过"),
            (
                "Only current trusted project acceptance records count toward coverage. Missi"
                "ng evidence does not mean passed."
            ),
        ),
        (
            ("无当前有效的受信项目验收证据，保持未知；材料/简历/面试不补分"),
            (
                "No current trusted project acceptance evidence. This remains unknown; materi"
                "als, resumes and interviews do not add points."
            ),
        ),
        (
            ("按可解释分项及明确反馈排序，资格未知单列待核实"),
            (
                "Ranked by explainable components and explicit feedback. Unknown eligibility "
                "is listed separately for review."
            ),
        ),
        (
            ("提供方向对比；尚未建立对个人兴趣的隐含推断模型"),
            ("Offers direction comparisons. No model infers your unstated personal interests."),
        ),
        (
            ("基于当前条件、基础证据与明确反馈的规则推荐；冷启动不宣称高度个性化。"),
            (
                "Rule recommendations use current constraints, foundational evidence and expl"
                "icit feedback. Cold-start results do not claim strong personalization."
            ),
        ),
        (
            ("喜欢 +5 / 不感兴趣 -20 / 误判 -10；已投递自记退出推荐但不改变投递状态"),
            (
                "Like +5 / not interested -20 / misclassified -10. A self-reported applicatio"
                "n removes a job from recommendations without changing application status."
            ),
        ),
        (
            ("按原文逐项比较；未知值不补齐，关闭或屏蔽的记录仍可明确选择作历史比较。"),
            (
                "Compare original fields without filling unknown values. You may explicitly s"
                "elect closed or blocked records for historical comparison."
            ),
        ),
        (
            ("词库基础学习分钟合计，不含项目、复习或求职准备，不预测达到岗位要求的时间"),
            (
                "Total foundational study minutes from the catalogue. Excludes projects, revi"
                "ew and application preparation; does not predict time to job readiness."
            ),
        ),
        (
            (
                "本人导入且未关闭/疑似失效/过期的 JD 中，非提及技能至少一半"
                "落在该路径；含尚未核实的岗位，样本可同时属于多条路径，不代表市场"
                "供需"
            ),
            (
                "Among your imported JDs that are not closed, possibly unavailable or expired"
                ", at least half of the non-mention skills belong to this path. Includes unve"
                "rified jobs; samples can belong to multiple paths and do not represent marke"
                "t supply or demand."
            ),
        ),
        (
            ("用户明确标记感兴趣"),
            ("You explicitly marked this direction as interesting."),
        ),
        (
            ("尚未表达方向兴趣"),
            ("You have not expressed interest in this direction."),
        ),
        (
            ("当前主目标"),
            ("Current primary goal."),
        ),
        (
            ("可作为备选方向"),
            ("Can be considered as an alternative direction."),
        ),
        (
            ("专业与方向的适配性、城市和资格条件需结合具体 JD 核实"),
            ("Check major fit, city and qualification constraints against specific JDs."),
        ),
        (
            ("不保证就业或薪资；小样本数量不能解释为市场需求"),
            (
                "No employment or salary guarantee. Small sample counts do not establish mark"
                "et demand."
            ),
        ),
        (
            (
                "按明确兴趣、主目标和基础补课成本排序。专业与自由文本偏好保留供核"
                "对，不作隐含资格推断。"
            ),
            (
                "Ranked by explicit interests, primary goal and foundational study cost. Majo"
                "r and free-text preferences are retained for review and do not imply qualifi"
                "cations."
            ),
        ),
        (
            ("仅增量检查本人已导入的本地岗位库；未搜索外部招聘平台，未实时读取源站。"),
            (
                "Incremental checks cover only your imported local job library. No external r"
                "ecruiting platform search or live source reading is performed."
            ),
        ),
    ]
)

PATTERNS = (
    (
        "有 (\\d+) 项基础必需技能待验证，不代表可保证补齐",
        "{0} required foundational skills need verification; closing the gaps is not guaranteed.",
    ),
    (
        "已识别 (\\d+) 项必需技能，条件范围较小，不代表保底机会",
        "{0} required skills identified. Fewer conditions do not mean a guaranteed opportunity.",
    ),
    ("(\\d+) 项基础技能有有效测验证据", "{0} foundational skills have valid assessment evidence."),
    ("(\\d+) 项基础技能仍需验证", "{0} foundational skills still need verification."),
)


def explanation(value):
    if value in TEXT:
        return TEXT[value]
    for pattern, template in PATTERNS:
        if match := re.fullmatch(pattern, value or ""):
            return template.format(*match.groups())
    return None


def _item(item):
    item["_ui"] = {
        "checks": {check["key"]: explanation(check["reason"]) for check in item["checks"]},
        "components": {
            key: explanation(component["basis"].get("reason"))
            for key, component in item["components"].items()
        },
        "mode_reason": explanation(item.get("mode_reason")),
    }


def present(value, locale, kind):
    """Decorate only response copies. Unknown explanations retain their original text."""
    if locale != "en-US":
        return value
    result = copy.deepcopy(value)
    if kind == "subscription":
        for item in result if isinstance(result, list) else [result]:
            item["_ui"] = {"scope": explanation(item.get("scope"))}
        return result
    if kind == "item":
        _item(result)
        return result
    result["_ui"] = {
        key: explanation(result.get(key)) for key in ("notice", "ranking_basis", "feedback_basis")
    }
    if kind == "compare":
        for job in result["jobs"]:
            _item(job["match"])
    elif kind == "career":
        for career in result["careers"]:
            career["_ui"] = {
                "cost_basis": explanation(career["cost_basis"]),
                "sample_basis": explanation(career["sample_basis"]),
                "reasons": [explanation(text) for text in career["reasons"]],
                "cautions": [explanation(text) for text in career["cautions"]],
            }
    else:
        for group in ("eligible", "needs_review", "excluded", "hidden"):
            for item in result.get(group, []):
                _item(item)
    return result
