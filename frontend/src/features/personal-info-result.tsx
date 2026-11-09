import { object, string, type Data, type Json } from "../shared/api";
import { useLabel, useText } from "../shared/ui";

export function PersonalInfoResult({ result }: { result: Data }) {
  const t = useText();
  const label = useLabel();
  const education: Record<string, string> = {
    high_school: t("高中", "High school"),
    associate: t("专科", "Associate"),
    bachelor: t("本科", "Bachelor"),
    master: t("硕士", "Master"),
    doctor: t("博士", "Doctorate"),
    other: t("其他", "Other"),
  };
  function display(value: Json | undefined, key = ""): string {
    if (value === null || value === undefined || value === "")
      return t("未填写", "Not provided");
    if (Array.isArray(value))
      return value.map((item) => display(item)).join("、");
    if (typeof value === "object")
      return string(object(value).value) || t("未填写", "Not provided");
    return key === "education"
      ? education[String(value)] || String(value)
      : String(value);
  }
  const experience = object(result.experience);
  const fields: [string, string, string][] = [
    ["title", "名称", "Title"],
    ["organization", "学校或机构", "School or organization"],
    ["role", "个人角色", "Your role"],
    ["start_date", "开始日期", "Start date"],
    ["end_date", "结束日期", "End date"],
    ["situation", "背景", "Situation"],
    ["task", "任务", "Task"],
    ["action", "个人行动", "Your actions"],
    ["result", "成果", "Result"],
  ];
  const kinds: Record<string, string> = {
    education: t("教育经历", "Education"),
    internship: t("实习经历", "Internship"),
    work: t("工作经历", "Work experience"),
    project: t("项目经历", "Project experience"),
  };
  return (
    <div className="assistant-result personal-info-result">
      <p className="personal-info-heading">
        {result.section === "profile_draft"
          ? t(
              "私人档案草稿 · 尚未生效",
              "Private profile draft · Not yet applied",
            )
          : result.section === "profile"
            ? t("基础档案", "Profile")
            : result.section === "experience"
              ? kinds[string(experience.kind)]
              : t("资料补充", "Profile note")}
      </p>
      {result.section === "profile" || result.section === "profile_draft" ? (
        <>
          <dl className="personal-info-fields">
            {Object.entries(object(result.changes)).map(([key, change]) => (
              <div key={key}>
                <dt>{label(key)}</dt>
                <dd>
                  {result.section === "profile_draft" && (
                    <span className="muted">
                      {t("正式档案：", "Official profile: ")}
                      {display(object(change).official, key)} ·{" "}
                      {t("原有值：", "Previous value: ")}
                    </span>
                  )}
                  <span className="muted">
                    {display(object(change).before, key)}
                  </span>
                  <span aria-hidden="true"> → </span>
                  <span className="sr-only">{t("修改为", "Change to")}</span>
                  <strong>{display(object(change).after, key)}</strong>
                  {key === "weekly_minutes" && t(" 分钟/周", " min/week")}
                </dd>
              </div>
            ))}
          </dl>
          {(result.needs_plan_review || result.plan_preview_id) && (
            <p className="muted">
              {t(
                "现有学习计划需要复核，保存档案不会自动替换计划。",
                "Your study plan needs review. Saving the profile will not replace the plan.",
              )}
            </p>
          )}
          {result.saved && result.plan_preview_id && (
            <a
              className="result-link"
              href={`/app/learn?record=${encodeURIComponent(string(result.plan_preview_id))}#plan-previews`}
            >
              {t("查看计划调整预览", "Review the plan changes")}
            </a>
          )}
        </>
      ) : result.section === "experience" ? (
        <dl className="personal-info-fields">
          {fields
            .filter(([key]) => {
              const value = experience[key];
              return (
                value && (typeof value !== "object" || object(value).value)
              );
            })
            .map(([key, zh, en]) => (
              <div key={key}>
                <dt>{t(zh, en)}</dt>
                <dd dir="auto">{display(experience[key])}</dd>
              </div>
            ))}
        </dl>
      ) : (
        <p className="personal-info-note" dir="auto">
          {string(result.text)}
        </p>
      )}
      {result.section !== "profile" && result.section !== "profile_draft" && (
        <small>
          {t(
            "本人确认的自述 · 未经独立验证",
            "Self reported and confirmed by you · Not independently verified",
          )}
        </small>
      )}
      {result.saved && (
        <a
          className="personal-info-link"
          href={
            result.section === "profile" || result.section === "profile_draft"
              ? "/app/profile"
              : `/app/profile?record=${encodeURIComponent(string(result.id))}#materials`
          }
        >
          {result.section === "profile_draft"
            ? t("草稿已保存 · 继续填写", "Draft saved · Continue editing")
            : t("已保存 · 查看我的资料", "Saved · View my information")}
        </a>
      )}
    </div>
  );
}
