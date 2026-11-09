import {
  useContext,
  useRef,
  useState,
} from "react";
import {
  array,
  command,
  download,
  headers,
  number,
  object,
  records,
  request,
  string,
  type Data,
  type Json,
} from "../shared/api";
import {
  Action,
  DataView,
  Feedback,
  Loading,
  title,
  useLabel,
  useResource,
  useText,
} from "../shared/ui";
import { clearCachedDrafts } from "../shared/drafts";
import { References, Revision } from "./references";
import { Collection, Disclosure, Form, InformationDetail, Reader, Subtabs } from "./workspace-components";
import { Project, ProjectRecommendations } from "./project";
import { InformationActions, informationKinds } from "./information-actions";
import { informationTitle } from "./information-detail";

export function Goals() {
  const t = useText();
  const { changed } = useContext(Revision);
  return (
    <Subtabs
      labels={[
        ["目标", "Goals"],
        ["岗位", "Jobs"],
        ["能力与匹配", "Skills and matching"],
      ]}
    >
      {(tab) =>
        tab === 0 ? (
          <>
            <Disclosure label={t("添加职业目标", "Add a career goal")}>
              <Form schema="GoalInput" path="/career-goals" onDone={changed} />
            </Disclosure>
            <Collection path="/career-goals">
              {(goal, refresh) => (
                <>
                  <InformationDetail
                    item={goal}
                    heading={informationTitle(goal)}
                  />
                  <Form
                    schema="GoalInput"
                    path={`/career-goals/${goal.id}`}
                    method="PATCH"
                    version={number(goal.version)}
                    defaults={goal}
                    onDone={refresh}
                  />
                  <GoalPreview goal={goal} />
                </>
              )}
            </Collection>
            <Disclosure label={t("职业方向", "Career directions")}>
              <Collection path="/catalogue/career" />
            </Disclosure>
          </>
        ) : tab === 1 ? (
          <>
            <Disclosure label={t("导入岗位原文", "Import job description")}>
              <Form schema="JobInput" path="/jobs/import" onDone={changed} />
            </Disclosure>
            <Disclosure label={t("筛选岗位", "Filter jobs")}>
              <Form schema="FilterInput" path="/job-search" />
            </Disclosure>
            <Collection path="/jobs">
              {(job) => (
                <>
                  <InformationDetail
                    item={job}
                    heading={informationTitle(job)}
                  />
                  <Disclosure label={t("匹配依据", "Match explanation")}>
                    <Reader path={`/jobs/${job.id}/match`} />
                  </Disclosure>
                  <Disclosure label={t("岗位状态", "Job status")}>
                    <Form
                      schema="JobStatusInput"
                      path={`/jobs/${job.id}/status`}
                      version={number(job.version)}
                      onDone={changed}
                    />
                  </Disclosure>
                  <Disclosure label={t("收藏与反馈", "Save and give feedback")}>
                    <Reader path={`/jobs/${job.id}/feedback/history`} />
                    <JobFeedback job={job} />
                  </Disclosure>
                  <ProjectRecommendations job={job} />
                </>
              )}
            </Collection>
            <Disclosure label={t("收藏与屏蔽", "Saved and blocked jobs")}>
              <Reader path="/saved-jobs" />
              <Editable path="/job-preferences" schema="PreferencesInput" />
            </Disclosure>
            <Disclosure label={t("岗位订阅", "Job subscriptions")}>
              <Form
                schema="SubscriptionInput"
                path="/job-subscriptions"
                onDone={changed}
              />
              <Collection path="/job-subscriptions">
                {(subscription) => (
                  <>
                    <DataView value={subscription} />
                    <Form
                      schema="SubscriptionState"
                      path={`/job-subscriptions/${subscription.id}`}
                      method="PATCH"
                      version={number(subscription.version)}
                      onDone={changed}
                    />
                    <Action
                      className="secondary"
                      run={() =>
                        command(
                          `/job-subscriptions/${subscription.id}/check`,
                          {},
                        )
                      }
                      onDone={changed}
                    >
                      {t("检查更新", "Check updates")}
                    </Action>
                  </>
                )}
              </Collection>
            </Disclosure>
          </>
        ) : (
          <>
            <Disclosure label={t("分析技能差距", "Analyze skill gaps")}>
              <Form schema="GapInput" path="/gap-analysis" />
            </Disclosure>
            <Disclosure label={t("重新计算岗位匹配", "Recompute job matching")}>
              <Form
                schema="MatchInput"
                path="/job-matches/recompute"
                onDone={changed}
              />
            </Disclosure>
            <Collection path="/job-matches" />
            <Disclosure label={t("已保存的匹配记录", "Saved matching records")}>
              <Collection path="/information?kind=match_snapshot" />
            </Disclosure>
            <Disclosure label={t("匹配约束", "Matching constraints")}>
              <Editable
                schema="MatchingConstraints"
                path="/matching-constraints"
              />
            </Disclosure>
            <Disclosure label={t("能力地图", "Skill map")}>
              <Form schema="SkillGraphInput" path="/skill-graph" />
            </Disclosure>
            <Collection path="/skills" />
            <Disclosure
              label={t("技能证据与撤销", "Skill evidence and revocation")}
            >
              <Collection path="/skill-evidence">
                {(item) => (
                  <>
                    <DataView value={item} />
                    <Form
                      schema="RevokeInput"
                      path={`/skill-evidence/${item.id}/revoke`}
                      version={number(item.version) || 1}
                      onDone={changed}
                    />
                  </>
                )}
              </Collection>
            </Disclosure>
          </>
        )
      }
    </Subtabs>
  );
}

