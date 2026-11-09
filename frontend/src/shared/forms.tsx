import { useContext, useEffect, useId, useRef, useState } from "react";
import contracts from "./contracts.json";
import {
  ApiError,
  array,
  command,
  currentWorkspace,
  number,
  object,
  request,
  string,
  type Data,
  type Json,
} from "./api";
import { cacheDraft, cachedDraft } from "./drafts";
import { Action, DataView, Feedback, useLabel, useText } from "./ui";

type Schema = {
  $ref?: string;
  type?: string;
  title?: string;
  enum?: Json[];
  const?: Json;
  default?: Json;
  anyOf?: Schema[];
  items?: Schema;
  properties?: Record<string, Schema>;
  propertyNames?: { enum?: string[] };
  additionalProperties?: Schema | boolean;
  required?: string[];
  format?: string;
  minLength?: number;
  maxLength?: number;
  minimum?: number;
  maximum?: number;
  minItems?: number;
  maxItems?: number;
};
const schemas = contracts as Record<string, Schema>;
export type Option = { value: string; label: string };
export type Options = Record<string, Option[]>;
function resolve(schema: Schema): Schema {
  if (schema.$ref)
    return resolve(schemas[schema.$ref.split("/").pop() || ""] || {});
  if (schema.anyOf)
    return {
      ...schema,
      ...resolve(schema.anyOf.find((x) => x.type !== "null") || {}),
    };
  return schema;
}
function initial(schema: Schema): Data {
  const result: Data = {};
  for (const [key, value] of Object.entries(resolve(schema).properties || {})) {
    const field = resolve(value);
    if (field.default !== undefined && field.default !== null)
      result[key] = field.default;
    else if (field.type === "array") result[key] = [];
  }
  return result;
}

