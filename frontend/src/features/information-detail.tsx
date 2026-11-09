import { type ReactNode } from "react";
import { array, object, string, type Data, type Json } from "../shared/api";
import { DataView, title, useLabel, useText, words } from "../shared/ui";
import { type Options } from "../shared/forms";

function hasContent(value: Json | undefined): boolean {
  if (value === null || value === undefined || value === "") return false;
  if (Array.isArray(value)) return value.some(hasContent);
  if (typeof value === "object") return Object.values(value).some(hasContent);
  return true;
}

export function informationTitle(item: Data) {
  const text = string(object(item.experience).title) || title(item);
  const firstLine = text.split(/\r?\n/, 1)[0];
  return firstLine.length > 90 ? `${firstLine.slice(0, 90)}…` : firstLine;
}

// Keep prose intact; presentation never rewrites the saved information.
export function InformationDetail({
  item,
  heading = "",
  options = {},
  omit = [],
}: {
  item: Data;
  heading?: string;
  options?: Options;
  omit?: string[];
}) {
  const t = useText();
  const label = useLabel();
  const used = new Set([
    "id",
    "user_id",
    "tenant_id",
    "record_kind",
    "structured_experience",
    "content_hash",
    "source_hash",
    "hash",
    "trace_id",
    "payload_json",
    ...omit,
  ]);
  const read = (key: string) => {
    used.add(key);
    return item[key];
  };
  const display = (value: Json | undefined): string => {
    if (Array.isArray(value))
      return value.map(display).filter(Boolean).join(t("、", ", "));
    if (typeof value === "object" && value !== null)
      return display(value.value);
    const text = string(value);
    if (text === "confirmed") return label("confirmed_status");
    return text && words[text] ? label(text) : text;
  };
  const context: string[] = [];
  const contextField = (key: string, withLabel = false) => {
    const text = display(read(key));
    if (text && text !== heading && !context.includes(text))
      context.push(withLabel ? `${label(key)}${t("：", ": ")}${text}` : text);
  };
  const sections: ReactNode[] = [];
  const seen = new Set<string>();
  const prose = (key: string, value: Json | undefined, caption?: string) => {
    const text = string(value);
    if (!text.trim() || seen.has(text)) return;
    seen.add(text);
    sections.push(
      <section className="information-section" key={key}>
        {caption && <h4>{caption}</h4>}
        <p className="information-prose" dir="auto">
          {text}
        </p>
      </section>,
    );
  };

  for (const key of ["title", "name", "filename"]) {
    const text = string(read(key));
    if (text && text !== heading) context.push(text);
  }
  for (const key of [
    "status",
    "source_status",
    "category",
    "skill_status",
    "stage",
    "kind",
  ])
    contextField(key, true);
  if (read("deleted") === true) context.push(t("已删除", "Deleted"));
  if (read("expired") === true) context.push(t("来源已过期", "Source expired"));
  if (read("revoked") === true) context.push(t("已撤回", "Withdrawn"));
  if (read("valid") === false) context.push(t("需要重新核对", "Needs review"));

  const experience = object(read("experience"));
  if (Object.keys(experience).length) {
    used.add("text"); // This is the generated copy of the experience fields.
    for (const key of ["kind", "organization", "role"])
      if (display(experience[key])) context.push(display(experience[key]));
    const dates = [experience.start_date, experience.end_date].map(display);
    if (dates.some(Boolean))
      context.push(
        dates.map((date) => date || t("未知", "Unknown")).join(" – "),
      );
    prose(
      "experience-title",
      experience.title === heading ? "" : experience.title,
    );
    prose("situation", experience.situation, t("背景", "Background"));
    prose("task", experience.task, t("承担任务", "Responsibilities"));
    prose("action", experience.action, t("个人行动", "Your contribution"));
    prose("result", experience.result, t("成果", "Outcome"));
  }

  const fields = object(read("fields"));
  const fieldValues: Data = {};
  for (const [key, value] of Object.entries(fields)) {
    const field = object(value);
    const content = Object.hasOwn(field, "value") ? field.value : value;
    if (hasContent(content) && field.state !== "unknown")
      fieldValues[key] = content!;
  }
  for (const [key, value] of Object.entries(fieldValues)) {
    const text = display(value);
    if (text && text !== heading)
      context.push(`${label(key)}${t("：", ": ")}${text}`);
  }
  const job = object(read("job"));
  const jobText = [job.title, job.company]
    .map(display)
    .filter(Boolean)
    .join(" · ");
  if (jobText)
    context.push(`${t("目标岗位", "Target job")}${t("：", ": ")}${jobText}`);
  for (const key of ["job_id", "plan_id", "skill_id", "job_ids"]) {
    const value = read(key);
    const values = Array.isArray(value) ? value : [value];
    const names = values
      .map((id) => options[key]?.find((option) => option.value === id)?.label)
      .filter(Boolean);
    if (names.length)
      context.push(`${label(key)}${t("：", ": ")}${names.join(t("、", ", "))}`);
    else used.delete(key);
  }
  for (const key of [
    "education",
    "major",
    "academic_year",
    "graduation_date",
    "preferred_cities",
    "date",
    "start_date",
    "end_date",
    "deadline",
  ])
    contextField(key, true);
  const period = object(item.period || object(item.report).period);
  if (period.start || period.end)
    context.push(
      [display(period.start), display(period.end)].filter(Boolean).join(" – "),
    );
  for (const key of ["minutes", "weekly_minutes", "budget_minutes"]) {
    const value = read(key);
    if (typeof value === "number")
      context.push(`${label(key)}${t("：", ": ")}${value} ${t("分钟", "min")}`);
  }
  if (hasContent(item.version_count)) {
    context.push(
      t(
        `共 ${item.version_count} 个简历版本`,
        `${item.version_count} resume versions`,
      ),
    );
    used.add("version_count");
  }

  for (const key of [
    "content",
    "text",
    "reflection",
    "summary",
    "description",
    "note",
    "outcome",
    "preferences",
    "instructions",
  ]) {
    const value = read(key);
    if (key !== "text" || !Object.keys(experience).length)
      prose(key, value, sections.length ? label(key) : undefined);
  }

  const template = object(item.template);
  prose(
    "project-instructions",
    template.instructions,
    t("项目说明", "Project brief"),
  );
  const narrative = object(item.narrative);
  if (narrative.status)
    context.push(
      `${t("成果陈述", "Outcome narrative")}${t("：", ": ")}${display(narrative.status)}`,
    );
  for (const key of ["situation", "task", "action", "result"])
    prose(`narrative-${key}`, narrative[key], label(key));
  prose("project-scope", template.scope_notice);
  for (const key of ["url", "source_url"]) {
    const url = string(item[key]);
    if (/^https?:\/\//i.test(url)) {
      used.add(key);
      sections.push(
        <p key={key} className="information-link">
          <a href={url}>{url}</a>
        </p>,
      );
    }
  }

  const facts = array(read("facts")).map(object);
  if (facts.length) {
    const kinds = [...new Set(facts.map((fact) => string(fact.kind)))];
    sections.push(
      <section className="information-section" key="facts">
        {kinds.map((kind) => (
          <section key={kind}>
            <h4>{label(kind || "facts")}</h4>
            {facts
              .filter((fact) => string(fact.kind) === kind)
              .map((fact, index) => (
                <p
                  className="information-prose"
                  dir="auto"
                  key={string(fact.id) || index}
                >
                  {string(fact.text)}
                  {fact.source_status && fact.source_status !== "current" ? (
                    <small className="information-verification">
                      {display(fact.source_status)}
                    </small>
                  ) : null}
                </p>
              ))}
          </section>
        ))}
      </section>,
    );
  }
  const tasks = array(read("tasks")).map(object);
  if (tasks.length)
    sections.push(
      <section className="information-section" key="tasks">
        <h4>{t("学习安排", "Study schedule")}</h4>
        <ul className="information-list">
          {tasks.map((task, index) => (
            <li key={string(task.id) || index}>
              <span>
                {string(task.title || task.name) ||
                  options.skill_id?.find(
                    (skill) => skill.value === task.skill_id,
                  )?.label ||
                  string(task.skill_id)}
              </span>
              <span className="muted">
                {[
                  display(task.date),
                  typeof task.minutes === "number"
                    ? `${task.minutes} ${t("分钟", "min")}`
                    : "",
                  display(task.status),
                ]
                  .filter(Boolean)
                  .join(" · ")}
              </span>
            </li>
          ))}
        </ul>
      </section>,
    );
  const verification = display(read("verification"));
  const supporting = Object.fromEntries(
    Object.entries(item).filter(
      ([key, value]) => !used.has(key) && hasContent(value),
    ),
  );
  if (supporting.template) {
    const {
      instructions: _instructions,
      scope_notice: _scope,
      ...details
    } = template;
    supporting.template = details;
  }
  if (supporting.narrative) {
    const {
      situation: _situation,
      task: _task,
      action: _action,
      result: _result,
      ...details
    } = narrative;
    supporting.narrative = details;
  }
  // Preserve source quotes, source versions and immutable snapshots on demand.
  const factSources = facts.map(
    ({ text: _text, kind: _kind, ...source }) => source,
  );
  for (const [key, value] of Object.entries({
    fields,
    job,
    facts: factSources,
  })) {
    if (hasContent(value)) supporting[key] = value;
  }

  return (
    <article className="information-detail">
      {context.length > 0 && (
        <p className="information-context">
          {[...new Set(context)].join(" · ")}
        </p>
      )}
      {sections}
      {verification && (
        <p className="information-verification">
          {label("verification")}
          {t("：", ": ")}
          {verification}
        </p>
      )}
      {Object.keys(supporting).length > 0 && (
        <details className="information-supporting">
          <summary>
            {t("来源与记录信息", "Sources and record information")}
          </summary>
          <DataView value={supporting} />
        </details>
      )}
    </article>
  );
}