function GoalPreview({ goal }: { goal: Data }) {
  const t = useText();
  const { changed } = useContext(Revision);
  const [preview, setPreview] = useState<Data>();
  return (
    <>
      <Action
        className="secondary"
        run={async () =>
          setPreview(
            object(await command(`/career-goals/${goal.id}/preview`, {})),
          )
        }
      >
        {t("预览切换为主目标", "Preview as primary goal")}
      </Action>
      {preview && (
        <>
          <DataView value={preview} />
          <Form
            schema="ConfirmInput"
            path={`/goal-previews/${preview.id}/accept`}
            version={number(preview.version)}
            onDone={changed}
          />
        </>
      )}
    </>
  );
}

export function Profile() {
  const t = useText();
  const { changed } = useContext(Revision);
  return (
    <Subtabs
      labels={[
        ["基础档案", "Profile"],
        ["材料与经历", "Materials and experience"],
        ["项目成果", "Project outcomes"],
        ["求职事实", "Career facts"],
        ["来源与历史", "Sources and history"],
        ["全部资料", "All information"],
      ]}
    >
      {(tab) =>
        tab === 0 ? (
          <>
            <p className="muted">
              {t(
                "可以先保存私人草稿；生成计划或匹配前再补齐必需信息。",
                "Save a private draft first and complete required information before planning or matching.",
              )}
            </p>
            <Editable
              schema="ConfirmProfile"
              path="/profile"
              draft={{ kind: "profile", key: "personal" }}
            />
            <Disclosure
              label={t("档案历史与恢复", "Profile history and restore")}
            >
              <Reader path="/profile/history" />
              <ProfileRestore />
            </Disclosure>
          </>
        ) : tab === 1 ? (
          <>
            <MaterialImport />
            <Disclosure
              label={t("添加结构化经历", "Add structured experience")}
            >
              <Form
                schema="ExperienceInput"
                path="/experiences/new"
                method="PUT"
                version={0}
                draft={{ kind: "experience", key: "new" }}
                onDone={changed}
              />
            </Disclosure>
            <Collection path="/experiences">
              {(experience) => (
                <>
                  <InformationDetail
                    item={experience}
                    heading={informationTitle(experience)}
                  />
                  <Disclosure label={t("编辑经历", "Edit experience")}>
                    <Form
                      schema="ExperienceInput"
                      path={`/experiences/${experience.id}`}
                      method="PUT"
                      version={number(experience.version)}
                      defaults={object(experience.experience)}
                      draft={{ kind: "experience", key: string(experience.id) }}
                      onDone={changed}
                    />
                  </Disclosure>
                  <Form
                    schema="ConfirmInput"
                    path={`/experiences/${experience.id}/fact`}
                    version={number(experience.version)}
                    onDone={changed}
                    label={t("引用为求职事实", "Use as a career fact")}
                  />
                </>
              )}
            </Collection>
            <Collection path="/materials" hideEmpty>
              {(material) => (
                <>
                  <InformationDetail
                    item={material}
                    heading={informationTitle(material)}
                  />
                  <Disclosure label={t("原文与来源", "Original and source")}>
                    <MaterialOriginal id={string(material.id)} />
                  </Disclosure>
                </>
              )}
            </Collection>
            <Collection path="/memories" hideEmpty>
              {(memory) => (
                <>
                  <InformationDetail
                    item={memory}
                    heading={informationTitle(memory)}
                  />
                  <Disclosure label={t("编辑文字资料", "Edit note")}>
                    <Form
                      schema="MemoryEdit"
                      path={`/memories/${memory.id}`}
                      method="PATCH"
                      version={number(memory.version)}
                      defaults={memory}
                      onDone={changed}
                    />
                  </Disclosure>
                  <WithdrawMemory item={memory} />
                </>
              )}
            </Collection>
          </>
        ) : tab === 2 ? (
          <>
            <Collection path="/projects">
              {(project) => <Project id={string(project.id)} />}
            </Collection>
            <p>
              <a href="/app/goals">
                {t(
                  "从目标岗位选择练习项目",
                  "Choose a practice project from a target job",
                )}
              </a>
            </p>
          </>
        ) : tab === 3 ? (
          <>
            <Disclosure
              label={t("确认可用于求职的事实", "Confirm a career fact")}
            >
              <Form
                schema="FactInput"
                path="/resume-facts"
                draft={{ kind: "fact", key: "new" }}
                onDone={changed}
              />
            </Disclosure>
            <Collection path="/resume-facts">
              {(fact) => (
                <>
                  <InformationDetail
                    item={fact}
                    heading={informationTitle(fact)}
                  />
                  <Form
                    schema="ReasonInput"
                    path={`/resume-facts/${fact.id}/revoke`}
                    version={number(fact.version)}
                    onDone={changed}
                    label={t("撤回此事实", "Revoke fact")}
                  />
                </>
              )}
            </Collection>
          </>
        ) : tab === 4 ? (
          <>
            <Reader path="/profile-timeline" />
            <Disclosure
              label={t(
                "添加本人引用与摘录",
                "Add a personal reference or excerpt",
              )}
            >
              <Form
                schema="DocumentInput"
                path="/source-documents"
                onDone={changed}
              />
            </Disclosure>
            <Collection path="/source-documents" />
            <GitHub />
            <Disclosure
              label={t("历史私人资料", "Historical private information")}
            >
              <HistoricalData />
            </Disclosure>
          </>
        ) : (
          <InformationBrowser />
        )
      }
    </Subtabs>
  );
}