function Field({
  name,
  schema: raw,
  value,
  onChange,
  required,
  options,
  prefix = "",
  hint,
  placeholder,
  displayLabel,
}: {
  name: string;
  schema: Schema;
  value?: Json;
  onChange: (value: Json) => void;
  required: boolean;
  options: Options;
  prefix?: string;
  hint?: string;
  placeholder?: string;
  displayLabel?: string;
}) {
  const schema = resolve(raw);
  const label = useLabel();
  const t = useText();
  const fieldLabel = displayLabel || label(name);
  const id = `field-${prefix}-${name}`;
  const choices = options[name];
  if (schema.type === "object" || schema.properties) {
    const fields =
      schema.properties ||
      Object.fromEntries(
        (schema.propertyNames?.enum || []).map((key) => [
          key,
          typeof schema.additionalProperties === "object"
            ? schema.additionalProperties
            : { type: "string" },
        ]),
      );
    return (
      <fieldset>
        <legend>{fieldLabel}</legend>
        {Object.entries(fields).map(([key, field]) => (
          <Field
            key={key}
            name={key}
            schema={field}
            value={object(value)[key]}
            required={!!schema.required?.includes(key)}
            onChange={(next) => onChange({ ...object(value), [key]: next })}
            options={options}
            prefix={`${prefix}-${name}`}
          />
        ))}
      </fieldset>
    );
  }
  if (schema.type === "array") {
    const values = array(value);
    if (choices)
      return (
        <fieldset>
          <legend>
            {fieldLabel}
            {required && " *"}
          </legend>
          {hint && <p className="field-hint">{hint}</p>}
          {choices.length ? (
            choices.map((choice) => (
              <label className="check" key={choice.value}>
                <input
                  type="checkbox"
                  checked={values.includes(choice.value)}
                  onChange={(e) =>
                    onChange(
                      e.target.checked
                        ? [...values, choice.value]
                        : values.filter((x) => x !== choice.value),
                    )
                  }
                />
                {choice.label}
              </label>
            ))
          ) : (
            <p className="muted">
              {t(
                "暂无可选资料，请先在相关页面添加。",
                "No records available. Add information on the related page first.",
              )}
            </p>
          )}
        </fieldset>
      );
    return (
      <fieldset>
        <legend>
          {fieldLabel}
          {required && " *"}
        </legend>
        {values.map((item, index) => (
          <div className="array-item" key={index}>
            <Field
              name={name}
              schema={schema.items || { type: "string" }}
              value={item}
              onChange={(next) =>
                onChange(values.map((v, i) => (i === index ? next : v)))
              }
              required={true}
              options={options}
              prefix={`${prefix}-${index}`}
            />
            <button
              type="button"
              className="subtle"
              aria-label={t("删除此项", "Remove item")}
              onClick={() => onChange(values.filter((_, i) => i !== index))}
            >
              ×
            </button>
          </div>
        ))}
        <button
          type="button"
          className="secondary"
          disabled={values.length >= (schema.maxItems || 100)}
          onClick={() =>
            onChange([
              ...values,
              resolve(schema.items || {}).type === "object" ||
              resolve(schema.items || {}).properties
                ? initial(schema.items || {})
                : "",
            ])
          }
        >
          {name === "tags"
            ? t("添加标签", "Add tag")
            : t("添加一项", "Add item")}
        </button>
      </fieldset>
    );
  }
  if (schema.type === "boolean" || typeof schema.const === "boolean")
    return (
      <label className="check" htmlFor={id}>
        <input
          id={id}
          type="checkbox"
          checked={value === true}
          required={schema.const === true}
          onChange={(e) => onChange(e.target.checked)}
        />
        {fieldLabel}
      </label>
    );
  if (schema.enum || choices)
    return (
      <label htmlFor={id}>
        {fieldLabel}
        {required && " *"}
        <select
          id={id}
          aria-label={fieldLabel}
          aria-describedby={hint ? `${id}-hint` : undefined}
          value={string(value)}
          required={required}
          onChange={(e) =>
            onChange(
              schema.type === "integer"
                ? Number(e.target.value)
                : e.target.value,
            )
          }
        >
          <option value="">{t("请选择", "Select")}</option>
          {choices
            ? choices.map((x) => (
                <option key={x.value} value={x.value}>
                  {x.label}
                </option>
              ))
            : schema.enum?.map((x) => (
                <option key={string(x)} value={string(x)}>
                  {label(string(x))}
                </option>
              ))}
        </select>
        {hint && (
          <span className="field-hint" id={`${id}-hint`}>
            {hint}
          </span>
        )}
      </label>
    );
  const common = {
    id,
    required,
    value: string(value),
    minLength: schema.minLength,
    maxLength: schema.maxLength,
    placeholder,
    "aria-describedby": hint ? `${id}-hint` : undefined,
    onChange: (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) =>
      onChange(
        schema.type === "integer" || schema.type === "number"
          ? e.target.value === ""
            ? null
            : Number(e.target.value)
          : e.target.value,
      ),
  };
  if (
    (schema.maxLength || 0) > 300 ||
    [
      "content",
      "text",
      "summary",
      "note",
      "message",
      "code",
      "situation",
      "task",
      "action",
      "result",
    ].includes(name)
  )
    return (
      <label htmlFor={id}>
        {fieldLabel}
        {required && " *"}
        <textarea
          {...common}
          rows={name === "content" || name === "code" ? 8 : 3}
          dir="auto"
        />
        {hint && (
          <span className="field-hint" id={`${id}-hint`}>
            {hint}
          </span>
        )}
      </label>
    );
  return (
    <label htmlFor={id}>
      {fieldLabel}
      {required && " *"}
      <input
        {...common}
        type={
          schema.format === "date"
            ? "date"
            : schema.format === "date-time"
              ? "datetime-local"
              : schema.type === "integer" || schema.type === "number"
                ? "number"
                : "text"
        }
        min={schema.minimum}
        max={schema.maximum}
        dir="auto"
      />
      {hint && (
        <span className="field-hint" id={`${id}-hint`}>
          {hint}
        </span>
      )}
    </label>
  );
}

