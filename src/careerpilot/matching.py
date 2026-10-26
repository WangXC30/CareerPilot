"""V2 deterministic eligibility and evidence-based comparison rules."""

import re
from datetime import date, datetime
from zoneinfo import ZoneInfo

from careerpilot import catalogue, growth, job_policy, learning, project_evidence

RULE_VERSION = "matching.rules.v3"
SUPPORTED_RULES = {"matching.rules.v2", RULE_VERSION}
WEIGHT_VERSION = "matching.weights.v1"
WEIGHTS = {
    "required_skills": 35,
    "project_evidence": 25,
    "preferences": 20,
    "preferred_skills": 10,
    "freshness": 10,
}
DEFAULTS: dict[str, object] = {
    "cities_hard": True,
    "available_months": None,
    "work_authorizations": None,
    "qualifications": None,
    "preferred_career_ids": [],
}
NOTICE = "实验性解释分，不是录用概率。基础测验不代表完整岗位胜任力。"
CITY_NAMES = set(
    (
        "北京 上海 天津 重庆 广州 深圳 杭州 南京 苏州 成都 武汉 西安 长沙 合肥 郑州 济南 青岛 "
        "厦门 福州 大连 沈阳 长春 哈尔滨 石家庄 太原 南昌 南宁 昆明 贵阳 海口 宁波 无锡 东莞 "
        "佛山 珠海 惠州 中山 温州 绍兴 常州 南通 扬州 镇江 嘉兴 湖州 金华 台州 丽水 舟山 衢州 "
        "拉萨 兰州 西宁 银川 乌鲁木齐 呼和浩特"
    ).split()
)


def location_names(value, cities):
    tokens = [s.strip().removesuffix("市") for s in re.split(r"[/、,，]", value or "")]
    known = CITY_NAMES | {c.removesuffix("市") for c in cities}
    return tokens if tokens and all(s in known for s in tokens) else None


def constraints(db, principal):
    row = db.scalar(growth.owner_query(principal, "matching_constraints"))
    return growth.data(row) if row else {"version": 0, **DEFAULTS}


def context(db, principal):
    row = growth.profile(db, principal)
    return {
        "profile": growth.data(row) if row else {},
        "constraints": constraints(db, principal),
        "states": {s["skill_id"]: s for s in learning.snapshots(db, principal)},
        "goals": growth.goal_snapshot(db, principal),
        "preferences": job_policy.preferences(db, principal),
        "feedback": job_policy.feedback_map(db, principal),
        "project_evidence": project_evidence.current(db, principal),
        "policy": active_policy(db, principal),
        "catalogue": catalogue.skill_map(db),
    }


def check(key, state, reason, field, user_value):
    return {
        "key": key,
        "state": state,
        "reason": reason,
        "job_value": field.get("value"),
        "job_evidence": field.get("evidence"),
        "user_value": user_value,
    }


def education(value, actual):
    ranks = {"high_school": 0, "associate": 1, "bachelor": 2, "master": 3, "doctor": 4}
    if not value:
        return "UNKNOWN", "学历或档案未明确，请核实"
    value = re.sub(r"\s+", "", value)
    if value in {"不限", "无要求", "不限学历", "学历不限"}:
        return "PASS", "岗位明确不限学历"
    if actual not in ranks:
        return "UNKNOWN", "学历或档案未明确，请核实"
    match = re.fullmatch(r"(高中|专科|大专|本科|学士|硕士|博士)(及以上|以上)?", value)
    if not match:
        return "UNKNOWN", "学历条件存在偏好、并列或附加限制，请核实"
    required = {"高中": 0, "专科": 1, "大专": 1, "本科": 2, "学士": 2, "硕士": 3, "博士": 4}[
        match[1]
    ]
    return (
        ("PASS", "已确认学历达到最低要求")
        if ranks[actual] >= required
        else ("FAIL", "已确认学历未达到最低要求")
    )


