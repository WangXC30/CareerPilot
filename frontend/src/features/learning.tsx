import { useContext, useEffect, useRef, useState } from "react";
import { array, command, currentWorkspace, number, object, records, string, type Data, type Json } from "../shared/api";
import { Action, DataView, Feedback, Loading, title, useLabel, useResource, useText } from "../shared/ui";
import { useSystemDate } from "../shared/dates";
import { informationTitle } from "./information-detail";
import { Project } from "./project";
import { References, Revision } from "./references";
import { Collection, Disclosure, Form, InformationDetail, Reader, Subtabs } from "./workspace-components";
import { Notifications, Report } from "./workspace-support";

function LearningOverview() {
  const t = useText();
  const { revision } = useContext(Revision);
  const plans = useResource("/plans", revision);
  const reviews = useResource("/study-reviews", revision);
  const today = useSystemDate();
  const activePlans = records(plans.data).filter(
    (plan) => plan.status === "active" && !plan.user_archived,
  );
  const tasks = activePlans
    .flatMap((plan) =>
      array(plan.tasks)
        .map(object)
        .filter(
          (task) =>
            ["pending", "paused", "running"].includes(string(task.status)) &&
            string(task.date) &&
            string(task.date) <= today,
        )
        .map((task) => ({ plan, task })),
    )
    .sort((a, b) => string(a.task.date).localeCompare(string(b.task.date)));
  const due = array(object(reviews.data).items)
    .map(object)
    .filter((item) => item.due === true);
  if (plans.error || reviews.error || plans.loading || reviews.loading)
    return null;
  return (
    <section
      className="learning-overview"
      aria-label={t("学习概览", "Learning overview")}
    >
      <div className="learning-overview-title">
        <div>
          <span className="learning-eyebrow">
            {t("学习工作区", "LEARNING WORKSPACE")}
          </span>
          <h2>{t("从今天开始", "Start with today")}</h2>
        </div>
        <a href="/app/today">{t("与助手讨论", "Ask the assistant")} ↗</a>
      </div>
      {!activePlans.length ? (
        <div className="learning-empty">
          <p>
            {t(
              "还没有进行中的学习计划。确定目标后，就可以把学习安排成可执行的任务。",
              "You have no active study plan. Turn a goal into actionable tasks.",
            )}
          </p>
          <div className="learning-actions">
            <a className="learning-primary" href="/app/today">
              {t("与助手制定计划", "Plan with the assistant")}
            </a>
            <a
              href="#create-plan"
              onClick={() => {
                const details = document.querySelector<HTMLDetailsElement>(
                  "#create-plan details",
                );
                if (details) details.open = true;
              }}
            >
              {t("手动创建计划", "Create a plan manually")} ↓
            </a>
            {due.length > 0 && (
              <a href="/app/learn#assessments">
                {t(`${due.length} 项待复习`, `${due.length} reviews due`)} →
              </a>
            )}
          </div>
        </div>
      ) : (
        <div className="learning-overview-grid">
          <div>
            <h3>
              {t("今天与逾期", "Today and overdue")} <span>{tasks.length}</span>
            </h3>
            {tasks.length ? (
              <ul>
                {tasks.slice(0, 3).map(({ plan, task }) => (
                  <li key={`${plan.id}:${task.id}`}>
                    <a
                      href={`/app/learn?record=${encodeURIComponent(string(plan.id))}&task=${encodeURIComponent(string(task.id))}#plans`}
                    >
                      {string(task.title || task.skill_id)}
                    </a>
                    <small>
                      {string(task.date) < today
                        ? t("已逾期", "Overdue")
                        : t("今天", "Today")}{" "}
                      · {number(task.minutes)} {t("分钟", "min")}
                    </small>
                  </li>
                ))}
              </ul>
            ) : (
              <p>{t("今天没有待完成的任务。", "No tasks due today.")}</p>
            )}
          </div>
          <div>
            <h3>
              {t("待复习", "Reviews due")} <span>{due.length}</span>
            </h3>
            <p>
              {due.length
                ? t(
                    `${due.length} 项技能需要复习。`,
                    `${due.length} skills are due for review.`,
                  )
                : t("目前没有到期的复习。", "No reviews are due.")}
            </p>
            <a href="/app/learn#assessments">
              {t("查看复习", "View reviews")} →
            </a>
          </div>
        </div>
      )}
    </section>
  );
}