export function CommandForm({
  schemaName,
  path,
  method = "POST",
  version,
  defaults = {},
  options = {},
  onDone,
  label: buttonLabel,
  draft,
  transform,
}: {
  schemaName: string;
  path: string;
  method?: string;
  version?: number;
  defaults?: Data;
  options?: Options;
  onDone?: (value: Json) => void;
  label?: string;
  draft?: { kind: string; key: string };
  transform?: (value: Data) => Data;
}) {
  const t = useText();
  const schema = resolve(schemas[schemaName] || {});
  const prefix = useId();
  const notify = useContext(Feedback);
  const [values, setValues] = useState<Data>(() => ({
    ...initial(schema),
    ...defaults,
  }));
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState("");
  const [result, setResult] = useState<Json>(null);
  const [recovery, setRecovery] = useState<Data>();
  const [remote, setRemote] = useState<Data>();
  const [step, setStep] = useState(0);
  const [impact, setImpact] = useState<Json>(null);
  const form = useRef<HTMLFormElement>(null);
  const staged = schemaName === "ConfirmProfile";
  const groups = [
    ["education", "major", "academic_year", "graduation_date"],
    [
      "preferred_cities",
      "expected_monthly_salary",
      "weekly_minutes",
      "preferences",
      "timezone",
    ],
    ["confirmed"],
  ];
  // Present common workflows in the order people complete them, independent of API field order.
  const formSections: Record<
    string,
    { title: [string, string]; fields: string[] }[]
  > = {
    PlanInput: [
      { title: ["目标岗位", "Target jobs"], fields: ["job_ids"] },
      {
        title: ["时间安排", "Schedule"],
        fields: ["start_date", "deadline", "weekly_minutes"],
      },
    ],
    LogInput: [
      { title: ["学习投入", "Study time"], fields: ["day", "minutes", "note"] },
      {
        title: ["关联计划（可选）", "Related plan (optional)"],
        fields: ["plan_id", "task_id"],
      },
      { title: ["确认", "Confirmation"], fields: ["confirmed"] },
    ],
    ResumeCreate: [
      { title: ["简历信息", "Resume details"], fields: ["title", "job_id"] },
      { title: ["选择事实", "Select facts"], fields: ["fact_ids"] },
      { title: ["确认", "Confirmation"], fields: ["confirmed"] },
    ],
    careerpilot__hiring_api__ApplicationInput: [
      {
        title: ["申请资料", "Application details"],
        fields: ["job_id", "resume_version_id"],
      },
      { title: ["确认", "Confirmation"], fields: ["confirmed"] },
    ],
    ExternalApplicationInput: [
      {
        title: ["投递信息", "Submission details"],
        fields: ["job_id", "resume_description", "submitted_at", "note"],
      },
      { title: ["确认", "Confirmation"], fields: ["confirmed"] },
    ],
    NotificationInput: [
      {
        title: ["提醒状态", "Reminder status"],
        fields: ["status", "timezone"],
      },
      {
        title: ["提醒时间", "Schedule"],
        fields: [
          "hour",
          "weekday",
          "month_day",
          "quiet_start",
          "quiet_end",
          "daily_limit",
        ],
      },
      {
        title: ["提醒内容", "Reminder types"],
        fields: [
          "daily_learning",
          "deadline_check",
          "weekly_report",
          "monthly_report",
          "spaced_review",
        ],
      },
      { title: ["确认", "Confirmation"], fields: ["confirmed"] },
    ],
    PracticeInput: [
      {
        title: ["练习内容", "Practice details"],
        fields: ["kind", "title", "difficulty", "tags"],
      },
      {
        title: ["结果与复盘", "Outcome and reflection"],
        fields: ["outcome", "practiced_on", "minutes", "reflection"],
      },
      { title: ["确认", "Confirmation"], fields: ["confirmed"] },
    ],
  };
  const hints: Record<string, Record<string, [string, string]>> = {
    PlanInput: {
      job_ids: [
        "选择已有岗位，计划将参考岗位要求。",
        "Choose saved jobs to use their requirements in the plan.",
      ],
      deadline: [
        "可留空；有明确期限时填写。",
        "Optional; enter a date if you have a deadline.",
      ],
      weekly_minutes: [
        "每周可用于学习的分钟数；已有档案预算时可留空。",
        "Minutes available each week; optional when your profile already has a budget.",
      ],
    },
    LogInput: {
      note: [
        "可简要记录学习内容或成果。",
        "Optionally note what you studied or accomplished.",
      ],
      plan_id: [
        "可选择这次学习所属的计划。",
        "Optionally choose the related study plan.",
      ],
      task_id: [
        "先选择计划，再选择其中的任务；也可不关联。",
        "Choose a plan first, then optionally select a task.",
      ],
    },
    ResumeCreate: {
      title: [
        "用容易识别的名称区分不同版本。",
        "Use a recognizable name to distinguish resumes.",
      ],
      job_id: [
        "可选择目标岗位，或留空创建通用简历。",
        "Optionally select a target job, or leave blank for a general resume.",
      ],
      fact_ids: [
        "选择已确认且来源有效的事实，至少选择一项。",
        "Select at least one confirmed fact with a current source.",
      ],
    },
    ExternalApplicationInput: {
      resume_description: [
        "可记录外部简历的名称或版本；不会导入简历文件。",
        "Optionally record the external resume name or version; no file is imported.",
      ],
      submitted_at: [
        "可留空；填写时使用本机时间。",
        "Optional; enter a time in your local time zone.",
      ],
    },
    NotificationInput: {
      timezone: [
        "使用时区名称，例如 Asia/Shanghai。提醒仅在站内显示。",
        "Use a time zone name such as Asia/Shanghai. Reminders appear in the app.",
      ],
      weekday: ["选择每周报告的提醒日。", "Choose the day for the weekly report."],
      quiet_start: [
        "免打扰开始时间，按所选时区的整点填写。",
        "Quiet hours start at this hour in the selected time zone.",
      ],
      quiet_end: [
        "免打扰结束时间，按所选时区的整点填写。",
        "Quiet hours end at this hour in the selected time zone.",
      ],
    },
  };
  const key = useRef(crypto.randomUUID());
  const guard = useRef(false);
  const draftVersion = useRef(0);
  const base = useRef(version || 0);
  const cacheKey = `${currentWorkspace()}:${draft?.kind}:${draft?.key}`;
  const draftPath = draft ? `/drafts/${draft.kind}/${draft.key}` : "";
  useEffect(() => {
    if (!draftPath) return;
    let active = true;
    Promise.all([
      request(draftPath),
      cachedDraft(cacheKey).catch(() => undefined),
    ])
      .then(([saved, cached]) => {
        if (!active) return;
        const data = object(saved);
        draftVersion.current = number(data.version);
        if (cached && Object.keys(cached).length) setRecovery(cached);
        else if (Object.keys(object(data.values)).length)
          setRecovery(object(data.values));
      })
      .catch((error: Error) => {
        if (active) setFeedback(error.message);
      });
    return () => {
      active = false;
    };
  }, [draftPath, cacheKey]);

  function edit(name: string, value: Json) {
    const next = { ...values, [name]: value };
    if (
      schemaName === "LogInput" &&
      name === "plan_id" &&
      value !== values.plan_id
    )
      next.task_id = "";
    setValues(next);
    key.current = crypto.randomUUID();
    setFeedback("");
    if (draft)
      cacheDraft(cacheKey, next).catch(() =>
        setFeedback(
          t(
            "浏览器暂存不可用，请保存草稿。",
            "Browser storage is unavailable. Save the draft.",
          ),
        ),
      );
  }
  function body() {
    const data: Data = {};
    for (const [name, val] of Object.entries(values)) {
      if (
        schema.properties?.[name] &&
        !(val === "" && !schema.required?.includes(name))
      )
        data[name] = val;
    }
    if (data.source_type === "manual") {
      delete data.source_id;
      delete data.source_version;
      delete data.profile_field;
    }
    if (data.source_type === "memory") {
      delete data.text;
      delete data.profile_field;
    }
    if (data.source_type === "profile") {
      delete data.text;
      delete data.source_id;
    }
    if (typeof data.submitted_at === "string" && data.submitted_at)
      data.submitted_at = new Date(data.submitted_at).toISOString();
    return transform ? transform(data) : data;
  }
  async function saveDraft() {
    try {
      const data = object(
        await command(
          draftPath,
          { values, base_record_version: base.current },
          "PUT",
          draftVersion.current,
        ),
      );
      draftVersion.current = number(data.version);
      setFeedback(
        t(
          "草稿已保存，尚未确认或生效。",
          "Draft saved. It is not confirmed or applied.",
        ),
      );
      await cacheDraft(cacheKey);
    } catch (error) {
      if (error instanceof ApiError && error.status === 412)
        setRemote(object(await request(draftPath)));
      throw error;
    }
  }
  return (
    <form
      ref={form}
      className="command-form"
      onSubmit={async (e) => {
        e.preventDefault();
        if (staged && step < 2) return;
        if (guard.current) return;
        const missingSelection =
          schemaName === "ResumeCreate" &&
          Object.entries(schema.properties || {}).find(([name, field]) => {
            const resolved = resolve(field);
            return (
              resolved.type === "array" &&
              (resolved.minItems || 0) > array(values[name]).length
            );
          });
        if (missingSelection) {
          const message = t(
            "请先选择或添加必需的内容。",
            "Select or add the required items first.",
          );
          setFeedback(message);
          notify(message);
          return;
        }
        guard.current = true;
        setBusy(true);
        setFeedback("");
        try {
          const output = await command(
            path,
            body(),
            method,
            version,
            key.current,
          );
          setResult(output);
          setFeedback(t("已保存", "Saved"));
          notify(t("已保存", "Saved"));
          if (draftPath && draftVersion.current)
            await request(draftPath, {
              method: "DELETE",
              headers: { "If-Match": `"${draftVersion.current}"` },
            }).catch(() => {});
          if (draft) await cacheDraft(cacheKey).catch(() => {});
          onDone?.(output);
        } catch (error) {
          const message =
            error instanceof Error
              ? error.message
              : t("操作失败", "Action failed");
          setFeedback(message);
          notify(message);
        } finally {
          setBusy(false);
          guard.current = false;
        }
      }}
    >
      {recovery && (
        <aside className="notice">
          <p>{t("发现未完成的草稿。", "An unfinished draft is available.")}</p>
          <button
            type="button"
            onClick={() => {
              setValues((current) => ({ ...current, ...recovery }));
              setRecovery(undefined);
            }}
          >
            {t("恢复编辑", "Restore editing")}
          </button>
          <button
            type="button"
            className="secondary"
            onClick={() => setRecovery(undefined)}
          >
            {t("暂不恢复", "Later")}
          </button>
        </aside>
      )}
      {remote && (
        <aside className="notice">
          <p>
            {t(
              "草稿已被其他页面修改。你的输入已保留，请比较后决定。",
              "Another page changed this draft. Your input is preserved; compare before saving.",
            )}
          </p>
          <DataView value={remote.values} />
          <button
            type="button"
            onClick={() => {
              setValues(object(remote.values));
              draftVersion.current = number(remote.version);
              setRemote(undefined);
            }}
          >
            {t("使用已保存版本", "Use saved version")}
          </button>
          <button
            type="button"
            className="secondary"
            onClick={() => {
              draftVersion.current = number(remote.version);
              setRemote(undefined);
            }}
          >
            {t("保留我的输入并重新保存", "Keep my input and save again")}
          </button>
        </aside>
      )}
      {staged && (
        <div
          className="subtabs"
          aria-label={t("档案填写步骤", "Profile steps")}
        >
          {[
            ["学习情况", "Education"],
            ["约束与预算", "Constraints and budget"],
            ["核对与确认", "Review and confirm"],
          ].map(([zh, en], index) => (
            <span
              className={step === index ? "current-step" : "muted"}
              key={index}
            >
              {index + 1}. {t(zh, en)}
            </span>
          ))}
        </div>
      )}
      {staged && step === 2 && <DataView value={impact} />}
      <fieldset disabled={busy} className="form-fields">
        {(
          formSections[schemaName] || [
            {
              title: ["", ""] as [string, string],
              fields: Object.keys(schema.properties || {}),
            },
          ]
        ).map((section, sectionIndex) => {
          const fields = section.fields
            .map((name) => [name, schema.properties?.[name]] as const)
            .filter((entry): entry is readonly [string, Schema] => !!entry[1])
            .filter(([name]) => {
              if (staged && !groups[step].includes(name)) return false;
              if (
                ["manual", "memory", "profile"].includes(
                  string(values.source_type),
                )
              ) {
                if (name === "text" && values.source_type !== "manual")
                  return false;
                if (name === "source_id" && values.source_type !== "memory")
                  return false;
                if (
                  name === "source_version" &&
                  values.source_type === "manual"
                )
                  return false;
                if (
                  name === "profile_field" &&
                  values.source_type !== "profile"
                )
                  return false;
              }
              return !["require_confirmation"].includes(name);
            });
          if (!fields.length) return null;
          return (
            <section
              className={section.title[0] ? "form-section" : undefined}
              key={sectionIndex}
            >
              {section.title[0] && <h3>{t(...section.title)}</h3>}
              {fields.map(([name, field]) => {
                const help = hints[schemaName]?.[name];
                const fieldOptions: Options =
                  schemaName === "LogInput" && name === "task_id"
                    ? {
                        ...options,
                        task_id:
                          options[`plan_tasks:${string(values.plan_id)}`] || [],
                      }
                    : { ...options };
                if (schemaName === "NotificationInput" && name === "weekday") {
                  fieldOptions.weekday = [
                    ["周一", "Monday"],
                    ["周二", "Tuesday"],
                    ["周三", "Wednesday"],
                    ["周四", "Thursday"],
                    ["周五", "Friday"],
                    ["周六", "Saturday"],
                    ["周日", "Sunday"],
                  ].map(([zh, en], index) => ({
                    value: String(index),
                    label: t(zh, en),
                  }));
                }
                if (schemaName === "NotificationInput" && name === "status") {
                  fieldOptions.status = [
                    { value: "active", label: t("启用", "Enabled") },
                    { value: "paused", label: t("暂停", "Paused") },
                    { value: "cancelled", label: t("取消", "Cancelled") },
                  ];
                }
                return (
                  <Field
                    key={name}
                    displayLabel={
                      schemaName === "LogInput" && name === "day"
                        ? t("学习日期", "Study date")
                        : undefined
                    }
                    name={name}
                    schema={field}
                    prefix={prefix}
                    required={!!schema.required?.includes(name)}
                    value={values[name]}
                    options={fieldOptions}
                    hint={help ? t(...help) : undefined}
                    onChange={(val) => edit(name, val)}
                  />
                );
              })}
            </section>
          );
        })}
      </fieldset>
      <div className="actions">
        {(!staged || step === 2) && (
          <button disabled={busy}>
            {busy
              ? t("处理中…", "Working…")
              : buttonLabel || t("确认保存", "Confirm and save")}
          </button>
        )}
        {staged && step > 0 && (
          <button
            type="button"
            className="secondary"
            disabled={busy}
            onClick={() => setStep(step - 1)}
          >
            {t("上一步", "Previous")}
          </button>
        )}
        {staged && step < 2 && (
          <Action
            run={async () => {
              if (!form.current?.reportValidity())
                throw new Error(
                  t(
                    "请补齐当前步骤的必填信息。",
                    "Complete the required fields in this step.",
                  ),
                );
              if (step === 1) {
                const payload = body();
                delete payload.confirmed;
                setImpact(await command("/profile/preview", payload));
              }
              setStep(step + 1);
            }}
          >
            {t("下一步", "Next")}
          </Action>
        )}
        {draft && (
          <Action className="secondary" run={saveDraft}>
            {t("保存私人草稿", "Save private draft")}
          </Action>
        )}
      </div>
      <p role="status">{feedback}</p>
      {result !== null && !onDone && <DataView value={result} />}
    </form>
  );
}
