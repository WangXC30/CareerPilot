"""Small versioned foundation catalogue; editorial paths are labelled as such."""

import json
from datetime import datetime, timezone

from sqlalchemy import select, update

from careerpilot.domain import DomainError
from careerpilot.persistence import affected, utc_now
from careerpilot.v1_models import CatalogueEntry

SKILLS = [
    ("python", "Python", ["Python", "python"], [], 180, "https://docs.python.org/3/tutorial/"),
    ("git", "Git", ["Git", "git"], [], 90, "https://git-scm.com/book/en/v2"),
    (
        "sql",
        "SQL",
        ["SQL", "sql"],
        [],
        180,
        "https://www.postgresql.org/docs/current/tutorial.html",
    ),
    (
        "javascript",
        "JavaScript",
        ["JavaScript", "JS"],
        [],
        180,
        "https://developer.mozilla.org/en-US/docs/Learn_web_development",
    ),
    ("docker", "Docker", ["Docker"], ["git"], 120, "https://docs.docker.com/get-started/"),
    ("fastapi", "FastAPI", ["FastAPI"], ["python"], 180, "https://fastapi.tiangolo.com/tutorial/"),
    (
        "pytorch",
        "PyTorch",
        ["PyTorch", "torch"],
        ["python"],
        240,
        "https://docs.pytorch.org/tutorials/",
    ),
    ("react", "React", ["React", "React.js"], ["javascript"], 180, "https://react.dev/learn"),
]
CAREERS = [
    (
        "backend",
        "后端工程师",
        ["python", "sql", "git", "fastapi"],
        ["设计服务接口", "维护数据库与测试"],
        ["编写一个带测试的 API"],
        "15-1252.00",
    ),
    (
        "ai",
        "AI 应用工程师",
        ["python", "pytorch", "git"],
        ["将模型接入应用", "检查模型结果与质量"],
        ["实现可评估的模型应用"],
        "15-1252.00",
    ),
    (
        "data",
        "数据分析师",
        ["python", "sql"],
        ["整理数据与分析问题", "解释分析结果"],
        ["用 SQL 回答业务问题"],
        "15-2051.00",
    ),
    (
        "frontend",
        "前端工程师",
        ["javascript", "react", "git"],
        ["构建用户界面", "测试交互与可访问性"],
        ["交付可操作的表单页面"],
        "15-1252.00",
    ),
]


def unpack(row):
    payload = json.loads(row.payload_json)
    reviewed = datetime.fromisoformat(payload.get("reviewed_at", row.updated_at))
    return {
        "id": row.id,
        "version": row.version,
        "updated_at": row.updated_at,
        **payload,
        "stale": (datetime.now(timezone.utc) - reviewed).days > 180,
    }


def entries(db, kind):
    return [
        unpack(row)
        for row in db.scalars(
            select(CatalogueEntry).where(CatalogueEntry.kind == kind).order_by(CatalogueEntry.id)
        )
    ]


def skill_map(db):
    return {item["id"]: item for item in entries(db, "skill")}


def require_skill(db, skill_id):
    row = db.get(CatalogueEntry, skill_id)
    if row is None or row.kind != "skill":
        raise DomainError("UNKNOWN_SKILL", "技能尚未收录到当前词库", 422)
    return unpack(row)


def canonical(db, name):
    for skill in entries(db, "skill"):
        if name.casefold() in {a.casefold() for a in skill["aliases"] + [skill["id"]]}:
            return skill["id"]
    raise DomainError("UNKNOWN_SKILL", "技能别名尚未收录", 422)


def topological(skills):
    result, visiting, seen = [], set(), set()

    def visit(key):
        if key in visiting or key not in skills:
            raise DomainError("INVALID_DAG", "先修依赖存在环或未知节点", 422)
        if key in seen:
            return
        visiting.add(key)
        for dependency in skills[key]["requires"]:
            visit(dependency)
        visiting.remove(key)
        seen.add(key)
        result.append(key)

    for key in sorted(skills):
        visit(key)
    return result