def cohort_field(job):
    # A V1 single-year extraction must not conceal alternative years in the same clause.
    field = job["fields"]["graduation_cohort"]
    lines = [line for line in job["content"].splitlines() if "届" in line]
    if not lines:
        return field
    line = lines[0]
    start = job["content"].find(line)
    end = job["content"].rfind(lines[-1]) + len(lines[-1])
    line = job["content"][start:end]
    return {"value": line, "evidence": {"start": start, "end": end, "quote": line}}


def eligibility(job, ctx, today):
    profile, limits = ctx["profile"], ctx["constraints"]
    fields = job["fields"]
    result = []

    def add(key, state, reason, field=None, user_value=None):
        result.append(check(key, state, reason, field or fields.get(key, {}), user_value))

    state, reason = education(fields["education"]["value"], profile.get("education"))
    add("education", state, reason, user_value=profile.get("education"))
    field = cohort_field(job)
    raw = field["value"] or ""
    graduation = profile.get("graduation_date", "")[:4]
    match = re.fullmatch(r"(?:届别[：:]\s*)?(20\d{2})届(?:毕业生)?", raw.strip())
    if match and graduation:
        state = "PASS" if match[1] == graduation else "FAIL"
        reason = "毕业年份与岗位届别一致" if state == "PASS" else "毕业年份与岗位届别不符"
    elif re.fullmatch(r"(?:(?:届别[：:]\s*)?(?:不限届别|届别不限)|届别[：:]\s*不限)", raw.strip()):
        state, reason = "PASS", "岗位明确不限届别"
    else:
        state, reason = "UNKNOWN", "届别未明确或含并列/附加条件，请核实"
    add("graduation_cohort", state, reason, field, graduation or None)
    raw = fields["location"]["value"] or ""
    cities = profile.get("preferred_cities", [])
    locations = location_names(raw, cities)
    if not limits["cities_hard"]:
        state, reason = "PASS", "用户将城市设置为软偏好"
    elif locations is None or not cities:
        state, reason = "UNKNOWN", "地点未明确或无法与期望城市精确比较"
    else:
        state = (
            "PASS"
            if any(s in {c.removesuffix("市") for c in cities} for s in locations)
            else "FAIL"
        )
        reason = "工作地符合城市约束" if state == "PASS" else "工作地不在允许城市中"
    add("location", state, reason, user_value=cities)
    raw = fields["duration"]["value"] or ""
    available = limits["available_months"]
    match = re.fullmatch(r"(?:至少|不少于)?\s*(\d{1,2})\s*个?月(?:以上|及以上)?", raw.strip())
    if match and int(match[1]) > 0 and available is not None:
        state = "PASS" if available >= int(match[1]) else "FAIL"
        reason = "可实习时长达到要求" if state == "PASS" else "可实习时长不足"
    elif fields["employment_type"]["value"] in {"全职", "正式", "full-time"} and not raw:
        state, reason = "PASS", "明确全职岗位，实习时长不适用"
    else:
        state, reason = "UNKNOWN", "实习期限或可用时长未明确，请核实"
    add("duration", state, reason, user_value=available)
    for key, user_key in [
        ("work_authorization", "work_authorizations"),
        ("qualification", "qualifications"),
    ]:
        aliases = (
            "(?:工作许可|签证|签证资格)" if key == "work_authorization" else "(?:资格|资格证书)"
        )
        field = growth.span_field(job["content"], rf"^{aliases}[：:][ \t]*([^\r\n]+)")
        raw, actual = field["value"], limits[user_key]
        if raw in {"不限", "无要求", "不适用"}:
            state, reason = "PASS", "岗位明确无限制或不适用"
        elif (
            not raw
            or actual is None
            or re.search(r"优先|或|至少|之一|不要求|无需|持有|具备|[、,，/；;]", raw)
        ):
            state, reason = "UNKNOWN", "岗位或用户资格信息未明确，请核实"
        elif raw in actual:
            state, reason = "PASS", "用户已确认持有所列资格；仍需源站核验"
        elif not actual:
            state, reason = "FAIL", "用户已确认无所需资格"
        else:
            state, reason = "UNKNOWN", "资格名称未精确对应，请核实等效性与附加要求"
        add(key, state, reason, field, actual)
    deadline = fields["deadline"]["value"]
    if job["status"] in {"closed", "merged"}:
        state, reason = "FAIL", "岗位已关闭或合并，不再推荐申请"
    elif deadline and date.fromisoformat(deadline) < today:
        state, reason = "FAIL", "岗位已超过明确截止日期"
    elif job["status"] == "possibly_expired":
        state, reason = "UNKNOWN", "岗位可能失效，请核实来源"
    elif job["status"] == "verified":
        state, reason = "PASS", "用户核对为有效；仍应在投递前复核"
    else:
        state, reason = "UNKNOWN", "岗位有效性尚待核实"
    add("availability", state, reason, fields["deadline"], None)
    overall = (
        "FAIL"
        if any(r["state"] == "FAIL" for r in result)
        else ("UNKNOWN" if any(r["state"] == "UNKNOWN" for r in result) else "PASS")
    )
    return overall, result


