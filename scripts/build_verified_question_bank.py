"""Build the frozen, executable Python and SQLite practice bank.

The exercises are original, parameterized practice problems.  Each answer is
checked against both execution and a separate reference calculation before the
bank is written.  No external question content is copied.
"""

import hashlib
import json
import random
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "src" / "careerpilot" / "generated_foundation_v4.json"
FAMILIES = 10
VARIANTS = 500


def python_problem(family, n):
    x = n + 10
    a, b = n % 11 + 2, n % 7 + 2
    values = [x, x + a, x + b, x + a]
    if family == 0:
        expression = f"sum(range({x}, {x + a}))"
        answer = a * (2 * x + a - 1) // 2
        explanation = "range 包含起点、不包含终点；对连续整数求和。"
    elif family == 1:
        expression = f"len(range({x}, {x + a}, {b}))"
        answer = (a + b - 1) // b
        explanation = "range 从起点按步长递增，终点不计入。"
    elif family == 2:
        expression = f"sum({values}[1:3])"
        answer = values[1] + values[2]
        explanation = "切片包含起点索引，不包含终点索引。"
    elif family == 3:
        expression = f"{values}.count({x + a})"
        answer = 2 + int(a == b)
        explanation = "count 统计列表中与给定值相等的元素数量。"
    elif family == 4:
        expression = f"len(set({values}))"
        answer = 2 if a == b else 3
        explanation = "set 去除相等元素后再计算长度。"
    elif family == 5:
        expression = f"({x} // {b}) * {b} + {x} % {b}"
        answer = x
        explanation = "整数除法的商和余数满足 x = (x // b) * b + x % b。"
    elif family == 6:
        expression = f"sum(v for v in {values} if v % 2 == 0)"
        answer = sum(v for v in values if v % 2 == 0)
        explanation = "先筛选能被 2 整除的元素，再求和。"
    elif family == 7:
        expression = f"max({values}) - min({values})"
        answer = max(a, b)
        explanation = "最大值减去最小值；此列表最小值是首项。"
    elif family == 8:
        expression = f"sum(v - {x} for v in {values})"
        answer = 2 * a + b
        explanation = "分别将每个元素减去基准值，再累加。"
    else:
        expression = f"len([v for v in {values} if v > {x + b}])"
        answer = 2 if a > b else 0
        explanation = "只统计严格大于指定阈值的元素，等于阈值不计入。"
    actual = eval(
        expression,
        {
            "__builtins__": {
                "sum": sum,
                "range": range,
                "len": len,
                "set": set,
                "max": max,
                "min": min,
            }
        },
        {},
    )
    if actual != answer or type(actual) is not int:
        raise ValueError(f"Python oracle mismatch: {expression}")
    return f"Python 3 中表达式 `{expression}` 的值是？", answer, explanation


def sql_problem(db, family, n):
    x = n + 10
    a, b = n % 11 + 2, n % 7 + 2
    values = [x, x + a, x + b, x + a]
    source = "WITH t(v) AS (VALUES " + ", ".join(f"({v})" for v in values) + ") "
    if family == 0:
        query, answer = "SELECT SUM(v) FROM t", 4 * x + 2 * a + b
        explanation = "SUM 对选中行的数值求和，重复值也参与计算。"
    elif family == 1:
        query, answer = f"SELECT COUNT(*) FROM t WHERE v > {x + b}", 2 if a > b else 0
        explanation = "WHERE 先筛选严格大于阈值的行，COUNT(*) 再统计行数。"
    elif family == 2:
        query, answer = "SELECT COUNT(DISTINCT v) FROM t", 2 if a == b else 3
        explanation = "COUNT(DISTINCT v) 统计不同的非 NULL 值。"
    elif family == 3:
        query, answer = "SELECT MAX(v) - MIN(v) FROM t", max(a, b)
        explanation = "MAX 与 MIN 分别取最大值和最小值。"
    elif family == 4:
        query, answer = f"SELECT SUM(v - {x}) FROM t", 2 * a + b
        explanation = "先逐行减去基准值，再对结果求和。"
    elif family == 5:
        query, answer = f"SELECT COUNT(*) FROM t WHERE v = {x + a}", 2 + int(a == b)
        explanation = "相等条件会匹配所有相同值的行，重复行不会自动去重。"
    elif family == 6:
        query, answer = (
            "SELECT SUM(CASE WHEN v % 2 = 0 THEN 1 ELSE 0 END) FROM t",
            sum(v % 2 == 0 for v in values),
        )
        explanation = "CASE 对偶数行计 1，其余计 0，然后求和。"
    elif family == 7:
        query, answer = f"SELECT COALESCE(MAX(v), {x}) FROM t WHERE v < {x}", x
        explanation = "没有符合条件的行时 MAX 返回 NULL，COALESCE 使用给定默认值。"
    elif family == 8:
        query, answer = (
            f"SELECT COUNT(*) FROM (SELECT v FROM t WHERE v >= {x + b})",
            1 + 2 * int(a >= b),
        )
        explanation = "子查询先保留大于或等于阈值的行，外层再统计。"
    else:
        query, answer = "SELECT SUM(v % 2) FROM t", sum(v % 2 for v in values)
        explanation = "各行对 2 取余后求和，结果等于奇数行数。"
    statement = source + query
    actual = db.execute(statement).fetchone()[0]
    if actual != answer or type(actual) is not int:
        raise ValueError(f"SQLite oracle mismatch: {statement}")
    return f"在 SQLite 中执行 `{statement}`，返回的单个数值是？", answer, explanation


def build():
    bank = {"python": [], "sql": []}
    seen = set()
    with sqlite3.connect(":memory:") as db:
        for skill in bank:
            for family in range(FAMILIES):
                for n in range(VARIANTS):
                    prompt, answer, explanation = (
                        python_problem(family, n)
                        if skill == "python"
                        else sql_problem(db, family, n)
                    )
                    if prompt in seen:
                        raise ValueError(f"Duplicate question: {prompt}")
                    seen.add(prompt)
                    choices = [answer, answer + 1, answer + 2, answer + 3]
                    random.Random(f"foundation.v4:{skill}:{family}:{n}").shuffle(choices)
                    bank[skill].append(
                        [prompt, [str(v) for v in choices], choices.index(answer), explanation]
                    )
    return bank


def main():
    bank = build()
    payload = json.dumps(bank, ensure_ascii=False, separators=(",", ":")) + "\n"
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(payload, encoding="utf-8", newline="\n")
    digest = hashlib.sha256(payload.encode()).hexdigest()
    print(f"Built {sum(map(len, bank.values()))} checked exercises; SHA-256 {digest}")


if __name__ == "__main__":
    main()