def update_dependencies(db, skill_id, requires, version):
    # Serialize graph edits so individually valid concurrent changes cannot introduce a cycle.
    db.execute(
        update(CatalogueEntry)
        .where(CatalogueEntry.id == "python")
        .values(version=CatalogueEntry.version)
    )
    graph = skill_map(db)
    if skill_id not in graph:
        raise DomainError("NOT_FOUND", "技能不存在", 404)
    graph[skill_id]["requires"] = requires
    topological(graph)
    row = db.get(CatalogueEntry, skill_id)
    payload = json.loads(row.payload_json)
    payload["requires"] = requires
    result = db.execute(
        update(CatalogueEntry)
        .where(CatalogueEntry.id == skill_id, CatalogueEntry.version == version)
        .values(version=version + 1, payload_json=json.dumps(payload), updated_at=utc_now())
    )
    if affected(result) != 1:
        raise DomainError("VERSION_CONFLICT", "词库已更新", 412)


def seed_catalogue(db):
    reviewed = "2026-10-20T00:00:00+00:00"
    for key, name, aliases, requires, minutes, url in SKILLS:
        if db.get(CatalogueEntry, key) is None:
            db.add(
                CatalogueEntry(
                    id=key,
                    kind="skill",
                    payload_json=json.dumps(
                        {
                            "name": name,
                            "aliases": aliases,
                            "requires": requires,
                            "minutes": minutes,
                            "industry": "software",
                            "source_url": url,
                            "reviewed_at": reviewed,
                            "scope": "foundation",
                            "dependency_basis": "CareerPilot editorial learning path",
                        }
                    ),
                )
            )
        resource_id = "resource-" + key
        if db.get(CatalogueEntry, resource_id) is None:
            db.add(
                CatalogueEntry(
                    id=resource_id,
                    kind="resource",
                    payload_json=json.dumps(
                        {
                            "skill_id": key,
                            "title": name + " 官方学习入口",
                            "url": url,
                            "type": "course" if key in {"pytorch", "react"} else "documentation",
                            "language": "en",
                            "source": "official_documentation",
                            "updated_at_source": None,
                            "reviewed_at": reviewed,
                            "status": "available",
                            "alternative": "重新检索官方来源或使用测验查找薄弱点",
                        }
                    ),
                )
            )
    for key, title, skills, duties, tasks, source_id in CAREERS:
        if db.get(CatalogueEntry, "career-" + key) is None:
            db.add(
                CatalogueEntry(
                    id="career-" + key,
                    kind="career",
                    payload_json=json.dumps(
                        {
                            "title": title,
                            "family": "计算机与数据",
                            "skills": skills,
                            "duties": duties,
                            "typical_tasks": tasks,
                            "entry_requirements": "以具体 JD 为准，学历与届别不得由百科推断",
                            "source_url": "https://www.onetonline.org/link/summary/" + source_id,
                            "reviewed_at": reviewed,
                            "trust": "editorial_summary",
                            "region": "general",
                        }
                    ),
                )
            )
    extras = [
        ("resource-git-video", "git", "Git 官方视频入口", "video", "https://git-scm.com/videos"),
        (
            "resource-pytorch-project",
            "pytorch",
            "PyTorch 官方教程源码",
            "open_source",
            "https://github.com/pytorch/tutorials",
        ),
        (
            "resource-sql-practice",
            "sql",
            "SQL 查询练习（基于官方教程示例）",
            "exercise",
            "https://www.postgresql.org/docs/current/tutorial-sql.html",
        ),
        (
            "resource-git-book",
            "git",
            "Pro Git 在线章节",
            "article",
            "https://git-scm.com/book/en/v2",
        ),
    ]
    for key, skill, title, kind, url in extras:
        if db.get(CatalogueEntry, key) is None:
            db.add(
                CatalogueEntry(
                    id=key,
                    kind="resource",
                    payload_json=json.dumps(
                        {
                            "skill_id": skill,
                            "title": title,
                            "url": url,
                            "type": kind,
                            "language": "en",
                            "source": "official_documentation",
                            "updated_at_source": None,
                            "reviewed_at": reviewed,
                            "status": "available",
                            "alternative": "返回该技能的官方文档入口",
                        }
                    ),
                )
            )