def component(ratio, basis):
    return {"ratio": ratio, "basis": basis}


def active_policy(db, principal):
    import hashlib

    policy = next(
        (p for p in catalogue.entries(db, "matching_policy") if p["id"] == "matching-policy"), None
    )
    bucket = (
        int(hashlib.sha256(f"matching.v1:{principal.user_id}".encode()).hexdigest()[:8], 16) % 100
    )
    if policy and sum(policy["weights"].values()) == 100 and bucket < policy["rollout_percent"]:
        return {"weights": policy["weights"], "version": f"matching-policy:{policy['version']}"}
    return {"weights": WEIGHTS, "version": WEIGHT_VERSION}


def score(job, ctx, today):
    policy = ctx.get("policy", {"weights": WEIGHTS, "version": WEIGHT_VERSION})
    weights = policy["weights"]
    states, profile = ctx["states"], ctx["profile"]
    components = {}
    for category, key in [("required", "required_skills"), ("preferred", "preferred_skills")]:
        skills = sorted({s["skill_id"] for s in job["skills"] if s["requirement"] == category})
        verified = [s for s in skills if states.get(s, {}).get("status") == "verified"]
        components[key] = component(
            len(verified) / len(skills) if skills else None,
            {
                "skills": skills,
                "verified": verified,
                "denominator": len(skills),
                "scope": "foundation",
            },
        )
    components["project_evidence"] = project_evidence.component(
        job, ctx.get("project_evidence", [])
    )
    salary = job["fields"]["salary"]["value"] or ""
    salary_match = re.fullmatch(r"(\d+)(?:-(\d+))?元/月", salary.strip())
    expected = profile.get("expected_monthly_salary")
    salary_ratio = None
    if salary_match and expected is not None:
        low, high = int(salary_match[1]), int(salary_match[2] or salary_match[1])
        if high >= low:
            salary_ratio = 1.0 if low >= expected else 0.0 if high < expected else 0.5
    raw = job["fields"]["location"]["value"]
    cities = profile.get("preferred_cities", [])
    locations = location_names(raw, cities)
    city_ratio = (
        None
        if locations is None or not cities
        else float(any(c in {s.removesuffix("市") for s in cities} for c in locations))
    )
    parts = [p for p in [city_ratio, salary_ratio] if p is not None]
    components["preferences"] = component(
        sum(parts) / 2 if parts else None,
        {
            "city_ratio": city_ratio,
            "salary_ratio": salary_ratio,
            "expected_monthly_salary": expected,
            "salary": salary,
            "reason": "城市与明确元/月薪资各占一半；缺失项不折算日薪、不补齐得分",
        },
    )
    components["preferences"]["known_weight"] = weights["preferences"] / 2 * len(parts)
    published = job["fields"]["published_at"]["value"]
    try:
        age = (today - date.fromisoformat(published)).days if published else None
    except ValueError:
        age = None
    ratio = max(0.0, 1 - age / 90) if age is not None and age >= 0 else None
    components["freshness"] = component(
        ratio,
        {
            "published_at": published,
            "age_days": age,
            "reason": "仅使用 JD 发布日期，导入日期不代表发布",
        },
    )
    for key, value in components.items():
        value["weight"] = weights[key]
        value.setdefault("known_weight", weights[key] if value["ratio"] is not None else 0)
        value["points"] = round((value["ratio"] or 0) * weights[key], 2)
    return {
        "components": components,
        "score": round(sum(c["points"] for c in components.values()), 2),
        "known_weight": sum(c["known_weight"] for c in components.values()),
        "weight_version": policy["version"],
        "rule_version": RULE_VERSION,
        "notice": NOTICE,
    }