function InformationBrowser() {
  const t = useText();
  const { changed, revision } = useContext(Revision);
  const options = useContext(References);
  const [kind, setKind] = useState("");
  const [query, setQuery] = useState("");
  const [adding, setAdding] = useState("memory");
  const [includeHistory, setIncludeHistory] = useState(false);
  const resource = useResource(
    `/information?kind=${encodeURIComponent(kind)}&q=${encodeURIComponent(query)}`,
    revision,
  );
  const visibleItems = records(resource.data).filter(
    (item) =>
      includeHistory ||
      (!item.user_archived &&
        !(item.record_kind === "career_goal" && item.status === "archived")),
  );
  const addForms: Record<string, [string, string]> = {
    memory: ["NoteInput", "/memories"],
    experience: ["ExperienceInput", "/experiences/new"],
    resume_fact: ["FactInput", "/resume-facts"],
    source_document: ["DocumentInput", "/source-documents"],
    job: ["JobInput", "/jobs/import"],
    study_log: ["LogInput", "/study-logs"],
    resume: ["ResumeCreate", "/resumes"],
    plan: ["PlanInput", "/plans"],
    career_goal: ["GoalInput", "/career-goals"],
    interview_session: ["SessionInput", "/interviews"],
    offer_comparison: ["OffersInput", "/offer-comparisons"],
    resume_translation: ["TranslationInput", "/resume-translations"],
  };
  return (
    <section className="information-browser">
      <Disclosure label={t("新增资料", "Add information")}>
        <label>
          {t("资料类型", "Information type")}
          <select value={adding} onChange={(e) => setAdding(e.target.value)}>
            {Object.keys(addForms).map((key) => (
              <option key={key} value={key}>
                {key === "experience"
                  ? t("结构化经历", "Structured experience")
                  : t(...informationKinds[key])}
              </option>
            ))}
            <option value="material">{t("原始材料", "Materials")}</option>
          </select>
        </label>
        {adding === "material" ? (
          <MaterialImport />
        ) : (
          <Form
            key={adding}
            schema={addForms[adding][0]}
            path={addForms[adding][1]}
            method={adding === "experience" ? "PUT" : "POST"}
            version={adding === "experience" ? 0 : undefined}
            defaults={adding === "memory" ? { category: "self_report" } : {}}
            onDone={changed}
          />
        )}
        <p>
          <a href="/app/goals">
            {t("管理目标与岗位", "Manage goals and jobs")}
          </a>
          {" · "}
          <a href="/app/learn">
            {t("创建计划与项目", "Create plans and projects")}
          </a>
          {" · "}
          <a href="/app/career">
            {t("管理简历与申请", "Manage resumes and applications")}
          </a>
        </p>
      </Disclosure>
      <div className="list-tools">
        <label>
          {t("资料类型", "Information type")}
          <select
            aria-label={t("资料类型", "Information type")}
            value={kind}
            onChange={(e) => setKind(e.target.value)}
          >
            <option value="">{t("全部类型", "All types")}</option>
            {Object.entries(informationKinds).map(([key, labels]) => (
              <option key={key} value={key}>
                {t(...labels)}
              </option>
            ))}
          </select>
        </label>
        <label className="check">
          <input
            type="checkbox"
            checked={includeHistory}
            onChange={(event) => setIncludeHistory(event.target.checked)}
          />
          {t("显示历史记录", "Show historical records")}
        </label>
        <label>
          {t("搜索全部资料", "Search all information")}
          <input
            type="search"
            value={query}
            maxLength={200}
            onChange={(e) => setQuery(e.target.value)}
          />
        </label>
        <button className="subtle" onClick={resource.refresh}>
          {t("刷新", "Refresh")}
        </button>
      </div>
      {resource.error || resource.loading ? (
        <Loading error={resource.error} retry={resource.refresh} />
      ) : visibleItems.length ? (
        visibleItems.map((item) => (
          <Disclosure
            key={`${item.id}:${item.version}`}
            label={`${t(...informationKinds[string(item.record_kind)])} · ${informationTitle(item)}`}
          >
            <InformationDetail item={item} heading={informationTitle(item)} />
            {item.record_kind === "material" &&
              item.status === "pending_confirmation" && (
                <MaterialImport existing={string(item.id)} />
              )}
            <InformationActions
              kind={string(item.record_kind)}
              item={item}
              options={options}
              onChanged={changed}
            />
          </Disclosure>
        ))
      ) : (
        <p className="empty">
          {t("没有匹配的资料。", "No matching information.")}
        </p>
      )}
    </section>
  );
}