export function Learn() {
  const t = useText();
  const label = useLabel();
  const { changed } = useContext(Revision);
  return (
    <>
      <Subtabs
        labels={[
          ["计划与任务", "Plans and tasks"],
          ["测验与复习", "Assessment and review"],
          ["项目", "Project practice"],
          ["投入与复盘", "Study and reflection"],
        ]}
      >
        {(tab) =>
          tab === 0 ? (
            <>
              <LearningOverview />
              <div className="section-intro">
                <div>
                  <h2>{t("学习计划", "Study plan")}</h2>
                  <p>
                    {t(
                      "专注当前任务，完成后记录成果。",
                      "Focus on your tasks and record outcomes as you finish.",
                    )}
                  </p>
                </div>
              </div>
              <Collection
                path="/plans"
                open
                empty={t(
                  "还没有学习计划。可以先与助手讨论目标，或在下方创建计划。",
                  "No study plan yet. Discuss your goal with the assistant or create a plan below.",
                )}
              >
                {(plan) => (
                  <>
                    <p className="review-period">
                      {t("开始日期：", "Start date: ")}
                      <time dateTime={string(plan.start_date)}>
                        {string(plan.start_date)}
                      </time>
                    </p>
                    <p className="muted">
                      {label(string(plan.status))} ·{" "}
                      {t("每周预算", "Weekly budget")}{" "}
                      {number(object(plan.validation).budget_minutes)}
                      {t(
                        " 分钟 · 勾选任务并记录成果，保存后计入完成进度。",
                        " min · Check a task and record your outcome to save completion.",
                      )}
                    </p>
                    {plan.status === "draft" && (
                      <Form
                        schema="ConfirmInput"
                        path={`/plans/${plan.id}/publish`}
                        version={number(plan.version)}
                        onDone={changed}
                        label={t("确认发布计划", "Confirm and publish plan")}
                      />
                    )}
                    <TaskList plan={plan} />
                    <Disclosure
                      label={t("历史与重排", "History and replanning")}
                    >
                      <Reader path={`/plans/${plan.id}/history`} />
                      <Form
                        schema="RestorePlanInput"
                        path={`/plans/${plan.id}/restore`}
                        version={number(plan.version)}
                        onDone={changed}
                      />
                      <Action
                        run={() => command(`/plans/${plan.id}/replan`, {})}
                        onDone={changed}
                      >
                        {t("预览重排", "Preview replanning")}
                      </Action>
                      <Form
                        schema="ModeInput"
                        path={`/plans/${plan.id}/mode-preview`}
                        version={number(plan.version)}
                        onDone={changed}
                      />
                    </Disclosure>
                  </>
                )}
              </Collection>
              <Collection path="/plan-previews" hideEmpty>
                {(preview) => (
                  <>
                    <DataView value={preview} />
                    <Form
                      schema="ConfirmInput"
                      path={`/plan-previews/${preview.id}/accept`}
                      version={number(preview.version)}
                      onDone={changed}
                      label={t("接受变更", "Accept changes")}
                    />
                    <Form
                      schema="ConfirmInput"
                      path={`/plan-previews/${preview.id}/reject`}
                      version={number(preview.version)}
                      onDone={changed}
                      label={t("拒绝变更", "Reject changes")}
                    />
                  </>
                )}
              </Collection>
              <div id="create-plan">
                <Disclosure label={t("生成学习计划", "Create a study plan")}>
                  <p className="muted">
                    {t(
                      "可以选择已有目标岗位；如需先添加岗位，请前往",
                      "Choose a saved target job, or first add one in ",
                    )}{" "}
                    <a href="/app/goals#jobs">
                      {t("目标与岗位", "Goals and jobs")}
                    </a>
                    。
                  </p>
                  <Form schema="PlanInput" path="/plans" onDone={changed} />
                </Disclosure>
              </div>
            </>
          ) : tab === 1 ? (
            <>
              <Assessment />
            </>
          ) : tab === 2 ? (
            <>
              <div className="section-intro">
                <div>
                  <h2>{t("项目练习", "Project practice")}</h2>
                  <p>
                    {t(
                      "跟踪里程碑，整理成果并查看评测。",
                      "Track milestones, organize outcomes and review assessments.",
                    )}
                  </p>
                </div>
                <a className="learning-primary-link" href="/app/goals#jobs">
                  {t("从目标岗位创建", "Create from a target job")} →
                </a>
              </div>
              <p className="project-help">
                {t(
                  "在目标岗位的“岗位导向练习项目”中查看可用模板。项目成果与我的资料共用同一记录。",
                  "Find available templates under Job-focused practice project on a target job. Outcomes use the same records as My information.",
                )}{" "}
                <a href="/app/profile#projects">
                  {t("管理项目成果", "Manage project outcomes")} →
                </a>
              </p>
              <Collection
                path="/projects"
                empty={t(
                  "还没有练习项目。先添加或选择目标岗位，再查看是否有适用的练习模板。",
                  "No practice projects yet. Add or choose a target job to see whether a suitable template is available.",
                )}
              >
                {(project) => <Project id={string(project.id)} />}
              </Collection>
            </>
          ) : (
            <>
              <div className="section-intro">
                <div>
                  <h2>{t("学习记录与复盘", "Study records and reflection")}</h2>
                  <p>
                    {t(
                      "记录投入，回顾学习与求职进展。",
                      "Record study time and review learning and career progress.",
                    )}
                  </p>
                </div>
              </div>
              <Disclosure label={t("记录学习投入", "Record study time")}>
                <Form schema="LogInput" path="/study-logs" onDone={changed} />
              </Disclosure>
              <Collection path="/study-logs" hideEmpty>
                {(log) => (
                  <>
                    <InformationDetail
                      item={log}
                      heading={informationTitle(log)}
                    />
                    <Form
                      schema="ConfirmInput"
                      path={`/study-logs/${log.id}/withdraw`}
                      version={number(log.version)}
                      onDone={changed}
                      label={t("撤回投入记录", "Withdraw study entry")}
                    />
                  </>
                )}
              </Collection>
              <Disclosure label={t("提醒", "Reminders")}>
                <Notifications />
              </Disclosure>
              <Report />
              <Disclosure label={t("复盘计划", "Review pipeline")}>
                <Form
                  schema="PipelineInput"
                  path="/review-pipelines"
                  onDone={changed}
                />
                <Collection path="/review-pipelines" />
              </Disclosure>
            </>
          )
        }
      </Subtabs>
    </>
  );
}

