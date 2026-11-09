import { useState } from "react";
import {
  array,
  command,
  number,
  object,
  request,
  string,
  type Data,
} from "../shared/api";
import { CommandForm, type Options } from "../shared/forms";
import { Action, DataView, title, useText } from "../shared/ui";
import { PlanEditor } from "./plan-editor";

export const informationKinds: Record<string, [string, string]> = {
  profile: ["基础档案", "Profile"],
  material: ["原始材料", "Materials"],
  memory: ["经历与文字资料", "Experience and notes"],
  practice_project: ["练习项目", "Practice projects"],
  resume_fact: ["求职事实", "Career facts"],
  source_document: ["来源资料", "Source documents"],
  job: ["岗位与 JD", "Jobs and JDs"],
  resume: ["简历", "Resumes"],
  job_application: ["申请记录", "Applications"],
  application_draft: ["求职材料草稿", "Application documents"],
  plan: ["学习计划", "Study plans"],
  study_log: ["学习记录", "Study logs"],
  quarter_review: ["阶段反思", "Reflections"],
  career_goal: ["职业目标", "Career goals"],
  interview_session: ["面试练习", "Interview practice"],
  interview_review: ["面试复盘", "Interview reviews"],
  resume_translation: ["简历译文", "Resume translations"],
  offer_comparison: ["Offer 比较", "Offer comparisons"],
  match_snapshot: ["岗位匹配记录", "Job matches"],
  plan_preview: ["计划调整预览", "Plan change previews"],
  review_pipeline: ["复习安排", "Review schedules"],
};
const archivedKinds = new Set([
  "career_goal",
  "interview_session",
  "interview_review",
  "resume_translation",
  "offer_comparison",
  "match_snapshot",
  "plan_preview",
  "review_pipeline",
]);
const relatedKinds: Record<string, [string, string]> = {
  ...informationKinds,
  project_asset: ["项目成果文件", "Project assets"],
  project_submission: ["项目提交", "Project submissions"],
  project_assessment: ["项目考核记录", "Project assessments"],
  project_narrative: ["项目成果陈述", "Project narratives"],
  project_review: ["项目复盘", "Project reviews"],
  project_plan_preview: ["项目计划预览", "Project plan previews"],
  plan_preview: ["计划调整预览", "Plan change previews"],
  review_pipeline: ["复习安排", "Review schedules"],
  resume_version: ["简历版本", "Resume versions"],
  resume_translation: ["简历译文", "Resume translations"],
  resume_fact_revocation: ["事实撤回记录", "Fact withdrawal"],
  application_reminder: ["申请提醒", "Application reminders"],
  application_notice: ["申请通知", "Application notices"],
};

