import { useState } from "react";
import {
  array,
  command,
  number,
  object,
  string,
  type Data,
} from "../shared/api";
import type { Options } from "../shared/forms";
import { Action, DataView, useText } from "../shared/ui";

type Task = {
  id: string;
  title: string;
  skill_id: string;
  minutes: number;
  date: string;
  requires: string[];
  resources: string[];
  definition_of_done: string;
};

export function PlanEditor({
  plan,
  options,
  onDone,
  onCancel,
}: {
  plan: Data;
  options: Options;
  onDone: () => void;
  onCancel: () => void;
}) {
  const t = useText();
  const original = array(plan.tasks).map(object);
  const [title, setTitle] = useState(
    string(plan.title || plan.name) || t("学习计划", "Study plan"),
  );
  const [start, setStart] = useState(string(plan.start_date));
  const [deadline, setDeadline] = useState(string(plan.deadline));
  const [tasks, setTasks] = useState<Task[]>(() =>
    original.map((task) => ({
      id: string(task.id),
      title: string(task.title),
      skill_id: string(task.skill_id),
      minutes: number(task.minutes),
      date: string(task.date),
      requires: array(task.requires).map(string),
      resources: array(task.resources).map(string),
      definition_of_done: string(task.definition_of_done),
    })),
  );
  const [preview, setPreview] = useState<Data>();
  const readableTask = (value: Data) => ({
    title: value.title,
    skill_id:
      options.skill_id?.find((skill) => skill.value === value.skill_id)
        ?.label || value.skill_id,
    date: value.date,
    minutes: value.minutes,
    requires: array(value.requires).map(
      (id) =>
        tasks.find((task) => task.id === id)?.title ||
        original.find((task) => task.id === id)?.title ||
        t("已移除任务", "Removed task"),
    ),
    resources: value.resources,
    definition_of_done: value.definition_of_done,
  });
  const protectedIds = new Set(
    original
      .filter(
        (task) =>
          task.locked ||
          ["completed", "running", "skipped"].includes(string(task.status)),
      )
      .map((task) => string(task.id)),
  );
  const protect = (id: string) => {
    for (const parent of array(
      original.find((task) => task.id === id)?.requires,
    ).map(string))
      if (!protectedIds.has(parent)) {
        protectedIds.add(parent);
        protect(parent);
      }
  };
  for (const id of [...protectedIds]) protect(id);
  const change = (id: string, fields: Partial<Task>) =>
    setTasks((current) =>
      current.map((task) => (task.id === id ? { ...task, ...fields } : task)),
    );
  return (
    <section
      className="plan-editor"
      aria-label={t("编辑学习计划", "Edit study plan")}
    >
      <p>
        {t(
          "修改先预览再保存。已完成、进行中、锁定及其先修任务保留；发布仍需单独确认。",
          "Preview changes before saving. Completed, running, locked and prerequisite tasks are preserved. Publishing requires a separate confirmation.",
        )}
      </p>
      <fieldset disabled={!!preview}>
        <label>
          {t("计划名称", "Plan name")}
          <input
            value={title}
            maxLength={200}
            onChange={(event) => setTitle(event.target.value)}
          />
        </label>
        <label>
          {t("开始日期", "Start date")}
          <input
            type="date"
            value={start}
            onChange={(event) => setStart(event.target.value)}
          />
        </label>
        <label>
          {t("截止日期（可留空）", "Deadline (optional)")}
          <input
            type="date"
            value={deadline}
            onChange={(event) => setDeadline(event.target.value)}
          />
        </label>
        {tasks.map((task, index) => (
          <fieldset
            key={task.id}
            className="plan-editor-task"
            disabled={protectedIds.has(task.id)}
          >
            <legend>
              {t(`任务 ${index + 1}`, `Task ${index + 1}`)}
              {protectedIds.has(task.id) ? t(" · 已保护", " · Protected") : ""}
            </legend>
            <label>
              {t("任务名称", "Task name")}
              <input
                value={task.title}
                maxLength={200}
                onChange={(event) =>
                  change(task.id, { title: event.target.value })
                }
              />
            </label>
            <label>
              {t("关联技能", "Related skill")}
              <select
                aria-label={t("关联技能", "Related skill")}
                value={task.skill_id}
                onChange={(event) =>
                  change(task.id, { skill_id: event.target.value })
                }
              >
                <option value="">{t("请选择技能", "Select a skill")}</option>
                {(options.skill_id || []).map((skill) => (
                  <option key={skill.value} value={skill.value}>
                    {skill.label}
                  </option>
                ))}
              </select>
            </label>
            <label>
              {t("安排日期", "Scheduled date")}
              <input
                type="date"
                value={task.date}
                onChange={(event) =>
                  change(task.id, { date: event.target.value })
                }
              />
            </label>
            <label>
              {t("计划分钟数", "Planned minutes")}
              <input
                type="number"
                min={1}
                max={10080}
                value={task.minutes}
                onChange={(event) =>
                  change(task.id, { minutes: Number(event.target.value) })
                }
              />
            </label>
            <label>
              {t("先修任务", "Prerequisite tasks")}
              <select
                aria-label={t("先修任务", "Prerequisite tasks")}
                multiple
                value={task.requires}
                onChange={(event) =>
                  change(task.id, {
                    requires: [...event.target.selectedOptions].map(
                      (option) => option.value,
                    ),
                  })
                }
              >
                {tasks
                  .filter((other) => other.id !== task.id)
                  .map((other) => (
                    <option key={other.id} value={other.id}>
                      {other.title || t("未命名任务", "Untitled task")}
                    </option>
                  ))}
              </select>
            </label>
            <label>
              {t("学习资源（每行一项）", "Learning resources (one per line)")}
              <textarea
                value={task.resources.join("\n")}
                onChange={(event) =>
                  change(task.id, { resources: event.target.value.split("\n") })
                }
              />
            </label>
            <label>
              {t("完成标准", "Completion criteria")}
              <textarea
                maxLength={2000}
                value={task.definition_of_done}
                onChange={(event) =>
                  change(task.id, { definition_of_done: event.target.value })
                }
              />
            </label>
            <button
              type="button"
              className="secondary"
              onClick={() =>
                setTasks((current) =>
                  current.filter((item) => item.id !== task.id),
                )
              }
            >
              {t("删除任务", "Delete task")}
            </button>
          </fieldset>
        ))}
        <button
          type="button"
          className="secondary"
          disabled={tasks.length >= 200}
          onClick={() =>
            setTasks((current) => [
              ...current,
              {
                id: crypto.randomUUID(),
                title: "",
                skill_id: "",
                minutes: 30,
                date: start,
                requires: [],
                resources: [],
                definition_of_done: "",
              },
            ])
          }
        >
          {t("新增任务", "Add task")}
        </button>
      </fieldset>
      {!preview ? (
        <Action
          run={async () => {
            if (!title.trim() || !start)
              throw new Error(
                t(
                  "请填写计划名称和开始日期。",
                  "Enter a plan name and start date.",
                ),
              );
            if (
              tasks.some(
                (task) =>
                  !task.title.trim() ||
                  !task.skill_id ||
                  !task.date ||
                  task.minutes < 1 ||
                  !task.definition_of_done.trim() ||
                  !task.resources.some((resource) => resource.trim()),
              )
            )
              throw new Error(
                t(
                  "请补齐任务名称、技能、日期、时长、资源和完成标准。",
                  "Complete task names, skills, dates, duration, resources and completion criteria.",
                ),
              );
            setPreview(
              object(
                await command(
                  `/plans/${plan.id}/edit-preview`,
                  {
                    title,
                    start_date: start,
                    deadline: deadline || null,
                    tasks: tasks.map((task) => ({
                      ...task,
                      resources: task.resources
                        .map((resource) => resource.trim())
                        .filter(Boolean),
                    })),
                  },
                  "POST",
                  number(plan.version),
                ),
              ),
            );
          }}
        >
          {t("预览修改", "Preview changes")}
        </Action>
      ) : (
        <section aria-label={t("计划修改预览", "Plan change preview")}>
          <DataView
            value={{
              ...object(preview.metadata_changes),
              added: array(object(preview.diff).added).map((value) =>
                readableTask(object(value)),
              ),
              removed: array(object(preview.diff).removed).map((value) =>
                readableTask(object(value)),
              ),
              changed: array(object(preview.diff).changed)
                .map(object)
                .filter((change) => change.before && change.after)
                .map((change) => ({
                  before: readableTask(object(change.before)),
                  after: readableTask(object(change.after)),
                })),
              budget_minutes: object(object(preview.candidate).validation)
                .budget_minutes,
            }}
          />
          <Action
            run={() =>
              command(
                `/plan-previews/${preview.id}/accept`,
                { confirmed: true },
                "POST",
                number(preview.version),
              )
            }
            onDone={onDone}
          >
            {t("确认保存计划修改", "Confirm plan changes")}
          </Action>
          <Action
            className="subtle"
            run={async () => {
              await command(
                `/plan-previews/${preview.id}/reject`,
                { confirmed: true },
                "POST",
                number(preview.version),
              );
              setPreview(undefined);
            }}
          >
            {t("返回修改", "Back to editing")}
          </Action>
        </section>
      )}
      {!preview && (
        <button type="button" className="subtle" onClick={onCancel}>
          {t("取消修改", "Cancel editing")}
        </button>
      )}
    </section>
  );
}