function learningDraftKey(kind: string, id = "") {
  return `careerpilot.learning-draft:${currentWorkspace()}:${kind}:${id}`;
}
function readLearningDraft(key: string): Data {
  try {
    return object(JSON.parse(sessionStorage.getItem(key) || "null"));
  } catch {
    return {};
  }
}
function writeLearningDraft(key: string, value?: Data): boolean {
  try {
    if (value) sessionStorage.setItem(key, JSON.stringify(value));
    else sessionStorage.removeItem(key);
    return true;
  } catch {
    return false;
  }
}

function TaskList({ plan, compact }: { plan: Data; compact?: boolean }) {
  const t = useText();
  const label = useLabel();
  const today = useSystemDate();
  const { changed } = useContext(Revision);
  const notify = useContext(Feedback);
  const [current, setCurrent] = useState(plan);
  const [selected, setSelected] = useState("");
  const [outcome, setOutcome] = useState("");
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState("");
  const guard = useRef(false);
  useEffect(() => setCurrent(plan), [plan]);
  const tasks = array(current.tasks).map(object);
  const completed = tasks.filter((task) => task.status === "completed");
  const completedIds = new Set(completed.map((task) => string(task.id)));
  const visible = compact
    ? tasks
        .filter(
          (task) =>
            !["completed", "passed", "skipped"].includes(string(task.status)),
        )
        .slice(0, 3)
    : tasks;
  const days = [...new Set(visible.map((task) => string(task.date)))].sort();
  return (
    <div className="task-list">
      <div className="plan-progress">
        <span>
          {t(
            `已完成 ${completed.length} / ${tasks.length} 项`,
            `${completed.length} / ${tasks.length} tasks completed`,
          )}
        </span>
        <progress
          max={tasks.length || 1}
          value={completed.length}
          aria-label={t("学习计划完成进度", "Study plan progress")}
        />
      </div>
      {!compact && (
        <p className="muted">
          {t(
            "完成学习后仍需考核才能验证能力。",
            "Study completion requires assessment to verify skills.",
          )}
        </p>
      )}
      {days.map((day) => (
        <section className="task-day" key={day}>
          <h3>
            <time dateTime={day}>{day}</time>
          </h3>
          <ul className="task-checklist">
            {visible
              .filter((task) => task.date === day)
              .map((task) => {
                const id = string(task.id);
                const done = task.status === "completed";
                const blocked = array(task.requires).some(
                  (dependency) => !completedIds.has(string(dependency)),
                );
                const available =
                  current.status === "active" &&
                  ["pending", "paused", "running"].includes(
                    string(task.status),
                  ) &&
                  !blocked;
                return (
                  <li
                    className={`task-item${done ? " is-complete" : ""}`}
                    data-task-id={id}
                    tabIndex={-1}
                    key={id}
                  >
                    <label className="task-check">
                      <input
                        type="checkbox"
                        checked={done || selected === id}
                        disabled={busy || !available}
                        onChange={(event) => {
                          setSelected(event.target.checked ? id : "");
                          setOutcome(
                            event.target.checked
                              ? string(
                                  readLearningDraft(
                                    learningDraftKey(
                                      "task",
                                      `${current.id}:${id}`,
                                    ),
                                  ).outcome,
                                )
                              : "",
                          );
                          setFeedback("");
                        }}
                      />
                      <span>
                        <strong dir="auto">
                          {string(task.title || task.skill_id)}
                        </strong>
                        <span className="task-meta">
                          {number(task.minutes)}
                          {t(" 分钟", " min")} · {label(string(task.status))}
                          {task.locked ? t(" · 已锁定", " · Locked") : ""}
                          {!done &&
                            string(task.date) &&
                            (string(task.date) < today
                              ? t(" · 已逾期", " · Overdue")
                              : string(task.date) === today
                                ? t(" · 今天", " · Today")
                                : "")}
                        </span>
                        {available && (
                          <span className="task-hint">
                            {t("勾选后记录成果", "Check to record an outcome")}
                          </span>
                        )}
                      </span>
                    </label>
                    {blocked && !done && (
                      <p className="task-hint">
                        {t("先完成先修任务：", "Complete prerequisites: ")}
                        {array(task.requires)
                          .filter(
                            (dependency) =>
                              !completedIds.has(string(dependency)),
                          )
                          .map((dependency) => {
                            const prior = tasks.find(
                              (item) => item.id === dependency,
                            );
                            return string(
                              prior?.title || prior?.skill_id || dependency,
                            );
                          })
                          .join(t("、", ", "))}
                      </p>
                    )}
                    {current.status !== "active" && (
                      <p className="task-hint">
                        {t(
                          "发布计划后即可勾选",
                          "Publish this plan to check off tasks",
                        )}
                      </p>
                    )}
                    {selected === id && !done && (
                      <form
                        className="task-completion"
                        onSubmit={async (event) => {
                          event.preventDefault();
                          if (guard.current || !outcome.trim()) return;
                          guard.current = true;
                          setBusy(true);
                          setFeedback("");
                          try {
                            const taskPath = `/plans/${current.id}/tasks/${id}`;
                            let version = number(current.version);
                            if (task.status !== "running") {
                              const started = object(
                                await command(
                                  taskPath,
                                  { action: "start" },
                                  "POST",
                                  version,
                                ),
                              );
                              setCurrent(started);
                              version = number(started.version);
                            }
                            const saved = object(
                              await command(
                                taskPath,
                                { action: "complete", outcome: outcome.trim() },
                                "POST",
                                version,
                              ),
                            );
                            setCurrent(saved);
                            writeLearningDraft(
                              learningDraftKey("task", `${current.id}:${id}`),
                            );
                            setSelected("");
                            setOutcome("");
                            const message = t(
                              "学习任务已完成",
                              "Study task completed",
                            );
                            setFeedback(message);
                            notify(message);
                            changed();
                          } catch (error) {
                            const message =
                              error instanceof Error
                                ? error.message
                                : t(
                                    "保存失败，请重试",
                                    "Could not save. Please retry.",
                                  );
                            setFeedback(message);
                            notify(message);
                            changed();
                          } finally {
                            guard.current = false;
                            setBusy(false);
                          }
                        }}
                      >
                        <label htmlFor={`task-outcome-${id}`}>
                          {t(
                            "记录这项任务的学习成果",
                            "Record your learning outcome",
                          )}
                        </label>
                        <textarea
                          id={`task-outcome-${id}`}
                          required
                          maxLength={4000}
                          rows={2}
                          value={outcome}
                          disabled={busy}
                          onChange={(event) => {
                            const value = event.target.value;
                            setOutcome(value);
                            if (
                              !writeLearningDraft(
                                learningDraftKey("task", `${current.id}:${id}`),
                                { outcome: value },
                              )
                            )
                              setFeedback(
                                t(
                                  "浏览器暂存不可用，请在离开前保存。",
                                  "Browser storage is unavailable; save before leaving.",
                                ),
                              );
                          }}
                        />
                        <div className="actions">
                          <button disabled={busy || !outcome.trim()}>
                            {busy
                              ? t("保存中…", "Saving…")
                              : t("保存并完成", "Save and complete")}
                          </button>
                          <button
                            type="button"
                            className="secondary"
                            disabled={busy}
                            onClick={() => {
                              setSelected("");
                              setOutcome("");
                            }}
                          >
                            {t("取消", "Cancel")}
                          </button>
                        </div>
                      </form>
                    )}
                    {!compact && (
                      <Disclosure
                        label={t(
                          "任务详情与调整",
                          "Task details and adjustments",
                        )}
                      >
                        <InformationDetail
                          item={task}
                          heading={informationTitle(task)}
                        />
                        <fieldset disabled={busy} className="task-adjustments">
                          <Form
                            schema="TaskAction"
                            path={`/plans/${current.id}/tasks/${task.id}`}
                            version={number(current.version)}
                            onDone={changed}
                          />
                        </fieldset>
                      </Disclosure>
                    )}
                  </li>
                );
              })}
          </ul>
        </section>
      ))}
      <p role="status">{feedback}</p>
      {compact && (
        <a href="/app/learn">{t("打开学习计划", "Open study plan")}</a>
      )}
    </div>
  );
}