function Editable({
  path,
  schema,
  draft,
}: {
  path: string;
  schema: string;
  draft?: { kind: string; key: string };
}) {
  const { revision, changed } = useContext(Revision);
  const resource = useResource(path, revision);
  return resource.loading || resource.error ? (
    <Loading error={resource.error} retry={resource.refresh} />
  ) : (
    <Form
      schema={schema}
      path={path}
      method="PUT"
      version={number(object(resource.data).version)}
      defaults={object(resource.data)}
      draft={draft}
      onDone={changed}
    />
  );
}
function ProfileRestore() {
  const { revision, changed } = useContext(Revision);
  const resource = useResource("/profile", revision);
  return (
    <Form
      schema="careerpilot__growth_api__RestoreInput"
      path="/profile/restore"
      version={number(object(resource.data).version)}
      onDone={changed}
    />
  );
}
function WithdrawMemory({ item }: { item: Data }) {
  const t = useText();
  const { changed } = useContext(Revision);
  const [confirmed, setConfirmed] = useState(false);
  return (
    <div className="danger-zone">
      <label className="check">
        <input
          type="checkbox"
          checked={confirmed}
          onChange={(e) => setConfirmed(e.target.checked)}
        />
        {t(
          "撤回会阻止后续使用，已有历史快照仍保留。",
          "Withdrawal prevents future use. Existing snapshots are preserved.",
        )}
      </label>
      {confirmed && (
        <Action
          className="secondary"
          run={() =>
            request(`/memories/${item.id}`, {
              method: "DELETE",
              headers: { "If-Match": `"${item.version}"` },
            })
          }
          onDone={changed}
        >
          {t("确认撤回", "Confirm withdrawal")}
        </Action>
      )}
    </div>
  );
}