export function InformationActions({
  kind,
  item,
  onChanged,
  options = {},
  edit = true,
}: {
  kind: string;
  item: Data;
  onChanged: () => void;
  options?: Options;
  edit?: boolean;
}) {
  const t = useText();
  const [editing, setEditing] = useState<Data>();
  const [preview, setPreview] = useState<Data>();
  const [showHistory, setShowHistory] = useState(false);
  const [history, setHistory] = useState<Data[]>();
  const id = encodeURIComponent(string(item.id));
  const path = `/information/${kind}/${id}`;
  const archived =
    !!item.user_archived ||
    (kind === "career_goal" && item.status === "archived");
  const factSource = object(item.source);
  const factDefaults: Data = {
    kind: item.kind,
    source_type: factSource.type,
    text: item.text,
    source_id: factSource.id,
    source_version: factSource.version,
    profile_field: factSource.field,
  };
  if (factSource.type === "project") factDefaults.source_type = "manual";
  if (factSource.type === "profile") delete factDefaults.source_id;
  const selfReview =
    kind === "interview_review" && item.basis === "user_reported_interview";
  const schema = selfReview
    ? "SelfReviewInput"
    : kind === "memory"
      ? item.structured_experience
        ? "ExperienceInput"
        : "MemoryEdit"
      : (
          {
            profile: "ConfirmProfile",
            material: "MaterialEdit",
            resume_fact: "FactInput",
            source_document: "DocumentInput",
            practice_project: "ProjectDetails",
            job: "JobEdit",
            application_draft: "DraftEdit",
            quarter_review: "ReflectionEdit",
            study_log: "LogInput",
            resume: "ResumeDetails",
            career_goal: "GoalInput",
            offer_comparison: "OffersInput",
            resume_translation: "TranslationInput",
            interview_review: "careerpilot__interview_api__ReviewInput",
            interview_session: "BusinessDetails",
            match_snapshot: "BusinessDetails",
            plan_preview: "BusinessDetails",
            review_pipeline: "BusinessDetails",
            job_application: "BusinessDetails",
          } as Record<string, string>
        )[kind];
  const editPath = selfReview
    ? `/interview-reviews/${id}/self-report`
    : kind === "memory"
      ? item.structured_experience
        ? `/experiences/${id}`
        : `/memories/${id}`
      : (
          {
            profile: "/profile",
            material: `/materials/${id}`,
            resume_fact: `/resume-facts/${id}`,
            source_document: `/source-documents/${id}`,
            practice_project: `/projects/${id}/details`,
            job: `/jobs/${id}`,
            application_draft: `/application-drafts/${id}`,
            quarter_review: `/quarter-reviews/${id}`,
            study_log: `/study-logs/${id}`,
            resume: `/resumes/${id}/details`,
            career_goal: `/career-goals/${id}`,
            offer_comparison: `/offer-comparisons/${id}`,
            resume_translation: `/resume-translations/${id}`,
            interview_review: `/interview-evaluations/${item.evaluation_id}/review`,
            interview_session: `/business-records/${kind}/${id}/details`,
            match_snapshot: `/business-records/${kind}/${id}/details`,
            plan_preview: `/business-records/${kind}/${id}/details`,
            review_pipeline: `/business-records/${kind}/${id}/details`,
            job_application: `/business-records/${kind}/${id}/details`,
          } as Record<string, string>
        )[kind];
  const defaults = selfReview
    ? {
        ...item,
        decisions: array(item.decisions).map((value) => {
          const decision = object(value);
          return {
            skill_id: decision.skill_id,
            minutes: decision.minutes,
            note: decision.note,
          };
        }),
      }
    : item.structured_experience
      ? object(item.experience)
      : kind === "resume_fact"
        ? factDefaults
        : kind === "source_document"
          ? {
              ...item,
              claims: array(item.claims).map((value) => {
                const claim = object(value);
                return {
                  topic: claim.topic,
                  value: claim.value,
                  start: claim.start,
                  end: claim.end,
                };
              }),
            }
          : kind === "job"
            ? {
                ...item,
                source_url: object(array(item.sources).at(-1)).source_url,
              }
            : kind === "practice_project"
              ? {
                  title: item.title || object(item.template).title,
                  note: item.note || "",
                }
              : kind === "offer_comparison"
                ? {
                    ...item,
                    offers: array(item.offers).map((value) => {
                      const offer = object(value);
                      return {
                        name: offer.name,
                        facts: offer.facts,
                        ratings: offer.ratings,
                        source_basis: offer.source_basis,
                        confirmed: false,
                      };
                    }),
                  }
                : [
                      "interview_session",
                      "match_snapshot",
                      "plan_preview",
                      "review_pipeline",
                      "job_application",
                    ].includes(kind)
                  ? {
                      ...item,
                      title:
                        item.title ||
                        title(item) ||
                        t(...informationKinds[kind]),
                      note: item.note || "",
                    }
                  : item;
  const done = () => {
    setEditing(undefined);
    setPreview(undefined);
    setHistory(undefined);
    onChanged();
  };
  const blockers = array(preview?.blockers).map(object);
  return (
    <div className="information-actions">
      <div className="actions">
        {kind === "resume" && (
          <a href="/app/career">
            {t("编辑简历内容与版本", "Edit resume content and versions")}
          </a>
        )}
        {kind === "job_application" && (
          <a href="/app/career">
            {t("编辑申请阶段与回执", "Edit application stage and receipt")}
          </a>
        )}
        {edit && (schema || kind === "plan") && !item.expired && (
          <Action
            className="secondary"
            run={async () => {
              setPreview(undefined);
              setEditing(
                kind === "material"
                  ? object(await request(`/materials/${id}`))
                  : kind === "resume"
                    ? {
                        ...defaults,
                        ...(await (async () => {
                          const current = object(
                            await request(
                              `/resume-versions/${item.current_version_id}`,
                            ),
                          );
                          return {
                            fact_ids: array(current.facts).map(
                              (value) => object(value).id,
                            ),
                            job_id: object(current.job).id || null,
                          };
                        })()),
                      }
                    : defaults,
              );
            }}
          >
            {t("修改资料", "Edit information")}
          </Action>
        )}
        <Action
          className="subtle"
          run={async () => {
            setHistory(
              array(
                await request(
                  kind === "career_goal"
                    ? `/career-goals/${id}/history`
                    : `${path}/history`,
                ),
              ).map(object),
            );
            setShowHistory(true);
          }}
        >
          {t("查看历史", "View history")}
        </Action>
        {archived ? (
          <Action
            className="secondary"
            run={() =>
              command(
                `/business-records/${kind}/${id}/archive`,
                { confirmed: true, archived: false },
                "POST",
                number(item.version),
              )
            }
            onDone={done}
          >
            {t("恢复记录", "Restore record")}
          </Action>
        ) : (
          <Action
            className="secondary"
            run={async () => {
              setEditing(undefined);
              setPreview(
                archivedKinds.has(kind)
                  ? { archive: true }
                  : object(await request(`${path}/deletion-preview`)),
              );
            }}
          >
            {t("删除资料", "Delete information")}
          </Action>
        )}
      </div>
      {editing && kind === "plan" && (
        <PlanEditor
          key={`${id}:${item.version}`}
          plan={item}
          options={options}
          onDone={done}
          onCancel={() => setEditing(undefined)}
        />
      )}
      {editing && schema && editPath && (
        <section className="panel-content">
          <p>
            {t(
              "修改后保留历史；引用旧版本的资料需要重新核对。",
              "History is retained. Review documents that reference older versions.",
            )}
          </p>
          {kind === "material" && (
            <p>
              {t(
                "原文修订后需重新确认陈述，原陈述不再用于新求职材料。",
                "After revising the original, confirm statements again before using them in new career documents.",
              )}
            </p>
          )}
          <CommandForm
            key={`${id}:${item.version}`}
            schemaName={schema}
            path={editPath}
            method={
              kind === "career_goal" ||
              (kind === "memory" && !item.structured_experience)
                ? "PATCH"
                : "PUT"
            }
            version={number(item.version)}
            defaults={editing}
            options={options}
            onDone={done}
            label={t("保存修改", "Save changes")}
          />
          {kind === "resume" && (
            <CommandForm
              key={`content:${id}:${item.version}`}
              schemaName="careerpilot__hiring_api__ResumeInput"
              path={`/resumes/${id}/versions`}
              version={number(item.version)}
              defaults={editing}
              options={options}
              onDone={done}
              label={t("保存简历新版本", "Save a new resume version")}
            />
          )}
          {kind === "job_application" && (
            <CommandForm
              key={`stage:${id}:${item.version}`}
              schemaName="careerpilot__hiring_api__TransitionInput"
              path={`/applications/${id}/transition`}
              version={number(item.version)}
              options={options}
              onDone={done}
              label={t("更新申请阶段", "Update application stage")}
            />
          )}
          <button
            type="button"
            className="subtle"
            onClick={() => setEditing(undefined)}
          >
            {t("取消修改", "Cancel editing")}
          </button>
        </section>
      )}
      {showHistory && history && (
        <details className="panel" open>
          <summary>{t("资料历史", "Information history")}</summary>
          <DataView value={history} />
          <button
            type="button"
            className="subtle"
            onClick={() => setShowHistory(false)}
          >
            {t("收起历史", "Close history")}
          </button>
        </details>
      )}
      {preview?.archive && (
        <section
          className="danger-zone"
          aria-label={t("删除预览", "Deletion preview")}
        >
          <p>
            {t(
              "此记录将移入历史，可随时恢复。原任务、关联记录和业务历史保持。",
              "This record will move to history and can be restored. Tasks, related records and business history are preserved.",
            )}
          </p>
          <Action
            className="secondary"
            run={() =>
              command(
                `/business-records/${kind}/${id}/archive`,
                { confirmed: true, archived: true },
                "POST",
                number(item.version),
              )
            }
            onDone={done}
          >
            {t("确认移入历史", "Confirm move to history")}
          </Action>
          <button
            type="button"
            className="subtle"
            onClick={() => setPreview(undefined)}
          >
            {t("取消删除", "Cancel deletion")}
          </button>
        </section>
      )}
      {preview && !preview.archive && (
        <section
          className="danger-zone"
          aria-label={t("删除预览", "Deletion preview")}
        >
          <p>
            {t(
              `将删除“${title(item)}”及以下关联记录和它们的历史。已有简历、申请中的独立快照仍可能保留原内容。`,
              `Delete “${title(item)}” and the related records and their history below. Independent snapshots in existing resumes or applications may still retain their content.`,
            )}
          </p>
          <ul>
            {array(preview.records)
              .map(object)
              .map((row) => (
                <li key={string(row.id)}>
                  {t(
                    ...(relatedKinds[string(row.kind)] || [
                      "关联资料",
                      "Related information",
                    ]),
                  )}{" "}
                  · {string(row.title)}
                </li>
              ))}
          </ul>
          {blockers.length > 0 ? (
            <>
              <p>
                {t(
                  "请先处理以下依赖资料，再重新预览删除。",
                  "Handle these dependent records first, then preview deletion again.",
                )}
              </p>
              <ul>
                {blockers.map((row) => (
                  <li key={string(row.id)}>
                    {t(
                      ...(relatedKinds[string(row.kind)] || [
                        "关联资料",
                        "Related information",
                      ]),
                    )}{" "}
                    · {string(row.title)}
                  </li>
                ))}
              </ul>
              <a href="/app/profile#all-information">
                {t("进入全部资料", "Open all information")}
              </a>
            </>
          ) : (
            <Action
              className="secondary"
              run={() =>
                command(
                  path,
                  {
                    confirmed: true,
                    fingerprint: preview.fingerprint,
                  },
                  "DELETE",
                  number(item.version),
                )
              }
              onDone={done}
            >
              {t("确认删除资料", "Confirm deletion")}
            </Action>
          )}
          <button
            type="button"
            className="subtle"
            onClick={() => setPreview(undefined)}
          >
            {t("取消删除", "Cancel deletion")}
          </button>
        </section>
      )}
    </div>
  );
}