def catalogue_patterns(skills):
    return {
        key: "(?:"
        + "|".join(
            re.escape(a)
            for a in sorted({key, entry["name"], *entry["aliases"]}, key=lambda a: (-len(a), a))
        )
        + ")"
        for key, entry in skills.items()
    }


def validate_alias_regression(graph, key):
    """A small safety gate, not a claim of real-world extraction accuracy."""
    from careerpilot.domain import DomainError

    patterns = catalogue_patterns(graph)
    for alias in {key, graph[key]["name"], *graph[key]["aliases"]}:
        if not alias.strip() or alias != alias.strip() or any(ord(c) < 32 for c in alias):
            raise DomainError("INVALID_SKILL_ALIAS", "技能名称与别名须为无控制字符的非空文本", 422)
        for prefix, expected in (("要求：", "required"), ("不要求：", "mentioned")):
            extracted = growth.extract_job(prefix + alias, patterns)["skills"]
            if not any(
                s["skill_id"] == key
                and s["requirement"] == expected
                and s["evidence"]["quote"] == alias
                for s in extracted
            ):
                raise DomainError(
                    "SKILL_REGRESSION_FAILED", "别名的原文或否定语义回归失败，请核对后重提", 422
                )


def extract_catalogue_skills(text, graph):
    spans = growth.extract_job(text, catalogue_patterns(graph))["skills"]
    selected, furthest = [], -1
    for span in sorted(
        spans, key=lambda s: (s["evidence"]["start"], -s["evidence"]["end"], s["skill_id"])
    ):
        # React.js is one named skill occurrence, not an additional standalone JS.
        if span["evidence"]["end"] <= furthest:
            continue
        selected.append(span)
        furthest = span["evidence"]["end"]
    return selected


def evaluate(job, ctx, today, *, rule_version=None):
    rule = rule_version or RULE_VERSION
    if rule not in SUPPORTED_RULES:
        from careerpilot.domain import DomainError

        raise DomainError("RULE_UNAVAILABLE", "历史评分规则版本不可执行，保留原结果", 409)
    # V2 used imported skill spans. Keep that evaluator for existing private snapshots.
    # V3 resolves literal aliases using only the supplied (possibly frozen) catalogue.
    if rule == RULE_VERSION and "catalogue" in ctx:
        job = {
            **job,
            "skills": extract_catalogue_skills(job["content"], ctx["catalogue"]),
        }
    overall, checks = eligibility(job, ctx, today)
    details = [{**s, "state": ctx["states"].get(s["skill_id"])} for s in job["skills"]]
    return {
        "job_id": job["id"],
        "job_version": job["version"],
        "title": job["fields"]["title"]["value"] or "待核实岗位",
        "company": job["fields"]["company"]["value"],
        "sources": job["sources"],
        "eligibility": overall,
        "checks": checks,
        "skills": details,
        "high_match_eligible": overall == "PASS",
        **score(job, ctx, today),
        "rule_version": rule,
    }