function MaterialOriginal({ id }: { id: string }) {
  const t = useText();
  const [text, setText] = useState("");
  return (
    <>
      <Action
        className="secondary"
        run={async () => {
          const response = await fetch(`/api/v1/materials/${id}/original`, {
            headers: headers(),
          });
          if (!response.ok) throw new Error(`${response.status}`);
          setText(await response.text());
        }}
      >
        {t("读取原文", "Read original")}
      </Action>
      <pre className="original" dir="auto">
        {text}
      </pre>
    </>
  );
}

function MaterialImport({ existing }: { existing?: string } = {}) {
  const t = useText();
  const { changed } = useContext(Revision);
  const [file, setFile] = useState<File>();
  const [preview, setPreview] = useState<Data>();
  const [claims, setClaims] = useState<Record<number, boolean>>({});
  const [corrections, setCorrections] = useState<Record<number, Data>>({});
  const confirmationKey = useRef(crypto.randomUUID());
  const label = useLabel();
  const [confirmed, setConfirmed] = useState(false);
  const material = object(preview?.material);
  const candidates = array(preview?.candidates).map(object);
  return (
    <Disclosure
      label={
        existing
          ? t("确认材料陈述", "Confirm material statements")
          : t("导入已有材料", "Import existing material")
      }
    >
      {!existing && (
        <label>
          {t("文本或 Markdown 材料", "Text or Markdown document")}
          <input
            type="file"
            accept=".txt,.md"
            onChange={(e) => {
              setFile(e.target.files?.[0]);
              setPreview(undefined);
              setClaims({});
              setCorrections({});
              confirmationKey.current = crypto.randomUUID();
              setConfirmed(false);
            }}
          />
        </label>
      )}
      <Action
        run={async () => {
          if (existing) {
            const material = object(await request(`/materials/${existing}`));
            setPreview({ material, candidates: material.candidates });
            return;
          }
          if (!file) throw new Error(t("请选择文件。", "Choose a file."));
          if (file.size > 2 * 1024 * 1024)
            throw new Error(t("文件过大。", "File is too large."));
          const response = await fetch(
            `/api/v1/resume-imports?filename=${encodeURIComponent(file.name)}`,
            { method: "POST", headers: headers(), body: file },
          );
          const data = object(await response.json());
          if (!response.ok) throw new Error(string(data.message));
          setPreview(data);
        }}
      >
        {t("读取并预览", "Read and preview")}
      </Action>
      {preview && (
        <>
          <p dir="auto">{string(preview.notice)}</p>
          <p>
            {t(
              "相同内容优先复用已有材料；以下陈述必须本人选择确认。",
              "Identical content reuses the existing material. Select statements to confirm below.",
            )}
          </p>
          {candidates.map((candidate, index) => (
            <fieldset key={index}>
              <legend>{t("陈述与来源", "Statement and source")}</legend>
              <p dir="auto">{string(candidate.text)}</p>
              <label className="check">
                <input
                  type="checkbox"
                  checked={!!claims[index]}
                  onChange={(e) =>
                    setClaims({ ...claims, [index]: e.target.checked })
                  }
                />
                <span>{t("选择此陈述", "Select this statement")}</span>
              </label>
              <label>
                {t("校正后的陈述", "Corrected statement")}
                <textarea
                  value={string(corrections[index]?.text ?? candidate.text)}
                  maxLength={2000}
                  onChange={(e) => {
                    setCorrections({
                      ...corrections,
                      [index]: { ...corrections[index], text: e.target.value },
                    });
                    confirmationKey.current = crypto.randomUUID();
                  }}
                />
              </label>
              <label>
                {t("用途栏目", "Section")}
                <select
                  value={string(
                    corrections[index]?.kind ||
                      candidate.suggested_kind ||
                      "experience",
                  )}
                  onChange={(e) => {
                    setCorrections({
                      ...corrections,
                      [index]: { ...corrections[index], kind: e.target.value },
                    });
                    confirmationKey.current = crypto.randomUUID();
                  }}
                >
                  {[
                    "name",
                    "contact",
                    "summary",
                    "education",
                    "experience",
                    "project",
                    "skill",
                  ].map((kind) => (
                    <option key={kind} value={kind}>
                      {label(kind)}
                    </option>
                  ))}
                </select>
              </label>
            </fieldset>
          ))}
          <label className="check">
            <input
              type="checkbox"
              checked={confirmed}
              onChange={(e) => setConfirmed(e.target.checked)}
            />
            {t(
              "我确认所选陈述及来源；自述不代表能力已验证。",
              "I confirm these statements and their sources. Self reports do not verify ability.",
            )}
          </label>
          {confirmed && (
            <Action
              run={async () => {
                const selected = candidates
                  .map((c, i): Data & { selected: boolean } => ({
                    ...c,
                    ...corrections[i],
                    selected: claims[i],
                  }))
                  .filter((c) => c.selected)
                  .map((c) => ({
                    text: c.text,
                    start: c.start,
                    end: c.end,
                    category: "self_report",
                    kind: c.kind || c.suggested_kind || "experience",
                  }));
                if (!selected.length)
                  throw new Error(
                    t("请至少选择一项。", "Select at least one statement."),
                  );
                await command(
                  `/resume-imports/${material.id}/confirm`,
                  { confirmed: true, claims: selected },
                  "POST",
                  number(material.version),
                  confirmationKey.current,
                );
                changed();
                setPreview(undefined);
              }}
            >
              {t("确认所选陈述", "Confirm selected statements")}
            </Action>
          )}
        </>
      )}
    </Disclosure>
  );
}

