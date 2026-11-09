import { useContext } from "react";
import { array, download, number, object, records, string, type Data } from "../shared/api";
import { Action, DataView, Loading, title, useLabel, useResource, useText } from "../shared/ui";
import { informationTitle } from "./information-detail";
import { Revision } from "./references";
import { Disclosure, Form, InformationDetail, Reader } from "./workspace-components";

export function ProjectRecommendations({ job }: { job: Data }) {
  const t = useText();
  const { revision, changed } = useContext(Revision);
  const recommendations = useResource(
    `/project-recommendations?job_id=${job.id}`,
    revision,
  );
  return (
    <Disclosure label={t("岗位导向练习项目", "Job-focused practice project")}>
      <DataView value={recommendations.data} />
      <Form
        schema="ProjectInput"
        path="/projects"
        defaults={{ job_id: job.id || "" }}
        options={{
          template_id: records(recommendations.data || []).map((x) => ({
            value: string(x.id || x.template_id),
            label: title(x),
          })),
        }}
        onDone={changed}
      />
    </Disclosure>
  );
}

export function Project({ id }: { id: string }) {
  const t = useText();
  const label = useLabel();
  const { revision, changed } = useContext(Revision);
  const resource = useResource(`/projects/${id}`, revision);
  const project = object(resource.data);
  const narrative = object(project.narrative);
  return resource.loading || resource.error ? (
    <Loading error={resource.error} retry={resource.refresh} />
  ) : (
    <>
      <p className="project-instructions">
        {string(object(project.template).instructions)}
      </p>
      {string(object(project.template).scope_notice) && (
        <p className="project-help">
          {string(object(project.template).scope_notice)}
        </p>
      )}
      <Disclosure label={t("项目详情与来源", "Project details and sources")}>
        <InformationDetail
          item={project}
          heading={informationTitle(project)}
          omit={["assets", "submissions"]}
        />
      </Disclosure>
      <h3 className="project-subheading">{t("里程碑", "Milestones")}</h3>
      {array(project.progress).length
        ? array(project.progress)
            .map(object)
            .map((milestone) => (
              <Disclosure
                key={string(milestone.id)}
                label={`${title(milestone)} · ${label(string(milestone.status))}`}
              >
                <p className="muted">
                  {number(milestone.minutes)} {t("分钟", "min")} ·{" "}
                  {milestone.verification === "sandbox_tests"
                    ? t(
                        "由实际代码评测结果决定",
                        "Determined by code assessment",
                      )
                    : t("本人记录", "Self-reported")}
                </p>
                {milestone.verification === "sandbox_tests" ? (
                  <p>
                    {t(
                      "请通过下方“客观代码评测”提交代码。",
                      "Submit code under Objective code assessment below.",
                    )}
                  </p>
                ) : (
                  <Form
                    schema="careerpilot__project_api__MilestoneInput"
                    path={`/projects/${id}/milestones/${milestone.id}`}
                    method="PATCH"
                    version={number(project.version)}
                    onDone={changed}
                  />
                )}
              </Disclosure>
            ))
        : array(object(project.template).milestones)
            .map(object)
            .map((milestone) => (
              <Disclosure key={string(milestone.id)} label={title(milestone)}>
                {milestone.verification === "sandbox_tests" ? (
                  <p>
                    {t(
                      "请通过下方“客观代码评测”提交代码。",
                      "Submit code under Objective code assessment below.",
                    )}
                  </p>
                ) : (
                  <Form
                    schema="careerpilot__project_api__MilestoneInput"
                    path={`/projects/${id}/milestones/${milestone.id}`}
                    method="PATCH"
                    version={number(project.version)}
                    onDone={changed}
                  />
                )}
              </Disclosure>
            ))}
      <h3 className="project-subheading">
        {t("成果与评测", "Outcomes and assessment")}
      </h3>
      <Disclosure label={t("添加成果或引用", "Add an outcome or reference")}>
        <Form
          schema="AssetInput"
          path={`/projects/${id}/assets`}
          version={number(project.version)}
          onDone={changed}
        />
      </Disclosure>
      <Disclosure
        label={t("成果叙述与确认", "Outcome narrative and confirmation")}
      >
        <Form
          schema="NarrativeInput"
          path={`/projects/${id}/narrative`}
          method="PUT"
          version={number(narrative.version)}
          defaults={narrative}
          draft={{ kind: "project", key: id }}
          onDone={changed}
        />
        <Reader path={`/projects/${id}/narrative-preview`} />
        <Form
          schema="ConfirmInput"
          path={`/projects/${id}/narrative-fact`}
          version={number(narrative.version)}
          onDone={changed}
          label={t(
            "将有效成果引用为简历事实",
            "Use valid outcomes as a resume fact",
          )}
        />
      </Disclosure>
      <Disclosure label={t("客观代码评测", "Objective code assessment")}>
        <Form
          schema="CodeInput"
          path={`/projects/${id}/submissions`}
          version={number(project.version)}
          onDone={changed}
        />
      </Disclosure>
      {array(project.assets)
        .map(object)
        .map((asset) => (
          <Disclosure key={string(asset.id)} label={title(asset)}>
            <InformationDetail item={asset} heading={informationTitle(asset)} />
            <Action
              className="secondary"
              run={() =>
                download(
                  `/project-assets/${asset.id}/download`,
                  string(asset.filename) || "project.txt",
                )
              }
            >
              {t("下载成果", "Download outcome")}
            </Action>
            <Form
              schema="ConfirmInput"
              path={`/project-assets/${asset.id}/withdraw`}
              version={number(asset.version)}
              onDone={changed}
              label={t("撤回成果", "Withdraw outcome")}
            />
          </Disclosure>
        ))}
      {array(project.submissions)
        .map(object)
        .map((submission) => (
          <Disclosure
            key={string(submission.id)}
            label={`${t("提交记录", "Submission")} · ${string(submission.created_at)}`}
          >
            <DataView value={submission} />
            <Form
              schema="ConfirmInput"
              path={`/project-submissions/${submission.id}/resume-fact`}
              version={number(submission.version)}
              onDone={changed}
              label={t("引用有效评测成果", "Use a valid assessed outcome")}
            />
            <Form
              schema="ConfirmInput"
              path={`/project-submissions/${submission.id}/withdraw`}
              version={number(submission.version)}
              onDone={changed}
              label={t("撤回提交", "Withdraw submission")}
            />
          </Disclosure>
        ))}
    </>
  );
}

