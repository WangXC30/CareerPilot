"""Owned JD intersections and an explicitly editorial prerequisite projection."""

from careerpilot import assessment, catalogue, growth, learning, learning_resources
from careerpilot.domain import DomainError


def project(db, principal, job_ids):
    ids = list(dict.fromkeys(job_ids))
    if not 3 <= len(ids) <= 20:
        raise DomainError("JOB_SELECTION_REQUIRED", "请选择 3 至 20 个不同的岗位", 422)
    jobs = [growth.data(growth.owned(db, principal, key, "job")) for key in ids]
    if any(job.get("status") in {"closed", "merged"} for job in jobs):
        raise DomainError("INACTIVE_JOB", "已关闭或已合并的岗位不能参与当前对比", 422)
    graph = catalogue.skill_map(db)
    order = catalogue.topological(graph)
    sources: dict[str, list] = {}
    for job in jobs:
        for requirement in job["skills"]:
            key = requirement["skill_id"]
            if key in graph and requirement["requirement"] in {"required", "preferred"}:
                sources.setdefault(key, []).append(
                    {
                        "job_id": job["id"],
                        "job_version": job["version"],
                        "requirement": requirement["requirement"],
                        "evidence": requirement["evidence"],
                    }
                )
    selected = set(sources)
    ancestors: dict[str, list[str]] = {}
    levels: dict[str, int] = {}
    for key in order:
        parents = graph[key]["requires"]
        closure = {p for parent in parents for p in [parent, *ancestors[parent]]}
        ancestors[key] = [p for p in order if p in closure]
        levels[key] = max((levels[p] + 1 for p in parents), default=0)
        if key in sources:
            selected.update(closure)
    states = {s["skill_id"]: s for s in learning.snapshots(db, principal)}
    resources = learning_resources.entries(db, principal)
    nodes = []
    clusters: dict[tuple[str, ...], list[str]] = {}
    for key in order:
        if key not in selected:
            continue
        matches = [
            job_id for job_id in ids if any(s["job_id"] == job_id for s in sources.get(key, []))
        ]
        count = len(matches)
        category = (
            "common"
            if count == len(ids)
            else "shared"
            if count > 1
            else "unique"
            if count
            else "prerequisite"
        )
        nodes.append(
            {
                **states[key],
                "category": category,
                "job_ids": matches,
                "job_count": count,
                "sources": sources.get(key, []),
                "requires": graph[key]["requires"],
                "prerequisite_path": ancestors[key],
                "level": levels[key],
                "catalogue_version": graph[key]["version"],
                "dependency_basis": graph[key]["dependency_basis"],
                "resources": [r for r in resources if r["skill_id"] == key],
                "verification": {
                    "kind": "objective_quiz" if key in assessment.BANK else "unavailable",
                    "scope": "foundation",
                    "rubric_version": assessment.BANK_VERSION if key in assessment.BANK else None,
                    "notice": "基础知识测验；不代表完整岗位胜任力，学习记录不会升级为已验证",
                },
            }
        )
        if matches:
            clusters.setdefault(tuple(matches), []).append(key)
    return {
        "rule_version": "jd-intersection.v1",
        "jobs": [
            {
                "id": job["id"],
                "version": job["version"],
                "title": job["fields"]["title"]["value"] or "待核实岗位",
                "status": job["status"],
                "recognized_skill_ids": [
                    key
                    for key in order
                    if any(s["job_id"] == job["id"] for s in sources.get(key, []))
                ],
            }
            for job in jobs
        ],
        "nodes": nodes,
        "edges": [{"from": p, "to": n["skill_id"]} for n in nodes for p in n["requires"]],
        "clusters": [{"job_ids": list(ids), "skill_ids": keys} for ids, keys in clusters.items()],
        "notice": (
            "仅统计所选 JD 中已收录且标注为必需或优先的技能；仅提及、否定要求不计入，"
            "未识别不等于没有要求。共同指全部岗位，交叉指部分岗位，特有指一个岗位。"
            "先修路径为编辑整理，不计入 JD 频次。"
        ),
    }