function JobFeedback({ job }: { job: Data }) {
  const { revision, changed } = useContext(Revision);
  const resource = useResource("/job-feedback", revision);
  const current = records(resource.data || []).find(
    (item) => item.job_id === job.id,
  );
  return resource.loading || resource.error ? (
    <Loading error={resource.error} retry={resource.refresh} />
  ) : (
    <Form
      schema="FeedbackInput"
      path={`/jobs/${job.id}/feedback`}
      method="PUT"
      version={number(current?.version)}
      defaults={current}
      onDone={changed}
    />
  );
}

function HistoricalData() {
  const t = useText();
  const { revision } = useContext(Revision);
  const exported = useResource("/account/export", revision);
  const [kind, setKind] = useState("");
  const rows = array(object(object(exported.data).data).growth_records).map(
    object,
  );
  const kinds = [...new Set(rows.map((row) => string(row.kind)))].sort();
  return (
    <>
      <p>
        {t(
          "延期功能的个人资料仍可在这里读取，并随本地资料导出或清空。",
          "Personal information from deferred features can be read here and included in local export or clearing.",
        )}
      </p>
      <label>
        {t("记录类别", "Record category")}
        <select value={kind} onChange={(e) => setKind(e.target.value)}>
          <option value="">{t("请选择", "Select")}</option>
          {kinds.map((key) => (
            <option key={key}>{key}</option>
          ))}
        </select>
      </label>
      {rows
        .filter((row) => row.kind === kind)
        .map((row) => {
          let content: Json = null;
          try {
            content = JSON.parse(string(row.payload_json));
          } catch {
            content = null;
          }
          return (
            <Disclosure
              key={string(row.id)}
              label={`${string(row.created_at)} · ${string(row.version)}`}
            >
              <DataView value={content} />
            </Disclosure>
          );
        })}
    </>
  );
}