def job_matches(db, principal, job_ids=None):
    ctx = context(db, principal)
    jobs = (
        [growth.data(growth.owned(db, principal, key, "job")) for key in dict.fromkeys(job_ids)]
        if job_ids
        else [growth.data(r) for r in db.scalars(growth.owner_query(principal, "job"))]
    )
    today = datetime.now(ZoneInfo(ctx["profile"].get("timezone", "Asia/Shanghai"))).date()
    groups: dict[str, list] = {"eligible": [], "needs_review": [], "excluded": [], "hidden": []}
    for job in jobs:
        item = evaluate(job, ctx, today)
        item["favorite"] = job["id"] in ctx["preferences"]["favorite_ids"]
        item["policy_blocked"] = job_policy.blocked(job, ctx["preferences"])
        if item["policy_blocked"]:
            item["high_match_eligible"] = False
        item["feedback"] = ctx["feedback"].get(job["id"])
        group = {"PASS": "eligible", "UNKNOWN": "needs_review", "FAIL": "excluded"}[
            item["eligibility"]
        ]
        groups["hidden" if item["policy_blocked"] else group].append(item)
    for items in groups.values():
        items.sort(key=lambda item: (-item["score"], item["job_id"]))
    return {
        **groups,
        "profile_version": ctx["profile"].get("version", 0),
        "constraints_version": ctx["constraints"]["version"],
        "preferences_version": ctx["preferences"]["version"],
        "goal_versions": {g["id"]: g["version"] for g in ctx["goals"]},
        "as_of": growth.utc_now(),
        "notice": NOTICE,
        "ranking_basis": "各组独立排序；未知与不符合不会进入明确符合列表",
    }


def recommendations(db, principal, mode):
    policy = job_policy.preferences(db, principal)
    result = job_matches(db, principal)
    disabled = mode not in policy["enabled_modes"]
    groups: dict[str, list] = {"eligible": [], "needs_review": []}
    for key in groups:
        for item in result[key]:
            required = item["components"]["required_skills"]["basis"]
            count = required["denominator"]
            missing = count - len(required["verified"])
            belongs = {
                "best": True,
                "stretch": 1 <= missing <= 2 and count > 0,
                "entry": 1 <= count <= 2,
                "explore": True,
            }[mode]
            if disabled or not belongs:
                continue
            feedback = item["feedback"]
            label = feedback["label"] if feedback and feedback["active"] else None
            if label == "applied":
                continue
            adjustment = job_policy.FEEDBACK.get(label or "", 0)
            groups[key].append(
                {
                    **item,
                    "feedback_adjustment": adjustment,
                    "ranking_score": round(max(0, min(100, item["score"] + adjustment)), 2),
                    "mode_reason": {
                        "best": "按可解释分项及明确反馈排序，资格未知单列待核实",
                        "stretch": f"有 {missing} 项基础必需技能待验证，不代表可保证补齐",
                        "entry": f"已识别 {count} 项必需技能，条件范围较小，不代表保底机会",
                        "explore": "提供方向对比；尚未建立对个人兴趣的隐含推断模型",
                    }[mode],
                }
            )
        groups[key].sort(key=lambda i: (-i["ranking_score"], -i["score"], i["job_id"]))
    return {
        **groups,
        "mode": mode,
        "disabled": disabled,
        "rule_version": "recommendation.v1",
        "preferences_version": policy["version"],
        "notice": "基于当前条件、基础证据与明确反馈的规则推荐；冷启动不宣称高度个性化。",
        "feedback_basis": "喜欢 +5 / 不感兴趣 -20 / 误判 -10；已投递自记退出推荐但不改变投递状态",
        "excluded_count": len(result["excluded"]),
        "hidden_count": len(result["hidden"]),
    }


def compare_jobs(db, principal, job_ids):
    jobs = [growth.data(growth.owned(db, principal, key, "job")) for key in job_ids]
    matches = job_matches(db, principal, job_ids)
    by_id = {
        i["job_id"]: i
        for key in ("eligible", "needs_review", "excluded", "hidden")
        for i in matches[key]
    }
    return {
        "jobs": [
            {
                "id": j["id"],
                "version": j["version"],
                "fields": j["fields"],
                "status": j["status"],
                "sources": j["sources"],
                "match": by_id[j["id"]],
            }
            for j in jobs
        ],
        "notice": "按原文逐项比较；未知值不补齐，关闭或屏蔽的记录仍可明确选择作历史比较。",
    }


