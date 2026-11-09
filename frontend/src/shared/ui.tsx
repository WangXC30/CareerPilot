import {
  createContext,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { array, object, request, string, type Data, type Json } from "./api";

export const Locale = createContext("zh-CN");
export const Feedback = createContext<(message: string) => void>(() => {});
export function useText() {
  const locale = useContext(Locale);
  return (zh: string, en: string) => (locale === "en-US" ? en : zh);
}
export const words: Record<string, [string, string]> = {
  preparation_note: ["准备事项", "Preparation notes"],
  application_id: ["关联申请", "Linked application"],
  skill_status: ["能力状态", "Skill status"],
  stage: ["阶段", "Stage"],
  pending_confirmation: ["待确认", "Awaiting confirmation"],
  published: ["已发布", "Published"],
  instructions: ["使用说明", "Instructions"],
  user_confirmed_not_independently_verified: [
    "本人已确认，尚未独立核实",
    "Confirmed by you; not independently verified",
  ],
  draft_generated: ["已生成草稿", "Draft generated"],
  awaiting_user_submission: ["待本人投递", "Awaiting your submission"],
  prepared: ["已准备", "Prepared"],
  submitted: ["已投递", "Submitted"],
  reported_submitted: ["本人记录已投递", "Submission reported"],
  screening: ["筛选中", "Screening"],
  assessment: ["考核中", "Assessment"],
  interviewing: ["面试中", "Interviewing"],
  no_response: ["暂无回应", "No response"],
  interview: ["面试中", "Interviewing"],
  offer: ["已获 Offer", "Offer received"],
  rejected: ["未通过", "Rejected"],
  record_kind: ["资料类别", "Information category"],
  user_archived: ["已移入历史", "Moved to history"],
  help: ["查看助手能力", "Assistant help"],
  locale: ["语言", "Language"],
  mappings: ["逐项事实译文", "Translations of individual facts"],
  fact_id: ["事实", "Fact"],
  event_ids: ["选择日历事项", "Select calendar events"],
  offers: ["Offer 选项", "Offer options"],
  weights: ["比较权重", "Comparison weights"],
  ratings: ["本人评分（未知留空）", "Your ratings (leave unknown blank)"],
  compensation: ["薪酬", "Compensation"],
  growth: ["成长空间", "Growth"],
  stability: ["稳定性", "Stability"],
  work_life: ["工作与生活", "Work and life"],
  contract_terms: ["合同条款", "Contract terms"],
  source_basis: ["事实依据", "Fact basis"],
  offer_index: ["选择序号（从 0 开始）", "Selected option (starting at 0)"],
  favorite: ["收藏", "Favorite"],
  quality: ["质量反馈", "Quality feedback"],
  broken: ["链接失效", "Broken link"],
  unrated: ["未评价", "Unrated"],
  helpful: ["有帮助", "Helpful"],
  poor: ["帮助有限", "Limited help"],
  sprint: ["冲刺", "Sprint"],
  balanced: ["均衡", "Balanced"],
  research_busy: ["科研繁忙", "Busy with research"],
  light: ["轻量", "Light"],
  precision: ["日期精度", "Date precision"],
  year: ["年", "Year"],
  organization: ["学校或机构", "School or organization"],
  role: ["个人角色", "Your role"],
  end_date: ["结束日期", "End date"],
  internship: ["实习", "Internship"],
  work: ["工作", "Work"],
  material_version: ["材料版本", "Material version"],
  receipt_code: ["回执编号", "Receipt code"],
  submitted_at: ["投递时间（可选）", "Submission time (optional)"],
  resume_description: [
    "外部简历说明（可选）",
    "External resume description (optional)",
  ],
  checklist: ["申请前核对", "Before applying"],
  facts: ["事实无误", "Facts are correct"],
  requirements: ["已核对岗位要求", "Requirements reviewed"],
  privacy: ["已核对个人信息范围", "Personal information reviewed"],
  source_reviewed: ["已核对岗位来源", "Job source reviewed"],
  label: ["反馈", "Feedback"],
  target_date: ["目标日期", "Target date"],
  rights_confirmed: [
    "我有权使用这些材料",
    "I have permission to use this material",
  ],
  license_basis: ["使用依据", "Permission basis"],
  retention_days: ["保留天数", "Retention days"],
  tone: ["语气", "Tone"],
  plain: ["简明", "Plain"],
  formal: ["正式", "Formal"],
  cover_letter: ["求职信", "Cover letter"],
  email: ["邮件草稿", "Email draft"],
  introduction: ["自我介绍", "Introduction"],
  progress: ["查看学习进展", "Read learning progress"],
  plan: ["生成计划草案", "Create plan draft"],
  replan: ["预览计划调整", "Preview replanning"],
  diagnose: ["分析能力差距", "Analyze skill gaps"],
  resources: ["检索学习资料", "Find study resources"],
  auto: ["根据消息选择", "Choose from message"],
  title: ["名称", "Title"],
  name: ["名称", "Name"],
  text: ["内容", "Text"],
  status: ["状态", "Status"],
  version: ["版本", "Version"],
  education: ["学历", "Education"],
  major: ["专业", "Major"],
  academic_year: ["年级", "Year of study"],
  graduation_date: ["毕业日期", "Graduation date"],
  preferred_cities: ["意向城市", "Preferred cities"],
  expected_monthly_salary: [
    "期望月薪（可留空）",
    "Expected monthly salary (optional)",
  ],
  weekly_minutes: ["每周学习分钟数", "Weekly study minutes"],
  preferences: ["补充偏好", "Preferences"],
  timezone: ["时区", "Time zone"],
  confirmed: [
    "我确认所选资料和此次操作",
    "I confirm the selected information and this action",
  ],
  content: ["原文", "Original text"],
  source_url: ["来源网址", "Source URL"],
  source_type: ["资料来源", "Source"],
  source_id: ["来源记录", "Source record"],
  source_version: ["来源版本", "Source version"],
  profile_field: ["档案字段", "Profile field"],
  kind: ["类别", "Kind"],
  job_ids: ["选择岗位", "Select jobs"],
  job_id: ["岗位", "Job"],
  fact_ids: ["选择已确认事实", "Select confirmed facts"],
  resume_version_id: ["简历版本", "Resume version"],
  skill_id: ["技能", "Skill"],
  plan_id: ["学习计划", "Study plan"],
  template_id: ["项目模板", "Project template"],
  start_date: ["开始日期", "Start date"],
  deadline: ["截止日期", "Deadline"],
  message: ["消息", "Message"],
  intent: ["操作", "Action"],
  reason: ["原因", "Reason"],
  action: ["操作", "Action"],
  minutes: ["分钟数", "Minutes"],
  date: ["日期", "Date"],
  outcome: ["实际成果", "Outcome"],
  target_version: ["历史版本", "Previous version"],
  situation: ["背景", "Situation"],
  task: ["任务", "Task"],
  result: ["结果（未知请留空）", "Result (leave blank if unknown)"],
  asset_ids: ["关联成果", "Related assets"],
  filename: ["文件名", "Filename"],
  url: ["网址", "URL"],
  note: ["说明", "Note"],
  target: ["目标阶段", "Target stage"],
  evidence: ["依据", "Evidence"],
  summary: ["摘要", "Summary"],
  category: ["陈述性质", "Statement category"],
  verification: ["验证范围", "Verification scope"],
  source_status: ["来源状态", "Source status"],
  source: ["来源", "Source"],
  budget_minutes: ["预算分钟", "Budget minutes"],
  tasks: ["任务", "Tasks"],
  skills: ["技能", "Skills"],
  fields: ["岗位信息", "Job information"],
  value: ["内容", "Value"],
  quote: ["原文依据", "Source quote"],
  requirement: ["要求", "Requirement"],
  state: ["状态", "State"],
  high_school: ["高中", "High school"],
  associate: ["专科", "Associate"],
  bachelor: ["本科", "Bachelor"],
  master: ["硕士", "Master"],
  doctor: ["博士", "Doctorate"],
  other: ["其他", "Other"],
  manual: ["手动填写", "Manual"],
  memory: ["已确认资料", "Confirmed information"],
  profile: ["基础档案", "Profile"],
  self_report: ["本人自述", "Self report"],
  document_fact: ["文档陈述", "Document statement"],
  inference: ["推断", "Inference"],
  project: ["项目", "Project"],
  experience: ["实践经历", "Experience"],
  contact: ["联系方式", "Contact"],
  skill: ["技能", "Skill"],
  pending: ["待开始", "Pending"],
  in_progress: ["进行中", "In progress"],
  completed: ["已完成", "Completed"],
  draft: ["草稿", "Draft"],
  active: ["使用中", "Active"],
  confirmed_status: ["已确认", "Confirmed"],
  withdrawn: ["已撤回", "Withdrawn"],
  failed: ["失败", "Failed"],
  unknown: ["未知", "Unknown"],
  verified: ["已验证", "Verified"],
  unverified: ["未验证", "Unverified"],
  closed: ["已关闭", "Closed"],
  current: ["当前有效", "Current"],
  source_withdrawn: ["来源已撤回", "Source withdrawn"],
  needs_reconfirmation: ["需要重新确认", "Needs confirmation"],
  action_label: ["行动", "Action"],
  created_at: ["创建时间", "Created"],
  start: ["开始", "Start"],
  pause: ["暂停", "Pause"],
  complete: ["完成学习", "Complete study"],
  skip: ["跳过", "Skip"],
  lock: ["锁定", "Lock"],
  unlock: ["解锁", "Unlock"],
  defer: ["延期", "Defer"],
  split: ["拆分", "Split"],
  attach: ["关联材料", "Attach material"],
  material_id: ["材料", "Material"],
  q: ["关键词", "Keywords"],
  query: ["检索词", "Search query"],
  location: ["城市", "City"],
  company: ["公司", "Company"],
  salary_min: ["最低薪资", "Minimum salary"],
  salary_unit: ["薪资周期", "Salary period"],
  month: ["月", "Month"],
  day: ["日", "Day"],
  task_id: ["关联任务（可选）", "Related task (optional)"],
  hour: ["提醒时间（整点）", "Reminder hour"],
  weekday: ["周报星期", "Weekly report day"],
  month_day: ["月报日期", "Monthly report day"],
  quiet_start: ["免打扰开始（整点）", "Quiet hours start"],
  quiet_end: ["免打扰结束（整点）", "Quiet hours end"],
  daily_limit: ["每日提醒上限", "Daily reminder limit"],
  daily_learning: ["每日学习提醒", "Daily learning reminders"],
  deadline_check: ["岗位截止提醒", "Job deadline reminders"],
  weekly_report: ["每周报告", "Weekly report"],
  monthly_report: ["每月报告", "Monthly report"],
  spaced_review: ["复习提醒", "Review reminders"],
  employment_type: ["用工类型", "Employment type"],
  duration: ["时长", "Duration"],
  graduation_cohort: ["毕业届别", "Graduation cohort"],
  deadline_before: ["截止日期早于", "Deadline before"],
  answers: ["选择答案", "Answers"],
  questions: ["题目", "Questions"],
  options: ["选项", "Options"],
  pending_verification: ["待考核", "Awaiting assessment"],
  passed: ["已通过", "Passed"],
  needs_review: ["需要复习", "Needs review"],
  skipped: ["已跳过", "Skipped"],
  question_id: ["题目", "Question"],
  choice: ["选项", "Choice"],
  skipped_field: ["跳过此题", "Skip this question"],
  decisions: ["复习行动", "Review actions"],
  mode: ["模式", "Mode"],
  goals: ["目标", "Goals"],
  purpose: ["用途", "Purpose"],
  granted: ["允许处理", "Allow processing"],
  message_type: ["消息类型", "Message type"],
  cancelled: ["已取消", "Cancelled"],
  queued: ["等待执行", "Queued"],
  running: ["正在执行", "Running"],
  waiting_confirmation: ["等待确认", "Waiting for confirmation"],
  success: ["已完成", "Completed"],
  source_deleted: ["来源已删除", "Source deleted"],
  text_reference: ["文字引用", "Text reference"],
  github_reference: ["GitHub 引用", "GitHub reference"],
  situation_prompt: [
    "描述背景与个人行动，不补造指标",
    "Describe context and your own actions without inventing metrics",
  ],
};
export function useLabel() {
  const t = useText();
  return (key: string) =>
    words[key] ? t(...words[key]) : key.replaceAll("_", " ");
}

export function useResource(path: string | null, revision = 0) {
  const [state, setState] = useState<{
    data: Json;
    loading: boolean;
    error: string;
  }>({ data: null, loading: !!path, error: "" });
  const [retry, setRetry] = useState(0);
  const generation = useRef(0);
  const previousPath = useRef(path);
  const locale = useContext(Locale);
  useEffect(() => {
    const current = ++generation.current;
    const controller = new AbortController();
    if (!path) {
      setState({ data: null, loading: false, error: "" });
      return;
    }
    const samePath = previousPath.current === path;
    previousPath.current = path;
    setState((previous) => ({
      data: samePath ? previous.data : null,
      loading: true,
      error: "",
    }));
    request(path, { signal: controller.signal })
      .then((data) => {
        if (current === generation.current)
          setState({ data, loading: false, error: "" });
      })
      .catch((error: Error) => {
        if (!controller.signal.aborted && current === generation.current)
          setState({ data: null, loading: false, error: error.message });
      });
    return () => controller.abort();
  }, [path, revision, retry, locale]);
  return { ...state, refresh: () => setRetry((x) => x + 1) };
}

export function Action({
  children,
  run,
  onDone,
  className = "",
  disabled = false,
  successMessage,
}: {
  children: ReactNode;
  run: () => Promise<unknown>;
  onDone?: () => void;
  className?: string;
  disabled?: boolean;
  successMessage?: string;
}) {
  const t = useText();
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState("");
  const notify = useContext(Feedback);
  const inFlight = useRef(false);
  return (
    <span className="action">
      <button
        type="button"
        className={className}
        disabled={busy || disabled}
        onClick={async () => {
          if (inFlight.current) return;
          inFlight.current = true;
          setBusy(true);
          setFeedback("");
          try {
            await run();
            const message =
              successMessage || t("操作已完成", "Action completed");
            setFeedback(message);
            notify(message);
            onDone?.();
          } catch (error) {
            const message =
              error instanceof Error
                ? error.message
                : t("操作失败", "Action failed");
            setFeedback(message);
            notify(message);
          } finally {
            inFlight.current = false;
            setBusy(false);
          }
        }}
      >
        {busy ? t("处理中…", "Working…") : children}
      </button>
      <span role="status">{feedback}</span>
    </span>
  );
}

const hidden = new Set([
  "id",
  "user_id",
  "tenant_id",
  "payload_json",
  "trace_id",
  "hash",
  "content_hash",
  "source_hash",
  "request_hash",
  "graph_version",
  "run_id",
  "sequence",
  "task_version",
]);
export function DataView({
  value,
  depth = 0,
}: {
  value: Json | undefined;
  depth?: number;
}) {
  const label = useLabel();
  const t = useText();
  if (value === undefined || value === null || value === "")
    return <span className="muted">{t("未填写", "Not provided")}</span>;
  if (typeof value === "boolean")
    return <span>{value ? t("是", "Yes") : t("否", "No")}</span>;
  if (typeof value !== "object")
    return (
      <span dir="auto">
        {typeof value === "string" && words[value]
          ? label(value)
          : String(value)}
      </span>
    );
  if (Array.isArray(value))
    return value.length ? (
      <ul className="values">
        {value.map((item, i) => (
          <li key={i}>
            <DataView value={item} depth={depth + 1} />
          </li>
        ))}
      </ul>
    ) : (
      <span className="muted">{t("暂无", "None yet")}</span>
    );
  const fields = Object.entries(object(value)).filter(
    ([key, val]) =>
      !hidden.has(key) && val !== undefined && key !== "candidates",
  );
  if (depth > 5)
    return (
      <span>
        {t("查看相关记录了解详情", "See the related record for details")}
      </span>
    );
  return (
    <dl className="details">
      {fields.map(([key, val]) => (
        <div key={key}>
          <dt>{label(key)}</dt>
          <dd>
            <DataView value={val} depth={depth + 1} />
          </dd>
        </div>
      ))}
    </dl>
  );
}

export function Loading({
  error,
  retry,
}: {
  error?: string;
  retry?: () => void;
}) {
  const t = useText();
  return (
    <div
      className={error ? "resource-state resource-error" : "resource-state"}
      role={error ? "alert" : "status"}
    >
      <span>{error || t("正在读取…", "Loading…")}</span>
      {error && retry && (
        <button className="secondary" onClick={retry}>
          {t("重试", "Retry")}
        </button>
      )}
    </div>
  );
}
export function title(data: Data) {
  const fields = object(data.fields);
  return string(
    data.title ||
      data.name ||
      object(fields.title).value ||
      object(data.template).title ||
      object(data.job).title ||
      data.text ||
      data.summary ||
      data.filename ||
      data.skill_id ||
      data.start_date ||
      data.created_at ||
      data.kind,
  );
}