function GitHub() {
  const t = useText();
  const { changed } = useContext(Revision);
  const [url, setUrl] = useState("");
  const params = new URLSearchParams(window.location.search);
  const code = params.get("code"),
    state = params.get("state");
  return (
    <Disclosure
      label={t("按需关联 GitHub", "Connect GitHub when needed")}
      open={!!code}
    >
      <Reader path="/github-binding" />
      <Action
        run={async () => {
          const data = object(
            await command("/github-binding/start", { confirmed: true }),
          );
          setUrl(string(data.authorization_url));
        }}
      >
        {t("开始关联 GitHub", "Start connecting GitHub")}
      </Action>
      {url && (
        <p>
          <a href={url} rel="noreferrer">
            {t("前往 GitHub 确认关联", "Confirm connection on GitHub")}
          </a>
        </p>
      )}
      {code && state && (
        <Action
          run={async () => {
            await command("/github-binding/finish", {
              code,
              state,
              confirmed: true,
            });
            history.replaceState(null, "", "/app/profile#history");
            changed();
          }}
        >
          {t("确认完成 GitHub 关联", "Confirm GitHub connection")}
        </Action>
      )}
      <Action
        className="secondary"
        run={() => command("/github-binding/sync", { confirmed: true })}
        onDone={changed}
      >
        {t("同步公开项目元数据", "Sync public project metadata")}
      </Action>
      <Action
        className="secondary"
        run={() => command("/github-binding/unbind", { confirmed: true })}
        onDone={changed}
      >
        {t("解除关联", "Disconnect")}
      </Action>
    </Disclosure>
  );
}

function Calendar() {
  const t = useText();
  const { revision, changed } = useContext(Revision);
  const events = useResource("/calendar-events", revision);
  return (
    <>
      <Form
        schema="CalendarEventInput"
        path="/calendar-events"
        onDone={changed}
      />
      <DataView value={events.data} />
      <Form
        schema="CalendarInput"
        path="/calendar-syncs"
        options={{
          event_ids: records(events.data || []).map((event) => ({
            value: string(event.id),
            label: title(event),
          })),
        }}
        onDone={changed}
      />
      <Collection path="/calendar-syncs">
        {(record) => (
          <>
            <DataView value={record} />
            <Action
              run={() =>
                download(
                  `/calendar-syncs/${record.id}/export`,
                  "careerpilot.ics",
                )
              }
            >
              {t("下载日历", "Download calendar")}
            </Action>
            <Action
              className="secondary"
              run={() =>
                request(`/calendar-syncs/${record.id}`, { method: "DELETE" })
              }
              onDone={changed}
            >
              {t("撤销日历授权", "Revoke calendar authorization")}
            </Action>
          </>
        )}
      </Collection>
    </>
  );
}

