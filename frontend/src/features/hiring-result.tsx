import { array, object, string, type Data } from "../shared/api";
import { useText } from "../shared/ui";

export function HiringResult({ result }: { result: Data }) {
  const t = useText();
  const job = object(result.job);
  const headings: Record<string, string> = {
    create_resume_fact: t("求职事实", "Career fact"),
    create_resume_facts: t("求职事实", "Career facts"),
    create_resume: t("创建简历", "Create resume"),
    revise_resume: t("修改简历", "Revise resume"),
    tailor_resume: t("岗位定制简历", "Tailor resume"),
    draft_material: t("求职材料草稿", "Application material draft"),
    create_application: t(
      "申请草稿 · 尚未投递",
      "Application draft · Not submitted",
    ),
    check_resume: t("简历检查", "Resume check"),
  };
  const kinds: Record<string, string> = {
    name: t("姓名", "Name"),
    contact: t("联系方式", "Contact"),
    summary: t("个人简介", "Summary"),
    education: t("教育背景", "Education"),
    experience: t("实践经历", "Experience"),
    project: t("项目经历", "Projects"),
    skill: t("技能与证书", "Skills and certificates"),
  };
  const issues: Record<string, string> = {
    NAME_MISSING: t("请确认并加入姓名", "Confirm and add your name"),
    CONTACT_MISSING: t(
      "请核对并加入邮箱或手机号",
      "Review and add your email or phone",
    ),
    STALE_FACT: t(
      "来源变更或撤销，需要重新确认",
      "Sources changed or were withdrawn. Reconfirm them",
    ),
    LONG_PARAGRAPH: t(
      "建议拆成易读的真实经历陈述",
      "Consider shorter statements of your actual experience",
    ),
  };
  return (
    <div className="assistant-result hiring-result">
      <p>
        <strong>{headings[string(result.action)]}</strong>
      </p>
      {result.title && <p dir="auto">{string(result.title)}</p>}
      {job.title && (
        <p dir="auto">
          {[string(job.title), string(job.company)].filter(Boolean).join(" · ")}
        </p>
      )}
      {result.text ? (
        <p className="personal-info-note" dir="auto">
          {string(result.text)}
        </p>
      ) : (
        <dl className="personal-info-fields">
          {array(result.facts)
            .map(object)
            .map((fact, index) => (
              <div key={string(fact.id) || index}>
                <dt>{kinds[string(fact.kind)]}</dt>
                <dd dir="auto">{string(fact.text)}</dd>
              </div>
            ))}
        </dl>
      )}
      {["create_resume_fact", "create_resume_facts"].includes(string(result.action)) && (
        <small>
          {t(
            "本人确认的自述 · 未经独立验证",
            "Self reported and confirmed by you · Not independently verified",
          )}
        </small>
      )}
      {result.action === "check_resume" && (
        <>
          <p>
            {result.ready_for_preparation
              ? t(
                  "基本信息与来源检查通过。",
                  "Basic information and source checks passed.",
                )
              : t(
                  "请先处理以下简历问题。",
                  "Address these resume issues first.",
                )}
          </p>
          <ul>
            {array(result.issues)
              .map(object)
              .map((issue, index) => (
                <li key={index}>
                  {issues[string(issue.code)] ||
                    t(
                      "请在求职页核对详情",
                      "Review the details on the Career page",
                    )}
                </li>
              ))}
          </ul>
          {array(result.missing_keywords).length > 0 && (
            <p>
              {t(
                "岗位要求中尚未体现的关键词：",
                "Job keywords not yet reflected: ",
              )}
              {array(result.missing_keywords).map(string).join("、")}
            </p>
          )}
          <small>
            {t(
              "本地规则检查，不保证通过 ATS，也不会补写未掌握技能。",
              "Local checks do not guarantee ATS acceptance or add unproven skills.",
            )}
          </small>
        </>
      )}
      {result.saved && (
        <a
          className="result-link"
          href={
            result.action === "create_resume_fact"
              ? `/app/profile?record=${encodeURIComponent(string(result.id))}#facts`
              : result.action === "create_resume_facts"
                ? "/app/profile#facts"
              : result.action === "create_application"
                ? `/app/career?record=${encodeURIComponent(string(result.id))}#applications`
                : result.action === "draft_material"
                  ? `/app/career?record=${encodeURIComponent(string(result.id))}#resumes`
                  : `/app/career?record=${encodeURIComponent(string(result.resume_id))}#resumes`
          }
        >
          {["create_resume_fact", "create_resume_facts"].includes(string(result.action))
            ? t("已保存 · 查看求职事实", "Saved · View career fact")
            : result.action === "create_application"
              ? t("已保存 · 查看申请", "Saved · View application")
              : result.action === "draft_material"
                ? t("已保存 · 查看材料草稿", "Saved · View material draft")
                : t("已保存 · 查看简历", "Saved · View resume")}
        </a>
      )}
    </div>
  );
}
