"""Fixed local cohort/unknown-data regression; never a claim of market accuracy."""

from datetime import date

from careerpilot import growth, matching


def run():
    day = date(2026, 10, 23)
    base = "岗位：测试\n地点：上海\n学历：本科\n2027届\n要求：Python\n截止日期：2026-11-01"
    groups = [
        ("cold_start", {}, "UNKNOWN"),
        ("known_cohort", {"education": "bachelor", "graduation_date": "2027-06-30"}, "UNKNOWN"),
        ("other_cohort", {"education": "bachelor", "graduation_date": "2028-06-30"}, "FAIL"),
        ("missing_cohort", {"education": "bachelor"}, "UNKNOWN"),
    ]
    results = []
    for group, profile, expected in groups:
        ctx = {
            "profile": profile,
            "constraints": matching.DEFAULTS,
            "states": {"python": {"status": "verified"}},
        }

        def evaluate(content):
            return matching.evaluate(
                {
                    "id": "fixture",
                    "version": 1,
                    "content": content,
                    "status": "verified",
                    "sources": [],
                    **growth.extract_job(content),
                },
                ctx,
                day,
            )

        exact = evaluate(base)
        assert exact["eligibility"] == expected, group
        unknown = evaluate(base.replace("2027届", "2027或2028届"))
        assert unknown["eligibility"] != "FAIL", group
        missing = evaluate(
            base.replace("截止日期：2026-11-01", "截止日期待核实").replace("2027届", "届别待核实")
        )
        assert missing["eligibility"] != "FAIL", group
        expired = evaluate(base.replace("2026-11-01", "2026-10-01"))
        assert expired["eligibility"] == "FAIL", group
        weaker = evaluate(base.replace("要求：Python", "要求：SQL"))
        assert exact["score"] > weaker["score"], group
        results.append(
            {"group": group, "cases": 5, "false_filters": 0, "verified_skill_order_correct": True}
        )
    return {
        "groups": results,
        "cases": 20,
        "rule_version": matching.RULE_VERSION,
        "scope": "固定合成样例；缺失/并列届别不误排除，真实过期仍拒绝，不代表实际招聘排序收益",
    }


if __name__ == "__main__":
    value = run()
    print(f"V4 matching: {value['cases']} checks, 4 groups, no unexpected filters")