def career_comparison(db, principal):
    ctx = context(db, principal)
    graph = catalogue.skill_map(db)
    careers = catalogue.entries(db, "career")
    jobs = [
        growth.data(r)
        for r in db.scalars(growth.owner_query(principal, "job"))
        if growth.data(r)["status"] not in {"closed", "merged", "possibly_expired"}
    ]
    today = datetime.now(ZoneInfo(ctx["profile"].get("timezone", "Asia/Shanghai"))).date()
    jobs = [
        j
        for j in jobs
        if not j["fields"]["deadline"]["value"]
        or date.fromisoformat(j["fields"]["deadline"]["value"]) >= today
    ]
    result = []
    for career in careers:
        required = set(career["skills"])

        def include_parents(key):
            for parent in graph[key]["requires"]:
                if parent not in required:
                    required.add(parent)
                    include_parents(parent)

        for key in list(required):
            include_parents(key)
        transferable = sorted(s for s in required if ctx["states"][s]["status"] == "verified")
        pending = sorted(required - set(transferable))
        minutes = sum(graph[s]["minutes"] for s in pending)
        budget = ctx["profile"].get("weekly_minutes", 0)
        samples = []
        for job in jobs:
            job_skills = {s["skill_id"] for s in job["skills"] if s["requirement"] != "mentioned"}
            overlap = job_skills & set(career["skills"])
            if job_skills and len(overlap) / len(job_skills) >= 0.5:
                samples.append(
                    {
                        "job_id": job["id"],
                        "job_version": job["version"],
                        "overlap_skills": sorted(overlap),
                        "sources": job["sources"],
                    }
                )
        interest = career["id"] in ctx["constraints"]["preferred_career_ids"]
        active = any(
            g["title"] == career["title"] and g["status"] == "active" for g in ctx["goals"]
        )
        result.append(
            {
                "career_id": career["id"],
                "career_version": career["version"],
                "title": career["title"],
                "source_url": career["source_url"],
                "reviewed_at": career["reviewed_at"],
                "stale": career["stale"],
                "interest": interest,
                "active_goal": active,
                "transferable_skills": [ctx["states"][s] for s in transferable],
                "pending_skills": [ctx["states"][s] for s in pending],
                "foundation_minutes": minutes,
                "minimum_budget_weeks": (minutes + budget - 1) // budget if budget else None,
                "cost_basis": (
                    "词库基础学习分钟合计，不含项目、复习或求职准备，不预测达到岗位要求的时间"
                ),
                "catalogue_versions": {s: graph[s]["version"] for s in sorted(required)},
                "sample_count": len(samples),
                "sample_denominator": len(jobs),
                "samples": samples,
                "sample_basis": (
                    "本人导入且未关闭/疑似失效/过期的 JD 中，非提及技能至少一半落在该路径；"
                    "含尚未核实的岗位，样本可同时属于多条路径，不代表市场供需"
                ),
                "reasons": [
                    "用户明确标记感兴趣" if interest else "尚未表达方向兴趣",
                    f"{len(transferable)} 项基础技能有有效测验证据",
                    "当前主目标" if active else "可作为备选方向",
                ],
                "cautions": [
                    f"{len(pending)} 项基础技能仍需验证",
                    "专业与方向的适配性、城市和资格条件需结合具体 JD 核实",
                    "不保证就业或薪资；小样本数量不能解释为市场需求",
                ],
                "background": {
                    "major": ctx["profile"].get("major"),
                    "preferences": ctx["profile"].get("preferences", ""),
                },
            }
        )
    result.sort(
        key=lambda c: (
            -int(c["interest"]),
            -int(c["active_goal"]),
            c["foundation_minutes"],
            c["career_id"],
        )
    )
    return {
        "careers": result,
        "as_of": growth.utc_now(),
        "profile_version": ctx["profile"].get("version", 0),
        "constraints_version": ctx["constraints"]["version"],
        "rule_version": "career-comparison.v1",
        "notice": (
            "按明确兴趣、主目标和基础补课成本排序。专业与自由文本偏好保留供核对，不作隐含资格推断。"
        ),
    }