function Assessment() {
  const t = useText();
  const { changed } = useContext(Revision);
  const draftKey = learningDraftKey("assessment");
  const [saved] = useState(() => readLearningDraft(draftKey));
  const [mode, setMode] = useState<"assessment" | "practice" | "review">(() => object(saved.assessment).id ? "assessment" : "practice");
  const [assessment, setAssessment] = useState<Data | undefined>(() =>
    object(saved.assessment).id ? object(saved.assessment) : undefined,
  );
  const [answers, setAnswers] = useState<Record<string, number>>(
    () => object(saved.answers) as Record<string, number>,
  );
  const [feedback, setFeedback] = useState<Json>(null);
  const [assessmentPosition, setAssessmentPosition] = useState(0);
  const [assessmentNotice, setAssessmentNotice] = useState("");
  const [storageError, setStorageError] = useState(false);
  function saveDraft(
    nextAssessment: Data,
    nextAnswers: Record<string, number>,
  ) {
    setStorageError(
      !writeLearningDraft(draftKey, {
        assessment: nextAssessment,
        answers: nextAnswers,
      }),
    );
  }
  function begin(value: Json) {
    const next = object(value);
    setAssessment(next);
    setAnswers({});
    setFeedback(null);
    setAssessmentPosition(0);
    setAssessmentNotice("");
    saveDraft(next, {});
    setMode("assessment");
    requestAnimationFrame(() => document.getElementById("assessment-questions")?.scrollIntoView({ block: "start" }));
  }
  return (
    <>
      <nav className="study-modes" aria-label={t("测验与复习方式", "Assessment and review modes")}>
        {([
          ["practice", t("刷题", "Practice")],
          ["assessment", t("测验", "Assessment")],
          ["review", t("复习", "Review")],
        ] as const).map(([value, label]) => <button key={value} type="button" aria-pressed={mode === value} onClick={() => setMode(value)}>{label}</button>)}
      </nav>
      <div hidden={mode !== "review"}>
        <details className="review-schedule-disclosure"><summary>{t("复习安排", "Review schedule")}</summary><ReviewQueue busy={!!assessment && !feedback} onStart={begin} /></details>
        <InterviewKnowledge />
        <Disclosure label={t("笔试与练习记录", "Written tests and practice")}>
          <Form schema="PracticeInput" path="/practice-attempts" onDone={changed} />
          <Reader path="/practice-attempts" />
        </Disclosure>
      </div>
      <div hidden={mode !== "practice"}><InterviewPractice /></div>
      <div hidden={mode !== "assessment"}>
      <div className="section-intro learning-section-heading">
        <div>
          <h2>{t("技能测验", "Skill assessment")}</h2>
        </div>
      </div>
      {!assessment && (
        <div className="assessment-catalogue">
          <p>{t("选择技能 · 6 题全对通过", "Choose a skill · 6 correct answers to pass")}</p>
          <div className="assessment-topics">
            {["python", "sql", "git", "javascript", "docker", "fastapi", "pytorch", "react"].map((skill) => (
              <Action key={skill} className="secondary" run={async () => begin(await command("/assessments", { skill_id: skill }))}>
                {skill === "sql" ? "SQL" : skill === "javascript" ? "JavaScript" : skill === "fastapi" ? "FastAPI" : skill === "pytorch" ? "PyTorch" : skill[0].toUpperCase() + skill.slice(1)}
              </Action>
            ))}
          </div>
          <details className="assessment-bank-info"><summary>{t("题库说明", "About the question bank")}</summary><p>{t("Python 和 SQL 各有 5,018 道题，其中各 5,000 道是由 10 类题型生成的代码执行题；SQL 使用 SQLite 语法。其他技能各 18 道。", "Python and SQL each have 5,018 questions, including 5,000 generated execution exercises based on 10 problem families; SQL uses SQLite. Other skills have 18 each.")}</p></details>
        </div>
      )}
      {assessment && (
        <section className="assessment-workspace" id="assessment-questions">
          <div className="assessment-progress" aria-live="polite">
            <strong>{string(assessment.skill_id)} · {t("基础测验", "Foundation assessment")}</strong>
            <span>{t("已作答", "Answered")} {Object.keys(answers).length} / {array(assessment.questions).length}</span>
          </div>
          {!feedback && (
            <p className="muted">
              {t(
                "作答会暂存在当前浏览器标签页，刷新或切换页面后可继续。",
                "Answers are saved in this browser tab and can be resumed after refresh or navigation.",
              )}
            </p>
          )}
          {storageError && (
            <p role="alert">
              {t(
                "浏览器暂存不可用，请在离开前提交。",
                "Browser storage is unavailable; submit before leaving.",
              )}
            </p>
          )}
          {!feedback && <><div className="assessment-step" aria-live="polite">{t("第", "Question ")}{assessmentPosition + 1} / {array(assessment.questions).length}{t("题", "")}</div>
          <div className="assessment-question-nav" aria-label={t("题目导航", "Question navigation")}>
            {array(assessment.questions).map((_, index) => <button key={index} type="button" aria-current={index === assessmentPosition ? "step" : undefined} aria-label={`${t("第", "Question ")}${index + 1}${t("题", "")} · ${answers[index] === undefined ? t("未作答", "unanswered") : t("已作答", "answered")}`} className={answers[index] === undefined ? "secondary" : "answered"} onClick={() => setAssessmentPosition(index)}>{index + 1}</button>)}
          </div>
          <form onSubmit={(e) => e.preventDefault()}>
            {array(assessment.questions).map((raw, index) => {
              const question = object(raw);
              return (
                <fieldset className="assessment-question" key={index} hidden={index !== assessmentPosition}>
                  <legend>
                    {t("第", "Question ")}{index + 1}{t("题", "")} · {" "}
                    {string(
                      question.prompt || question.question || question.stem,
                    )}
                  </legend>
                  {array(question.choices || question.options).map(
                    (option, choice) => (
                      <label className="check" key={choice}>
                        <input
                          type="radio"
                          name={`question-${index}`}
                          checked={answers[index] === choice}
                          disabled={!!feedback}
                          onChange={() => {
                            const next = { ...answers, [index]: choice };
                            setAnswers(next);
                            setAssessmentNotice("");
                            saveDraft(assessment, next);
                          }}
                        />
                        {string(option) || title(object(option))}
                      </label>
                    ),
                  )}
                </fieldset>
              );
            })}
          </form>
          {assessmentNotice && <p role="alert">{assessmentNotice}</p>}
          <div className="assessment-navigation">
            <button type="button" className="secondary" disabled={assessmentPosition === 0} onClick={() => setAssessmentPosition(assessmentPosition - 1)}>{t("上一题", "Previous question")}</button>
            {assessmentPosition + 1 < array(assessment.questions).length ? <button type="button" onClick={() => setAssessmentPosition(assessmentPosition + 1)}>{t("下一题", "Next question")} →</button> : (
            <Action
              run={async () => {
                const questions = array(assessment.questions);
                if (questions.some((_, index) => answers[index] === undefined))
                {
                  setAssessmentPosition(questions.findIndex((_, index) => answers[index] === undefined));
                  setAssessmentNotice(t("请回答全部题目。", "Answer every question."));
                  return;
                }
                setFeedback(
                  await command(
                    `/assessments/${assessment.id}/submit`,
                    { answers: questions.map((_, i) => answers[i]) },
                    "POST",
                    number(assessment.version),
                  ),
                );
                writeLearningDraft(draftKey);
                changed();
              }}
            >
              {t("提交测验", "Submit assessment")}
            </Action>
            )}
          </div></>}
          {feedback && (() => {
            const response = object(feedback);
            const result = object(response.result);
            if (response.status === "system_error") return <p role="alert">{t("评分暂时不可用，请重新开始测验。此次结果不会作为能力证据。", "Scoring is unavailable. Start a new assessment; this attempt does not create skill evidence.")}</p>;
            return <div className="assessment-result" role="status">
              <h3>{result.passed ? t("测验通过", "Assessment passed") : t("继续练习", "Keep practicing")} · {string(result.score)} / {string(result.total)}</h3>
              <p>{t("查看每道题的答案与解析，再决定下一步。", "Review each answer and explanation before your next attempt.")}</p>
              {array(result.feedback).map((raw, index) => {
                const item = object(raw);
                const question = object(array(assessment.questions)[index]);
                const choices = array(question.choices);
                return <div className="assessment-feedback" key={index}>
                  <strong>{index + 1}. {string(question.prompt)} · {item.correct ? t("正确", "Correct") : t("答错", "Incorrect")}</strong>
                  <p>{t("你的答案：", "Your answer: ")}{string(choices[answers[index]])} · {t("正确答案：", "Correct answer: ")}{string(choices[number(item.expected_choice)])}</p>
                  <p>{string(item.explanation)}</p>
                </div>;
              })}
            </div>;
          })()}
          <button
            className="secondary"
            type="button"
            onClick={() => {
              if (
                !feedback &&
                !window.confirm(
                  t(
                    "放弃当前测验及未提交的答案？",
                    "Discard this assessment and its unsubmitted answers?",
                  ),
                )
              )
                return;
              writeLearningDraft(draftKey);
              setAssessment(undefined);
              setAnswers({});
              setFeedback(null);
              setAssessmentPosition(0);
              setAssessmentNotice("");
            }}
          >
            {feedback
              ? t("开始新的测验", "Start another assessment")
              : t("放弃本次测验", "Discard assessment")}
          </button>
        </section>
      )}
      </div>
    </>
  );
}

