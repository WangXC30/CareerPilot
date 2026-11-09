import { type ReactNode } from "react";
import {
  array,
  number,
  object,
  string,
  type Data,
  type Json,
} from "../shared/api";
import type { Options } from "../shared/forms";
import { useLabel, useText } from "../shared/ui";
import { PersonalInfoResult } from "./personal-info-result";
import { HiringResult } from "./hiring-result";

const rows = (value: Json | undefined) => array(value).map(object);
export function hasAssistantResult(value: Json | undefined): boolean {
  const result = object(value);
  if (
    [
      "assistant_chat",
      "personal_info",
      "hiring",
      "job_import",
      "followup",
      "followup_review",
      "practice_checks",
    ].includes(string(result.kind))
  )
    return true;
  return (
    Array.isArray(result.skills) ||
    Array.isArray(result.tasks) ||
    Array.isArray(result.results) ||
    !!result.diff
  );
}

function ShortList({
  items,
  render,
}: {
  items: Data[];
  render: (item: Data, index: number) => ReactNode;
}) {
  const t = useText();
  return (
    <>
      <ul className="assistant-result-list">
        {items.slice(0, 5).map((item, index) => (
          <li key={string(item.id || item.skill_id) || index}>
            {render(item, index)}
          </li>
        ))}
      </ul>
      {items.length > 5 && (
        <details className="assistant-result-more">
          <summary>
            {t(`其余 ${items.length - 5} 项`, `${items.length - 5} more items`)}
          </summary>
          <ul className="assistant-result-list">
            {items.slice(5).map((item, index) => (
              <li key={string(item.id || item.skill_id) || index}>
                {render(item, index + 5)}
              </li>
            ))}
          </ul>
        </details>
      )}
    </>
  );
}

function safeLink(value: Json | undefined) {
  const href = string(value);
  if (href.startsWith("/app/") || href.startsWith("/app#")) return href;
  try {
    const url = new URL(href);
    return ["http:", "https:"].includes(url.protocol) ? url.href : undefined;
  } catch {
    return undefined;
  }
}