export function Settings() {
  const t = useText();
  const [confirmation, setConfirmation] = useState("");
  const { changed } = useContext(Revision);
  return (
    <>
      <details className="settings-group">
        <summary>
          <strong>{t("助手与偏好", "Assistant and preferences")}</strong>
          <span>
            {t(
              "记忆、通知、模型与日历",
              "Memory, notifications, models and calendar",
            )}
          </span>
        </summary>
        <div className="settings-group-content">
          <Disclosure label={t("助手记忆", "Assistant memory")}>
            <Collection
              path="/workflows/preferences"
              empty={t("尚未保存跨会话偏好。", "No saved preferences.")}
            >
              {(preference) => (
                <>
                  <p>{string(preference.text)}</p>
                  <Action
                    className="secondary"
                    run={() =>
                      command(
                        `/workflows/preferences/${preference.id}/forget`,
                        { confirmed: true },
                        "POST",
                        number(preference.version),
                      )
                    }
                    onDone={changed}
                  >
                    {t("忘记此偏好", "Forget this preference")}
                  </Action>
                </>
              )}
            </Collection>
          </Disclosure>
          <Disclosure label={t("通知偏好", "Notification preferences")}>
            <Editable
              schema="NotificationInput"
              path="/notification-settings"
            />
          </Disclosure>
          <Disclosure label={t("按需导出日历", "Export calendar when needed")}>
            <Calendar />
          </Disclosure>
          <Disclosure
            label={t("模型与外部处理用途", "Models and external processing")}
          >
            <Collection path="/consents">
              {(consent) => (
                <>
                  <DataView value={consent} />
                  <Form
                    schema="ConsentInput"
                    path={`/consents/${consent.purpose}`}
                    method="PUT"
                    version={number(consent.version)}
                    defaults={consent}
                    onDone={changed}
                  />
                </>
              )}
            </Collection>
          </Disclosure>
        </div>
      </details>
      <details className="settings-group">
        <summary>
          <strong>{t("数据与诊断", "Data and diagnostics")}</strong>
          <span>
            {t(
              "导出、历史资料和后台任务",
              "Export, historical data and background tasks",
            )}
          </span>
        </summary>
        <div className="settings-group-content">
          <Disclosure label={t("后台任务", "Background tasks")}>
            <Collection path="/agent-tasks">
              {(task) => (
                <>
                  <DataView value={task} />
                  {task.status === "failed" && (
                    <Action
                      run={() =>
                        command(
                          `/agent-tasks/${task.id}/retry`,
                          {},
                          "POST",
                          number(task.version),
                        )
                      }
                      onDone={changed}
                    >
                      {t("重试", "Retry")}
                    </Action>
                  )}
                </>
              )}
            </Collection>
          </Disclosure>
          <section className="panel">
            <h2>{t("本地资料", "Local information")}</h2>
            <p>
              {t(
                "资料保存在当前本地工作区，无需登录。",
                "Your information is stored in this local workspace without sign-in.",
              )}
            </p>
            <Disclosure label={t("技术详情", "Technical details")}>
              <Reader path="/local-workspace" />
            </Disclosure>
            <Action
              run={() => download("/account/export", "careerpilot-export.json")}
            >
              {t("导出全部个人资料", "Export all personal information")}
            </Action>
            <p>
              <a href="/local-use" target="_blank" rel="noreferrer">
                {t("本地使用说明", "Local use notice")}
              </a>
            </p>
          </section>
          <Disclosure
            label={t(
              "延期功能的历史资料",
              "Historical information from deferred features",
            )}
          >
            <HistoricalData />
          </Disclosure>
          <Disclosure label={t("清空本地资料", "Clear local information")}>
            <p>
              {t(
                "这会删除当前工作区的资料、草稿和工作流状态。业务备份按原规则保留。请输入 CLEAR LOCAL DATA 确认。",
                "This deletes information, drafts and workflow state in the current workspace. Business backups are retained. Type CLEAR LOCAL DATA to confirm.",
              )}
            </p>
            <label>
              {t("确认文字", "Confirmation text")}
              <input
                value={confirmation}
                onChange={(e) => setConfirmation(e.target.value)}
                autoComplete="off"
              />
            </label>
            {confirmation === "CLEAR LOCAL DATA" && (
              <Action
                className="danger"
                run={async () => {
                  await command("/local-workspace/reset", { confirmation });
                  await clearCachedDrafts();
                  setConfirmation("");
                  changed();
                }}
              >
                {t("确认清空当前工作区", "Confirm clearing this workspace")}
              </Action>
            )}
          </Disclosure>
        </div>
      </details>
    </>
  );
}