function InterviewPractice() {
  const t = useText();
  const { revision } = useContext(Revision);
  const resource = useResource("/interview-practice", revision);
  const [topic, setTopic] = useState("");
  const [query, setQuery] = useState("");
  const [wrongOnly, setWrongOnly] = useState(false);
  const [reviewIds, setReviewIds] = useState<string[]>([]);
  const [attempts, setAttempts] = useState<Record<string, { choice: number; feedback: Data }>>({});
  const [view, setView] = useState<"list" | "question">("list");
  const [listPage, setListPage] = useState(0);
  const [position, setPosition] = useState(0);
  const [selected, setSelected] = useState<number | null>(null);
  const [feedback, setFeedback] = useState<Data | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const all = array(object(resource.data).items).map(object);
  const topics = Array.from(new Set(all.map((q) => string(q.topic))));
  const needle = query.trim().toLocaleLowerCase();
  const questions = all.filter((q) => (!topic || q.topic === topic) &&
    (!needle || string(q.question).toLocaleLowerCase().includes(needle)) &&
    (!wrongOnly || reviewIds.includes(string(q.id))));
  const currentIndex = Math.min(position, Math.max(questions.length - 1, 0));
  const question = questions[currentIndex];
  const questionId = string(question?.id);
  useEffect(() => {
    const prior = attempts[questionId];
    setSelected(prior?.choice ?? null);
    setFeedback(prior?.feedback ?? null);
    // Restore the answer only when navigating to a different question.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [questionId]);
  if (resource.loading || resource.error)
    return <Loading error={resource.error} retry={resource.refresh} />;
  const wrongCount = Object.values(attempts).filter((attempt) => attempt.feedback.correct === false).length;
  const pageCount = Math.max(1, Math.ceil(questions.length / 20));
  const page = Math.min(listPage, pageCount - 1);
  function move(next: number) {
    setPosition(next);
  }
  return <section className="interview-practice">
    {view === "list" && <><div className="practice-list-heading"><h2>{t("题库", "Problems")}</h2><span>{t("共", "")} {questions.length} {t("题", "problems")}</span></div>
    <div className="practice-filters"><label><span className="sr-only">{t("练习领域", "Practice topic")}</span><select disabled={submitting} value={topic} onChange={(event) => { setTopic(event.target.value); move(0); setListPage(0); }}>
      <option value="">{t("全部领域", "All topics")}</option>
      {topics.map((item) => <option key={item} value={item}>{item}</option>)}
    </select></label><label><span className="sr-only">{t("搜索题目", "Search questions")}</span><input type="search" value={query} disabled={submitting} onChange={(event) => { setQuery(event.target.value); move(0); setListPage(0); }} placeholder={t("搜索题目", "Search questions")} /></label>
    <button type="button" className="secondary" aria-pressed={wrongOnly} disabled={submitting} onClick={() => { if (!wrongOnly) setReviewIds(Object.entries(attempts).filter(([, attempt]) => attempt.feedback.correct === false).map(([id]) => id)); setWrongOnly(!wrongOnly); move(0); setListPage(0); }}>{wrongOnly ? t("全部题目", "All questions") : t("本次错题", "Session mistakes")} · {wrongCount}</button><small>{t("作答状态仅本页保留", "Status is kept on this page only")}</small></div>
    {questions.length ? <><table className="practice-table"><thead><tr><th>{t("状态", "Status")}</th><th>{t("题目", "Problem")}</th><th>{t("领域", "Topic")}</th></tr></thead><tbody>
      {questions.slice(page * 20, page * 20 + 20).map((item, index) => {
        const attempt = attempts[string(item.id)];
        return <tr key={string(item.id)}><td><span className={attempt ? (attempt.feedback.correct ? "practice-solved" : "practice-wrong") : "practice-unattempted"}>{attempt ? (attempt.feedback.correct ? t("已答对", "Correct") : t("待重做", "Retry")) : t("未作答", "Unanswered")}</span></td>
          <td><button type="button" className="practice-question-link" onClick={() => { move(page * 20 + index); setView("question"); }}>{page * 20 + index + 1}. {string(item.question).split("\n")[0]}</button></td><td>{string(item.topic)}</td></tr>;
      })}
    </tbody></table>{pageCount > 1 && <div className="practice-list-pagination"><span>{t("第", "Page ")}{page + 1} / {pageCount}{t("页", "")}</span><button type="button" className="secondary" disabled={page === 0} onClick={() => setListPage(page - 1)}>{t("上一页", "Previous")}</button><button type="button" className="secondary" disabled={page + 1 >= pageCount} onClick={() => setListPage(page + 1)}>{t("下一页", "Next")}</button></div>}</> : <p className="empty">{wrongOnly ? t("本次没有符合条件的错题。", "No session mistakes match these filters.") : t("没有匹配的题目，请调整筛选条件。", "No matching questions. Adjust the filters.")}</p>}</>}
    {view === "question" && question && <>
      <button type="button" className="practice-back" disabled={submitting} onClick={() => { setListPage(Math.floor(currentIndex / 20)); setView("list"); }}>← {t("题目列表", "Problem list")}</button>
      <div className="practice-progress" aria-live="polite">{string(question.topic)} · {currentIndex + 1} / {questions.length}{question.language === "en" && <span>{t("英文原题", "Original English")}</span>}</div>
      <fieldset className="assessment-question"><legend>{string(question.question)}</legend>
        {array(question.choices).map((choice, index) => <label className="check" key={index}>
          <input type="radio" name="interview-practice-choice" checked={selected === index} disabled={submitting || !!feedback} onChange={() => setSelected(index)} />
          {string(choice)}
        </label>)}
      </fieldset>
      {!feedback && <Action disabled={selected === null || submitting} run={async () => {
        if (selected === null) return;
        setSubmitting(true);
        try {
          const result = object(await command(`/interview-practice/${question.id}/answer`, { choice: selected }));
          setFeedback(result);
          setAttempts((previous) => ({ ...previous, [string(question.id)]: { choice: selected, feedback: result } }));
        }
        finally { setSubmitting(false); }
      }}>{t("检查答案", "Check answer")}</Action>}
      {feedback && <div className="assessment-feedback" role="status">
        <strong>{feedback.correct ? t("回答正确", "Correct") : t("再想一想", "Incorrect")}</strong>
        <p>{t("正确答案：", "Correct answer: ")}{string(array(question.choices)[number(feedback.correct_choice)])}</p>
        <p>{string(feedback.explanation)}</p>
        <details className="practice-sources"><summary>{t("题目来源", "Sources")}</summary>
          <a href={string(feedback.source_url)} target="_blank" rel="noreferrer">{t("参考资料", "Reference")} ↗</a>
          {feedback.origin_url && <p><a href={string(feedback.origin_url)} target="_blank" rel="noreferrer">{t("原题", "Original question")} ↗</a> · {string(feedback.attribution)}</p>}
          {feedback.author_url && feedback.license_url && <p><a href={string(feedback.author_url)} target="_blank" rel="noreferrer">CodePrep ↗</a> · <a href={string(feedback.license_url)} target="_blank" rel="noreferrer">CC BY 4.0 ↗</a></p>}
        </details>
        {!feedback.correct && <button type="button" className="secondary" onClick={() => { setSelected(null); setFeedback(null); }}>{t("再做一次", "Try again")}</button>}
      </div>}
      <div className="interview-knowledge-pagination">
        <button type="button" className="secondary" disabled={submitting || currentIndex === 0} onClick={() => move(currentIndex - 1)}>{t("上一题", "Previous question")}</button>
        <button type="button" className={feedback ? "" : "secondary"} disabled={submitting || currentIndex + 1 >= questions.length} onClick={() => move(currentIndex + 1)}>{t("下一题", "Next question")} →</button>
      </div>
    </>}
  </section>;
}

function InterviewKnowledge() {
  const t = useText();
  const { revision } = useContext(Revision);
  const resource = useResource("/interview-knowledge", revision);
  const [topic, setTopic] = useState("");
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(0);
  if (resource.loading || resource.error)
    return <Loading error={resource.error} retry={resource.refresh} />;
  const cards = array(object(resource.data).items).map(object);
  const topics = Array.from(new Set(cards.map((card) => string(card.topic))));
  const needle = query.trim().toLocaleLowerCase();
  const visible = cards.filter((card) =>
    (!topic || card.topic === topic) &&
    (!needle || [card.question, card.answer, card.topic].some((value) => string(value).toLocaleLowerCase().includes(needle))),
  );
  const currentPage = Math.min(page, Math.max(0, visible.length - 1));
  const card = visible[currentPage];
  return <section className="interview-knowledge">
    <h2>{t("知识卡片", "Knowledge cards")}</h2>
    <div className="practice-filters"><label>{t("知识领域", "Topic")}{" "}<select value={topic} onChange={(event) => { setTopic(event.target.value); setPage(0); }}>
      <option value="">{t("全部领域", "All topics")}</option>
      {topics.map((item) => <option key={item} value={item}>{item}</option>)}
    </select></label>
    <label>{t("搜索题目或答案", "Search questions or answers")}{" "}<input type="search" value={query} onChange={(event) => { setQuery(event.target.value); setPage(0); }} /></label></div>
    {visible.length === 0 && <p>{t("没有匹配的题目。", "No matching questions.")}</p>}
    {card && <><div className="knowledge-card" key={string(card.id)}>
      <div className="knowledge-card-meta" aria-live="polite"><span>{string(card.topic)}</span><span>{currentPage + 1} / {visible.length}</span></div>
      <h3>{string(card.question)}</h3>
      <details><summary>{t("查看答案", "Reveal answer")}</summary>
        <p>{string(card.answer)}</p>
        <a href={string(card.source_url)} target="_blank" rel="noreferrer">{t("查看参考来源", "View source")} ↗</a>
      </details>
    </div><div className="interview-knowledge-pagination">
      <button type="button" className="secondary" disabled={currentPage === 0} onClick={() => setPage(currentPage - 1)}>{t("上一题", "Previous question")}</button>
      <button type="button" className="secondary" disabled={currentPage + 1 >= visible.length} onClick={() => setPage(currentPage + 1)}>{t("下一题", "Next question")}</button>
    </div></>}
  </section>;
}

function ReviewQueue({
  busy,
  onStart,
}: {
  busy: boolean;
  onStart: (value: Json) => void;
}) {
  const t = useText();
  const label = useLabel();
  const { revision } = useContext(Revision);
  const resource = useResource("/study-reviews", revision);
  if (resource.loading || resource.error)
    return <Loading error={resource.error} retry={resource.refresh} />;
  const data = object(resource.data);
  const items = array(data.items).map(object);
  return (
    <section className="review-queue">
      {items.length ? (
        (["overdue", "due", "scheduled"] as const).map((group) => {
          const matches = items.filter((item) => item.risk === group);
          if (!matches.length) return null;
          return (
            <div className="review-group" key={group}>
              <h3>
                {group === "overdue"
                  ? t("已逾期", "Overdue")
                  : group === "due"
                    ? t("今天", "Today")
                    : t("之后", "Upcoming")}{" "}
                <span>{matches.length}</span>
              </h3>
              <ul>
                {matches.map((item) => (
                  <li key={string(item.skill_id)}>
                    <div>
                      <strong>{string(item.name || item.skill_id)}</strong>
                      <p>
                        {t("上次结果：", "Last result: ")}
                        {label(string(item.conclusion))} ·{" "}
                        {t("复习日期：", "Due: ")}
                        {string(item.due_date)}
                      </p>
                      {array(item.wrong_questions).length > 0 && (
                        <details>
                          <summary>
                            {t("查看错题解析", "Review incorrect answers")}
                          </summary>
                          {array(item.wrong_questions).map((raw, index) => {
                            const wrong = object(raw);
                            const question = object(wrong.question);
                            const choices = array(question.choices);
                            return <div className="assessment-feedback" key={index}>
                              <strong>{string(question.prompt) || string(wrong.question_id)}</strong>
                              {choices.length > 0 && <p>{t("正确答案：", "Correct answer: ")}{string(choices[number(wrong.expected_choice)])}</p>}
                              <p>{string(wrong.explanation)}</p>
                            </div>;
                          })}
                        </details>
                      )}
                    </div>
                    <Action
                      className="secondary"
                      run={async () => {
                        if (busy)
                          throw new Error(
                            t(
                              "请先完成或放弃当前测验。",
                              "Finish or discard the current assessment first.",
                            ),
                          );
                        const value = await command("/assessments", {
                          skill_id: string(item.skill_id),
                        });
                        onStart(value);
                      }}
                    >
                      {t("开始复测", "Retake")}
                    </Action>
                  </li>
                ))}
              </ul>
            </div>
          );
        })
      ) : (
        <p className="empty">
          {t(
            "还没有复习安排。完成技能测验后会自动生成。",
            "No reviews yet. Complete an assessment to create a schedule.",
          )}
        </p>
      )}
      <details className="review-rules">
        <summary>{t("复习规则", "Review rules")}</summary>
        <p>{string(data.notice)}</p>
      </details>
    </section>
  );
}