export function AssistantResult({
  value,
  references,
}: {
  value: Json;
  references: Options;
}) {
  const t = useText();
  const label = useLabel();
  const result = object(value);
  if (result.kind === "followup_review") {
    const before = object(result.before);
    const skills = rows(result.skills);
    return (
      <div className="assistant-result">
        <strong>{t("本人面试复盘", "Your interview reflection")}</strong>
        {before.summary && (
          <p>
            {t("原复盘：", "Previous reflection: ")}
            {string(before.summary)}
          </p>
        )}
        {rows(before.decisions).length > 0 && (
          <p>
            {t("原复习项：", "Previous study items: ")}
            {rows(before.decisions)
              .map(
                (item) =>
                  `${string(item.skill_id)} (${number(item.minutes)} ${t("分钟", "minutes")}) ${string(item.note)}`,
              )
              .join("；")}
          </p>
        )}
        <p>
          {t("复盘：", "Reflection: ")}
          {string(result.summary)}
        </p>
        {result.status && (
          <p>
            {t("复盘状态：", "Reflection status: ")}
            {label(string(result.status))}
          </p>
        )}
        {result.application && (
          <p>
            {t("关联申请：", "Linked application: ")}
            {string(object(object(result.application).job).title)} ·{" "}
            {string(object(result.application).resume_title)}
          </p>
        )}
        {skills.length > 0 && (
          <ul>
            {skills.map((skill, index) => (
              <li key={index}>
                {string(skill.name || skill.skill_id)} · {number(skill.minutes)}{" "}
                {t("分钟", "minutes")} {string(skill.note)}
              </li>
            ))}
          </ul>
        )}
        <p>
          {result.action === "plan_interview_review"
            ? t(
                "将生成计划调整预览；接受前不会修改当前计划。",
                "A plan preview will be created; the active plan changes only after acceptance.",
              )
            : t(
                "本人自述，不代表已验证能力。",
                "Self-reported, not verified ability.",
              )}
        </p>
        {result.saved && (
          <a
            className="result-link"
            href={`/app/career?record=${encodeURIComponent(string(result.id))}#interview-review`}
          >
            {t("已保存 · 查看复盘", "Saved · View reflection")}
          </a>
        )}
      </div>
    );
  }
  if (result.kind === "practice_checks") {
    const items = Array.isArray(result.items)
      ? rows(result.items)
      : rows(object(result.items).items);
    const states: Record<string, [string, string]> = {
      queued: ["等待检查", "Queued"],
      running: ["检查中", "Running"],
      passed: ["项目评测通过", "Project assessment passed"],
      user_failed: ["项目评测未通过", "Project assessment did not pass"],
      system_error: ["检查不可用", "Assessment unavailable"],
      timeout: ["检查超时", "Assessment timed out"],
      manual_review: ["需要人工查看", "Manual review needed"],
      cancelled: ["已取消", "Cancelled"],
    };
    return (
      <div className="assistant-result">
        <strong>
          {t("项目检查实际状态", "Actual project assessment status")}
        </strong>
        {items.length ? (
          <ul>
            {items.map((item) => (
              <li key={string(item.id)}>
                {t(
                  ...(states[string(item.status)] || [
                    "状态待核对",
                    "Status unknown",
                  ]),
                )}{" "}
                · {t("规则版本", "Test version")}: {string(item.test_version)}
                {rows(item.results).length > 0 && (
                  <ul>
                    {rows(item.results).map((entry, index) => (
                      <li key={index}>
                        {string(entry.id)}:{" "}
                        {entry.passed === true
                          ? t("通过", "Passed")
                          : t("未通过", "Not passed")}
                        {entry.reason && <> · {string(entry.reason)}</>}
                      </li>
                    ))}
                  </ul>
                )}
              </li>
            ))}
          </ul>
        ) : (
          <p>{t("暂无项目检查记录。", "No project assessments yet.")}</p>
        )}
        <p>
          {t(
            "项目评测结果不等于技能已验证。",
            "Project assessment does not verify a skill.",
          )}
        </p>
        <a className="result-link" href="/app/learn#learning-projects">
          {t("查看学习与项目", "View learning and projects")}
        </a>
      </div>
    );
  }
  if (result.kind === "followup") {
    const event = object(result.event);
    const job = object(result.job);
    return (
      <div className="assistant-result">
        <p>
          <strong>{t("本地面试安排", "Local interview appointment")}</strong>
        </p>
        {result.existing && (
          <p>
            {t(
              "相同时间的安排已存在，将复用该记录。",
              "An appointment at this time already exists and will be reused.",
            )}
          </p>
        )}
        <p>
          {string(job.title) || t("目标岗位", "Target job")} ·{" "}
          {string(job.company)} · {string(result.resume_title)}
        </p>
        <p>
          {string(event.date)}{" "}
          {string(event.time) || t("时间待定", "Time not specified")} ·{" "}
          {string(event.timezone)}
        </p>
        <p>
          {t("申请阶段：", "Application stage: ")}
          {label(string(result.before_status))} →{" "}
          {label(string(result.after_status))}
        </p>
        {event.preparation_note && (
          <p>
            {t("准备事项：", "Preparation: ")}
            {string(event.preparation_note)}
          </p>
        )}
        <p>
          {t(
            "保存在本地日程，不会自动同步或发送。",
            "Stored locally; no automatic sync or sending.",
          )}
        </p>
        {result.saved && (
          <a className="result-link" href="/app/career#interviews">
            {t("已保存 · 查看求职", "Saved · View career")}
          </a>
        )}
      </div>
    );
  }
  if (result.kind === "job_import")
    return (
      <div className="assistant-result">
        <p>
          {result.deduplicated === true
            ? t(
                "岗位原文已存在，已复用已有记录。",
                "This job already exists; the existing record was reused.",
              )
            : t("岗位原文已保存。", "Job description saved.")}
        </p>
        <a
          className="result-link"
          href={`/app/goals?record=${encodeURIComponent(string(result.id))}#jobs`}
        >
          {t("查看岗位", "View jobs")}
        </a>
      </div>
    );
  if (result.kind === "hiring") return <HiringResult result={result} />;
  if (result.kind === "personal_info")
    return <PersonalInfoResult result={result} />;
  if (result.kind === "assistant_chat")
    return (
      <>
        <div className="assistant-chat-results">
        {rows(result.tools)
          .filter(
            (tool) =>
              tool.status === "success" && hasAssistantResult(tool.result),
          )
          .map((tool, index) => (
            <AssistantResult
              key={index}
              value={tool.result!}
              references={references}
            />
          ))}
        </div>
        {rows(result.citations).length > 0 && (
          <details className="assistant-result-sources">
            <summary>{t("依据", "Sources")}</summary>
            <ul>
              {rows(result.citations).map((source) => (
                <li key={string(source.id)}>
                  <a href={safeLink(source.href)}>{string(source.label)}</a>
                </li>
              ))}
            </ul>
          </details>
        )}
      </>
    );
  const name = (skill: Data) =>
    string(skill.name) ||
    references.skill_id?.find((item) => item.value === skill.skill_id)?.label ||
    string(skill.skill_id);
  const state = (value: Json | undefined) => {
    const states: Record<string, [string, string]> = {
      verified: ["考核通过", "Assessment passed"],
      unassessed: ["待评估", "Not assessed"],
      self_report: ["本人自述", "Self reported"],
      supported: ["有学习记录", "Study recorded"],
      needs_reassessment: ["需要重新考核", "Reassessment needed"],
      pending: ["待开始", "Pending"],
      running: ["进行中", "In progress"],
      paused: ["已暂停", "Paused"],
      completed: ["学习已完成", "Study completed"],
      skipped: ["已跳过", "Skipped"],
    };
    const labels = states[string(value)];
    return labels ? t(...labels) : t("待确认", "Needs review");
  };
  const task = (item: Data) => (
    <>
      <span dir="auto">{string(item.title) || name(item)}</span>
      <small>
        {[
          string(item.date),
          number(item.minutes)
            ? t(`${item.minutes} 分钟`, `${item.minutes} min`)
            : "",
          state(item.status),
        ]
          .filter(Boolean)
          .join(" · ")}
      </small>
    </>
  );
  const sources = (skill: Data) => {
    const entries = rows(skill.sources);
    if (!entries.length) return null;
    return (
      <details className="assistant-result-sources">
        <summary>{t("依据", "Evidence")}</summary>
        <ul>
          {entries.map((source, index) => {
            const job = references.job_id?.find(
              (item) => item.value === source.job_id,
            );
            const heading =
              job?.label ||
              (source.career_id
                ? t("目标方向要求", "Career requirements")
                : source.prerequisite_of
                  ? t("先修技能要求", "Prerequisite requirement")
                  : t("岗位原文", "Job description"));
            return (
              <li key={index}>
                <span dir="auto">{heading}</span>
                {source.job_version && (
                  <small>
                    {t(
                      `版本 ${source.job_version}`,
                      `Version ${source.job_version}`,
                    )}
                  </small>
                )}
                {object(source.evidence).quote && (
                  <blockquote dir="auto">
                    {string(object(source.evidence).quote)}
                  </blockquote>
                )}
              </li>
            );
          })}
        </ul>
      </details>
    );
  };

  if (Array.isArray(result.results)) {
    const resources = rows(result.results);
    return (
      <div className="assistant-result">
        {resources.length ? (
          <ShortList
            items={resources}
            render={(resource) => {
              const href = safeLink(resource.url);
              return (
                <>
                  {href ? (
                    <a
                      href={href}
                      target={href.startsWith("http") ? "_blank" : undefined}
                      rel="noreferrer"
                    >
                      {string(resource.title) ||
                        t("学习资源", "Study resource")}
                    </a>
                  ) : (
                    <span>{string(resource.title)}</span>
                  )}
                </>
              );
            }}
          />
        ) : (
          <p className="muted">
            {t(
              "没有找到匹配的学习资源，可换一个技能关键词。",
              "No matching resources. Try another skill keyword.",
            )}
          </p>
        )}
      </div>
    );
  }
  if (result.diff) {
    const diff = object(result.diff),
      error = object(result.validation_error);
    const groups: [string, string, string][] = [
      ["added", "新增", "Added"],
      ["removed", "移除", "Removed"],
      ["changed", "调整", "Changed"],
    ];
    return (
      <div className="assistant-result">
        {result.status === "pending" &&
          result.reason === "InterviewReviewConfirmed" && (
            <p>
              {t(
                "计划调整预览已保存，尚未应用；接受前请核对任务。",
                "The plan preview is saved but not applied. Review the tasks before accepting.",
              )}
            </p>
          )}
        <p>
          {groups
            .map(([key, zh, en]) =>
              t(
                `${zh} ${array(diff[key]).length} 项`,
                `${array(diff[key]).length} ${en.toLowerCase()}`,
              ),
            )
            .join(" · ")}
        </p>
        {Object.keys(error).length > 0 && (
          <p role="alert">
            {string(error.message) ||
              t(
                "该预览尚未通过校验。",
                "This preview has not passed validation.",
              )}
          </p>
        )}
        {groups.map(
          ([key, zh, en]) =>
            rows(diff[key]).length > 0 && (
              <details key={key}>
                <summary>{t(zh, en)}</summary>
                <ShortList
                  items={rows(diff[key])}
                  render={(item) =>
                    key === "changed" ? (
                      <>
                        <span dir="auto">
                          {string(object(item.after).title)}
                        </span>
                        <small>
                          {string(object(item.before).date)} →{" "}
                          {string(object(item.after).date)}
                        </small>
                      </>
                    ) : (
                      task(item)
                    )
                  }
                />
              </details>
            ),
        )}
        <a
          className="result-link"
          href={
            result.id
              ? `/app/learn?record=${encodeURIComponent(string(result.id))}#plan-previews`
              : "/app/learn#plan-previews"
          }
        >
          {t("到学习计划核对变更", "Review changes in your study plan")}
        </a>
      </div>
    );
  }
  if (Array.isArray(result.tasks)) {
    const tasks = rows(result.tasks);
    const budget = number(object(result.validation).budget_minutes);
    return (
      <div className="assistant-result">
        {result.id && (result.current_status || result.status) === "draft" && (
          <p className="plan-save-status">
            {t(
              "已确认并保存为草稿 · 尚未发布",
              "Confirmed and saved as a draft · Not published",
            )}
          </p>
        )}
        {result.id && (result.current_status || result.status) === "active" && (
          <p className="plan-save-status">
            {t(
              "计划已发布，可以开始执行",
              "Plan published. You can start working on it",
            )}
          </p>
        )}
        {budget > 0 && (
          <p>{t(`每周预算 ${budget} 分钟`, `Weekly budget: ${budget} min`)}</p>
        )}
        <ShortList items={tasks} render={task} />
        <a
          className="result-link"
          href={
            result.id
              ? `/app/learn?record=${encodeURIComponent(string(result.id))}#plans`
              : "/app/learn#plans"
          }
        >
          {t(
            "打开完整计划并核对发布",
            "Open the full plan to review and publish",
          )}
        </a>
      </div>
    );
  }
  if (Array.isArray(result.skills)) {
    const progress = "active_plan" in result;
    const skills = rows(result.skills).filter(
      (skill) => !progress || skill.status !== "unassessed",
    );
    const plan = object(result.active_plan);
    return (
      <div className="assistant-result">
        {skills.length ? (
          <ShortList
            items={skills}
            render={(skill) => (
              <>
                <span dir="auto">{name(skill)}</span>
                <small>{state(skill.status)}</small>
                {!progress && sources(skill)}
              </>
            )}
          />
        ) : (
          <p className="muted">
            {t("暂无技能进展记录。", "No skill progress recorded yet.")}
          </p>
        )}
        {progress && (
          <p>
            {Object.keys(plan).length
              ? t(
                  `当前计划有 ${rows(plan.tasks).filter((item) => !["completed", "skipped"].includes(string(item.status))).length} 项任务待完成。`,
                  `${rows(plan.tasks).filter((item) => !["completed", "skipped"].includes(string(item.status))).length} tasks remain in your current plan.`,
                )
              : t("还没有已发布的学习计划。", "No published study plan yet.")}
          </p>
        )}
        <a
          className="result-link"
          href={progress ? "/app/learn#plans" : "/app/goals#jobs"}
        >
          {progress
            ? t("打开学习计划", "Open study plan")
            : t("到目标与岗位查看详情", "View details in Goals and jobs")}
        </a>
      </div>
    );
  }
  return null;
}
