import { useContext, useState } from "react";
import { array, command, download, number, object, records, request, string, type Data } from "../shared/api";
import { Action, DataView, Loading, title, useLabel, useResource, useText } from "../shared/ui";
import { useSystemDate } from "../shared/dates";
import { informationTitle } from "./information-detail";
import { References, Revision } from "./references";
import { Collection, Disclosure, Form, InformationDetail, Reader, Subtabs } from "./workspace-components";
import { Notifications } from "./workspace-support";

function CareerOverview() {
  const t = useText();
  const today = useSystemDate();
  const { revision } = useContext(Revision);
  const applications = useResource("/applications", revision);
  const events = useResource("/calendar-events", revision);
  const notices = useResource("/notification-center", revision);
  const active = records(applications.data || []).filter(
    (item) =>
      !item.user_archived &&
      !["offer", "rejected", "withdrawn", "no_response"].includes(
        string(item.status),
      ),
  );
  const upcoming = records(events.data || [])
    .filter(
      (event) => event.kind === "interview" && string(event.date) >= today,
    )
    .sort((a, b) =>
      `${string(a.date)} ${string(a.time)}`.localeCompare(
        `${string(b.date)} ${string(b.time)}`,
      ),
    );
  return (
    <section
      className="career-overview"
      aria-label={t("求职近况", "Job search overview")}
    >
      <h2>{t("接下来", "Coming up")}</h2>
      {events.loading || events.error ? (
        <Loading error={events.error} retry={events.refresh} />
      ) : upcoming.length ? (
        <ul>
          {upcoming.slice(0, 3).map((event) => (
            <li key={string(event.id)}>
              <a href="/app/career#interviews">{string(event.title)}</a> ·{" "}
              {string(event.date)}{" "}
              {string(event.time) || t("时间待定", "Time not specified")}
            </li>
          ))}
        </ul>
      ) : (
        <p className="career-overview-empty">
          {t("近期没有已安排的面试。", "No upcoming interviews scheduled.")}
        </p>
      )}
      {events.data &&
        records(events.data)
          .filter(
            (event) =>
              event.kind === "job_deadline" &&
              string(event.date) >= today &&
              string(event.date) <=
                new Date(Date.parse(`${today}T00:00:00Z`) + 7 * 86400000)
                  .toISOString()
                  .slice(0, 10),
          )
          .slice(0, 3)
          .map((event) => (
            <p key={string(event.id)}>
              {t("岗位截止", "Job deadline")}：
              <a
                href={`/app/goals?record=${encodeURIComponent(string(event.id))}#jobs`}
              >
                {string(event.title)}
              </a>{" "}
              · {string(event.date)}
            </p>
          ))}
      {notices.loading || notices.error ? (
        <Loading error={notices.error} retry={notices.refresh} />
      ) : (
        records(notices.data || []).filter((notice) => !notice.read).length >
          0 && (
          <p>
            <a href="/app/career?notices=1#career-notices">
              {t(
                `有 ${records(notices.data || []).filter((notice) => !notice.read).length} 条未读站内提醒`,
                `${records(notices.data || []).filter((notice) => !notice.read).length} unread in-app reminders`,
              )}
            </a>
          </p>
        )
      )}
      {applications.loading || applications.error ? (
        <Loading error={applications.error} retry={applications.refresh} />
      ) : active.length ? (
        <p className="career-active-count">
          {t(
            `${active.length} 条进行中的申请`,
            `${active.length} active applications`,
          )}
        </p>
      ) : null}
    </section>
  );
}

function ApplicationContext({ application }: { application: Data }) {
  const t = useText();
  const { revision, changed } = useContext(Revision);
  const events = useResource("/calendar-events", revision);
  const reviews = useResource("/interview-reviews", revision);
  const [note, setNote] = useState("");
  const job = object(application.job);
  const related = records(events.data || []).filter(
    (event) => event.application_id === application.id,
  );
  return (
    <section className="application-context">
      <h3>{t("这次申请", "This application")}</h3>
      <p>
        {[string(job.company), string(job.title)].filter(Boolean).join(" · ")}
      </p>
      <p>
        {t("使用的简历", "Resume used")}：{string(application.resume_title)}
        {application.resume_sequence
          ? ` · ${t("版本", "Version")} ${application.resume_sequence}`
          : ""}{" "}
        {application.resume_version_id && (
          <a href="/app/career#resumes">{t("查看简历", "View resumes")}</a>
        )}
      </p>
      {job.id && (
        <p>
          <a
            href={`/app/goals?record=${encodeURIComponent(string(job.id))}#jobs`}
          >
            {t("查看岗位原文", "View job description")}
          </a>
        </p>
      )}
      {application.verification === "pending_verification" && (
        <p>
          {t(
            "已记录本人投递，尚未核验回执。",
            "Submission reported; receipt not yet verified.",
          )}
        </p>
      )}
      {events.loading || events.error ? (
        <Loading error={events.error} retry={events.refresh} />
      ) : (
        related.length > 0 && (
          <div>
            <strong>{t("关联面试", "Linked interviews")}</strong>
            <ul>
              {related.map((event) => (
                <li key={string(event.id)}>
                  {string(event.title)} · {string(event.date)}{" "}
                  {string(event.time)}
                </li>
              ))}
            </ul>
          </div>
        )
      )}
      {reviews.loading || reviews.error ? (
        <Loading error={reviews.error} retry={reviews.refresh} />
      ) : (
        records(reviews.data || [])
          .filter(
            (review) =>
              review.application_id === application.id &&
              review.status === "confirmed",
          )
          .map((review) => (
            <div key={string(review.id)}>
              <strong>{t("面试复盘", "Interview reflection")}</strong>
              <p>{string(review.summary)}</p>
              {review.basis === "user_reported_interview" && (
                <Disclosure label={t("编辑复盘", "Edit reflection")}>
                  <EditQuickReview review={review} />
                </Disclosure>
              )}
            </div>
          ))
      )}
      {[
        "reported_submitted",
        "submitted",
        "assessment",
        "interviewing",
      ].includes(string(application.status)) && (
        <Disclosure label={t("安排关联面试", "Schedule linked interview")}>
          <ScheduleInterview application={application} />
        </Disclosure>
      )}
      <Disclosure label={t("记录面试复盘", "Record interview reflection")}>
        <QuickReview applicationId={string(application.id)} />
      </Disclosure>
      {application.status === "awaiting_user_submission" && (
        <div className="application-quick-receipt">
          <h4>{t("记录本人已投递", "Record your submission")}</h4>
          <p>
            {t(
              "没有回执材料时，可先记录自述；申请仍显示待本人投递及待核验。",
              "Without receipt evidence, your report is saved pending verification and the application remains awaiting submission.",
            )}
          </p>
          <label>
            {t("备注（可选）", "Note (optional)")}
            <input
              maxLength={2000}
              value={note}
              onChange={(e) => setNote(e.target.value)}
            />
          </label>
          <Action
            run={() =>
              command(
                `/applications/${application.id}/receipt`,
                { note, confirmed: true },
                "POST",
                number(application.version),
              )
            }
            successMessage={t(
              "已记录本人投递，待核验。",
              "Submission report saved; awaiting verification.",
            )}
            onDone={() => {
              setNote("");
              changed();
            }}
          >
            {t("确认记录本人投递", "Confirm submission report")}
          </Action>
        </div>
      )}
    </section>
  );
}

export function Career() {
  const t = useText();
  const { changed } = useContext(Revision);
  return (
    <>
      <Subtabs
        labels={[
          ["申请记录", "Applications"],
          ["简历与材料", "Resumes and documents"],
          ["面试", "Interviews"],
        ]}
      >
        {(tab) =>
          tab === 1 ? (
            <>
              <p>
                <a href="/app/profile#facts">
                  {t(
                    "先在“我的资料”确认事实，再选择用于简历。",
                    "Confirm facts in My information, then select them for your resume.",
                  )}
                </a>
              </p>
              <div className="section-intro">
                <div>
                  <h2>{t("我的简历", "My resumes")}</h2>
                  <p>
                    {t(
                      "选择一份简历查看版本或继续完善。",
                      "Choose a resume to review its versions or make changes.",
                    )}
                  </p>
                </div>
              </div>
              <Collection
                path="/resumes"
                firstOpen
                empty={t(
                  "还没有简历，可以在下方创建，或请助手帮你整理。",
                  "No resume yet. Create one below or ask the assistant for help.",
                )}
              >
                {(resume) => (
                  <>
                    <ResumeVersions resume={resume} />
                    <Disclosure label={t("简历信息", "Resume details")}>
                      <InformationDetail
                        item={resume}
                        heading={informationTitle(resume)}
                      />
                    </Disclosure>
                    <Disclosure label={t("编辑简历", "Edit resume")}>
                      <Form
                        schema="careerpilot__hiring_api__ResumeInput"
                        path={`/resumes/${resume.id}/versions`}
                        version={number(resume.version)}
                        onDone={changed}
                      />
                    </Disclosure>
                    <Disclosure label={t("针对岗位定制", "Tailor for a job")}>
                      <p>
                        {t(
                          "按岗位要求调整已有事实的排列，不增加未经确认的经历；确认后生成新版本。",
                          "Reorders confirmed facts for the job without adding unconfirmed experience; creates a new version after confirmation.",
                        )}
                      </p>
                      <TailorPreview resume={resume} />
                    </Disclosure>
                  </>
                )}
              </Collection>
              <Disclosure label={t("创建简历", "Create resume")}>
                <Form schema="ResumeCreate" path="/resumes" onDone={changed} />
              </Disclosure>
              <Collection path="/application-drafts" hideEmpty />
              <Disclosure
                label={t("按需翻译简历", "Translate a resume when needed")}
              >
                <Form
                  schema="TranslationInput"
                  path="/resume-translations"
                  onDone={changed}
                />
                <Collection path="/resume-translations" />
              </Disclosure>
            </>
          ) : tab === 0 ? (
            <>
              <CareerOverview />
              <Disclosure label={t("添加申请", "Add application")}>
                <Disclosure label={t("添加岗位原文", "Add a job description")}>
                  <Form
                    schema="JobInput"
                    path="/jobs/import"
                    onDone={changed}
                  />
                </Disclosure>
                <Disclosure
                  label={t(
                    "记录已投递（外部简历）",
                    "Record a submission with an external resume",
                  )}
                >
                  <p>
                    {t(
                      "仅记录本人已投递的情况；外部简历不会成为系统简历版本，回执未核验。",
                      "Records your own submission report. The external resume is not a system resume version and the receipt is unverified.",
                    )}
                  </p>
                  <Form
                    schema="ExternalApplicationInput"
                    path="/applications/external"
                    onDone={changed}
                    label={t("确认记录投递", "Confirm submission report")}
                  />
                </Disclosure>
                <Disclosure label={t("准备申请", "Prepare an application")}>
                  <p>
                    {t(
                      "请先添加岗位并创建简历版本，再选择用于这次申请。",
                      "First add a job and create a resume version, then select them for this application.",
                    )}{" "}
                    <a href="/app/goals#jobs">{t("添加岗位", "Add a job")}</a> ·{" "}
                    <a href="/app/career#resumes">
                      {t("创建简历", "Create a resume")}
                    </a>
                  </p>
                  <Form
                    schema="careerpilot__hiring_api__ApplicationInput"
                    path="/applications"
                    onDone={changed}
                  />
                </Disclosure>
              </Disclosure>
              <h2 className="career-list-heading">
                {t("申请记录", "Applications")}
              </h2>
              <Collection
                path="/applications"
                empty={t(
                  "暂无进行中的申请。使用“添加申请”记录第一条。",
                  "No active applications. Use Add application to create one.",
                )}
              >
                {(application) => (
                  <>
                    <ApplicationContext application={application} />
                    <InformationDetail
                      item={application}
                      heading={informationTitle(application)}
                      omit={["verification"]}
                    />
                    <Disclosure
                      label={t("更新申请阶段", "Update application stage")}
                    >
                      <ApplicationTransition application={application} />
                    </Disclosure>
                    <Disclosure
                      label={t(
                        "补充投递回执材料",
                        "Add submission receipt evidence",
                      )}
                    >
                      <Form
                        schema="ReceiptInput"
                        path={`/applications/${application.id}/receipt`}
                        version={number(application.version)}
                        onDone={changed}
                      />
                    </Disclosure>
                    <Disclosure label={t("申请提醒", "Application reminder")}>
                      <QuickReminder application={application} />
                      <Disclosure label={t("自定义提醒", "Custom reminder")}>
                        <Form
                          schema="ReminderInput"
                          path={`/applications/${application.id}/reminders`}
                          onDone={changed}
                        />
                      </Disclosure>
                    </Disclosure>
                    <Disclosure label={t("申请历史", "Application history")}>
                      <Reader
                        path={`/applications/${application.id}/history`}
                      />
                    </Disclosure>
                  </>
                )}
              </Collection>
              <Disclosure
                label={t("提醒", "Reminders")}
                open={window.location.hash === "#career-notices"}
              >
                <Notifications />
              </Disclosure>
              <Disclosure
                label={t("按需比较 Offer", "Compare offers when needed")}
              >
                <Form
                  schema="OffersInput"
                  path="/offer-comparisons"
                  onDone={changed}
                />
                <Collection path="/offer-comparisons">
                  {(offer) => (
                    <>
                      <InformationDetail
                        item={offer}
                        heading={informationTitle(offer)}
                      />
                      <Form
                        schema="OfferDecisionInput"
                        path={`/offer-comparisons/${offer.id}/decision`}
                        version={number(offer.version)}
                        onDone={changed}
                      />
                    </>
                  )}
                </Collection>
              </Disclosure>
            </>
          ) : (
            <>
              <InterviewAppointments />
              <h2>{t("面试练习", "Interview practice")}</h2>
              <Disclosure label={t("开始文字面试", "Start a text interview")}>
                <Form
                  schema="SessionInput"
                  path="/interviews"
                  onDone={changed}
                />
              </Disclosure>
              <Collection path="/interviews">
                {(session) => <Interview id={string(session.id)} />}
              </Collection>
              <Disclosure
                label={t("面试复盘", "Interview reviews")}
                open={window.location.hash === "#interview-review"}
              >
                <p>
                  {t(
                    "本人面试复盘是自述，不等同客观考核。",
                    "Your interview reflection is self-reported, not an assessment.",
                  )}
                </p>
                <QuickReview />
                <Disclosure
                  label={t("添加复习技能和时间", "Add review skills and time")}
                >
                  <Form
                    schema="SelfReviewInput"
                    path="/interview-reviews/self-report"
                    onDone={changed}
                    label={t("保存本人复盘", "Save reflection")}
                  />
                </Disclosure>
                <Collection path="/interview-reviews">
                  {(review) => (
                    <>
                      <InformationDetail
                        item={review}
                        heading={informationTitle(review)}
                      />
                      <Form
                        schema="PreviewInput"
                        path={`/interview-reviews/${review.id}/plan-preview`}
                        version={number(review.version)}
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
    </>
  );
}

function InterviewAppointments() {
  const t = useText();
  const { revision } = useContext(Revision);
  const today = useSystemDate();
  const events = useResource("/calendar-events", revision);
  const interviews = records(events.data || []).filter(
    (event) => event.kind === "interview",
  );
  return (
    <section aria-label={t("面试安排", "Interview appointments")}>
      <h2>{t("我的面试", "My interviews")}</h2>
      <p>
        {t(
          "从申请记录安排关联面试，也可以在这里添加独立本地日程。",
          "Schedule a linked interview from an application, or add a separate local appointment here.",
        )}
      </p>
      <Disclosure
        label={t("添加本地面试安排", "Add a local interview appointment")}
      >
        <Form
          schema="CalendarEventInput"
          path="/calendar-events"
          defaults={{ kind: "interview" }}
          onDone={() => {}}
        />
      </Disclosure>
      {events.loading || events.error ? (
        <Loading error={events.error} retry={events.refresh} />
      ) : interviews.length ? (
        <>
          <h3>{t("即将进行", "Upcoming")}</h3>
          {interviews.some((event) => string(event.date) >= today) ? (
            <ul>
              {interviews
                .filter((event) => string(event.date) >= today)
                .map((event) => (
                  <AppointmentItem key={string(event.id)} event={event} />
                ))}
            </ul>
          ) : (
            <p>{t("近期没有面试。", "No upcoming interviews.")}</p>
          )}
          {interviews.some((event) => string(event.date) < today) && (
            <Disclosure label={t("已结束的面试", "Past interviews")}>
              <ul>
                {interviews
                  .filter((event) => string(event.date) < today)
                  .map((event) => (
                    <AppointmentItem key={string(event.id)} event={event} />
                  ))}
              </ul>
            </Disclosure>
          )}
        </>
      ) : (
        <p>{t("暂无面试安排。", "No interview appointments yet.")}</p>
      )}
    </section>
  );
}

function AppointmentItem({ event }: { event: Data }) {
  const t = useText();
  const { changed } = useContext(Revision);
  const [confirmed, setConfirmed] = useState(false);
  return (
    <li>
      <strong>{string(event.title)}</strong> · {string(event.date)}{" "}
      {string(event.time) || t("时间待定", "Time not specified")} ·{" "}
      {string(event.timezone)}
      {event.preparation_note && <p>{string(event.preparation_note)}</p>}
      <p>
        {event.application_id ? (
          <a
            href={`/app/career?record=${encodeURIComponent(string(event.application_id))}#applications`}
          >
            {t("查看申请与复盘", "View application and reflection")}
          </a>
        ) : (
          <a href="/app/career#interview-review">
            {t("记录面试复盘", "Record interview reflection")}
          </a>
        )}
      </p>
      <Disclosure label={t("修改面试安排", "Edit interview appointment")}>
        <Form
          schema="CalendarEventInput"
          path={`/calendar-events/${event.id}`}
          method="PUT"
          version={number(event.version)}
          defaults={event}
          onDone={changed}
        />
      </Disclosure>
      <Disclosure label={t("移除面试安排", "Remove interview appointment")}>
        <p>
          {t(
            "仅移除此日程，不改变申请阶段。",
            "Only this appointment is removed; the application stage is unchanged.",
          )}
        </p>
        <label className="check">
          <input
            type="checkbox"
            checked={confirmed}
            onChange={(e) => setConfirmed(e.target.checked)}
          />
          {t("确认移除此安排", "Confirm removal")}
        </label>
        <Action
          disabled={!confirmed}
          run={() =>
            command(
              `/calendar-events/${event.id}`,
              {},
              "DELETE",
              number(event.version),
            )
          }
          onDone={changed}
        >
          {t("确认移除", "Remove appointment")}
        </Action>
      </Disclosure>
    </li>
  );
}

function ScheduleInterview({ application }: { application: Data }) {
  const t = useText();
  const { revision, changed } = useContext(Revision);
  const settings = useResource("/notification-settings", revision);
  const [date, setDate] = useState("");
  const [time, setTime] = useState("");
  const [note, setNote] = useState("");
  const [preview, setPreview] = useState<Data>();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const zone = string(object(settings.data).timezone);
  const args = {
    version: number(application.version),
    date,
    time: time || null,
    timezone: zone,
    preparation_note: note,
  };
  function clear() {
    setPreview(undefined);
    setError("");
  }
  return (
    <div>
      <p>
        {t(
          "确认后保存本地面试日程，并将申请更新到面试中。",
          "Confirmation saves a local appointment and updates the application to interviewing.",
        )}
      </p>
      {settings.loading || settings.error ? (
        <Loading error={settings.error} retry={settings.refresh} />
      ) : (
        <>
          <label>
            {t("日期", "Date")}
            <input
              type="date"
              value={date}
              disabled={busy}
              onChange={(e) => {
                setDate(e.target.value);
                clear();
              }}
            />
          </label>
          <label>
            {t("时间（可选）", "Time (optional)")}
            <input
              type="time"
              value={time}
              disabled={busy}
              onChange={(e) => {
                setTime(e.target.value);
                clear();
              }}
            />
          </label>
          <p>
            {t("时区", "Time zone")}：{zone}
          </p>
          <label>
            {t("准备备注（可选）", "Preparation note (optional)")}
            <textarea
              maxLength={2000}
              value={note}
              disabled={busy}
              onChange={(e) => {
                setNote(e.target.value);
                clear();
              }}
            />
          </label>
          <button
            type="button"
            disabled={!date || !zone || busy}
            onClick={async () => {
              setBusy(true);
              clear();
              try {
                setPreview(
                  object(
                    await command(
                      `/applications/${application.id}/interview-preview`,
                      args,
                    ),
                  ),
                );
              } catch (e) {
                setError(
                  e instanceof Error
                    ? e.message
                    : t("预览失败", "Preview failed"),
                );
              } finally {
                setBusy(false);
              }
            }}
          >
            {t("核对安排", "Review appointment")}
          </button>
          {error && <p role="alert">{error}</p>}
          {preview && (
            <section>
              <h4>{t("请核对面试安排", "Review interview appointment")}</h4>
              <p>
                {string(object(object(preview.preview).job).company)} ·{" "}
                {string(object(object(preview.preview).job).title)}
              </p>
              <p>
                {date} · {time || t("时间待定", "Time not specified")} · {zone}
              </p>
              <p>{note}</p>
              <Action
                run={() =>
                  command(`/applications/${application.id}/interview`, {
                    ...args,
                    preview_hash: string(preview.context_hash),
                    confirmed: true,
                  })
                }
                successMessage={t(
                  "面试安排已保存。",
                  "Interview appointment saved.",
                )}
                onDone={() => {
                  setPreview(undefined);
                  changed();
                }}
              >
                {t("确认安排面试", "Confirm interview appointment")}
              </Action>
            </section>
          )}
        </>
      )}
    </div>
  );
}

function ApplicationTransition({ application }: { application: Data }) {
  const t = useText();
  const label = useLabel();
  const { changed } = useContext(Revision);
  const targets: Record<string, string[]> = {
    draft: ["prepared", "withdrawn"],
    prepared: ["awaiting_user_submission", "withdrawn"],
    awaiting_user_submission: ["withdrawn"],
    submitted: [
      "assessment",
      "interviewing",
      "rejected",
      "withdrawn",
      "no_response",
    ],
    reported_submitted: [
      "assessment",
      "interviewing",
      "rejected",
      "withdrawn",
      "no_response",
    ],
    assessment: ["interviewing", "rejected", "withdrawn"],
    interviewing: ["offer", "rejected", "withdrawn"],
  };
  const allowed = targets[string(application.status)] || [];
  const [target, setTarget] = useState("");
  const [note, setNote] = useState("");
  const [checks, setChecks] = useState<Record<string, boolean>>({});
  const checksList: [string, string, string][] = [
    ["facts", "事实无误", "Facts reviewed"],
    ["contact", "联系方式无误", "Contact reviewed"],
    ["requirements", "已核对岗位要求", "Requirements reviewed"],
    ["privacy", "已核对个人信息范围", "Privacy reviewed"],
    ["source_reviewed", "已核对岗位来源", "Source reviewed"],
  ];
  const ready =
    target &&
    (target !== "prepared" || checksList.every(([key]) => checks[key]));
  return allowed.length ? (
    <div className="application-transition">
      <label>
        {t("下一阶段", "Next stage")}
        <select
          value={target}
          onChange={(event) => setTarget(event.target.value)}
        >
          <option value="">{t("请选择", "Select")}</option>
          {allowed.map((value) => (
            <option key={value} value={value}>
              {label(value)}
            </option>
          ))}
        </select>
      </label>
      <label>
        {t("备注（可选）", "Note (optional)")}
        <textarea
          maxLength={2000}
          value={note}
          onChange={(event) => setNote(event.target.value)}
        />
      </label>
      {target === "prepared" && (
        <fieldset>
          <legend>{t("申请前核对", "Before applying")}</legend>
          {checksList.map(([key, zh, en]) => (
            <label className="check" key={key}>
              <input
                type="checkbox"
                checked={!!checks[key]}
                onChange={(event) =>
                  setChecks({ ...checks, [key]: event.target.checked })
                }
              />
              {t(zh, en)}
            </label>
          ))}
        </fieldset>
      )}
      <Action
        disabled={!ready}
        run={() =>
          command(
            `/applications/${application.id}/transition`,
            {
              target,
              note,
              confirmed: true,
              ...(target === "prepared" ? { checklist: checks } : {}),
            },
            "POST",
            number(application.version),
          )
        }
        successMessage={t("申请阶段已更新。", "Application stage updated.")}
        onDone={() => {
          setTarget("");
          setNote("");
          setChecks({});
          changed();
        }}
      >
        {t("确认更新阶段", "Confirm stage update")}
      </Action>
    </div>
  ) : (
    <p>
      {t(
        "此申请已结束，没有可更新的阶段。",
        "This application is closed; no further stages are available.",
      )}
    </p>
  );
}

function EditQuickReview({ review }: { review: Data }) {
  const t = useText();
  const { changed } = useContext(Revision);
  const [summary, setSummary] = useState(string(review.summary));
  return (
    <div>
      <label>
        {t("面试复盘", "Interview reflection")}
        <textarea
          rows={4}
          maxLength={4000}
          value={summary}
          onChange={(event) => setSummary(event.target.value)}
        />
      </label>
      <Action
        disabled={!summary.trim()}
        run={() =>
          command(
            `/interview-reviews/${review.id}/self-report`,
            {
              status: string(review.status),
              summary: summary.trim(),
              application_id: review.application_id || null,
              decisions: array(review.decisions),
              confirmed: true,
            },
            "PUT",
            number(review.version),
          )
        }
        successMessage={t("复盘已更新。", "Reflection updated.")}
        onDone={changed}
      >
        {t("保存修改", "Save changes")}
      </Action>
    </div>
  );
}

function QuickReview({ applicationId = "" }: { applicationId?: string }) {
  const t = useText();
  const { changed } = useContext(Revision);
  const options = useContext(References);
  const [summary, setSummary] = useState("");
  const [application, setApplication] = useState(applicationId);
  return (
    <div>
      {!applicationId && (
        <label>
          {t("关联申请（可选）", "Link an application (optional)")}
          <select
            value={application}
            onChange={(e) => setApplication(e.target.value)}
          >
            <option value="">{t("不关联", "No application")}</option>
            {options.application_id?.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </label>
      )}
      <label>
        {t(
          "面试中问了什么、哪些地方需要改进？",
          "What were you asked and what would you improve?",
        )}
        <textarea
          rows={4}
          maxLength={4000}
          value={summary}
          onChange={(e) => setSummary(e.target.value)}
        />
      </label>
      <Action
        disabled={!summary.trim()}
        run={() =>
          command("/interview-reviews/self-report", {
            status: "confirmed",
            summary: summary.trim(),
            application_id: application || null,
            decisions: [],
            confirmed: true,
          })
        }
        successMessage={t("本人复盘已保存。", "Reflection saved.")}
        onDone={() => {
          setSummary("");
          changed();
        }}
      >
        {t("保存本人复盘", "Save reflection")}
      </Action>
    </div>
  );
}

function QuickReminder({ application }: { application: Data }) {
  const t = useText();
  const { revision, changed } = useContext(Revision);
  const settings = useResource("/notification-settings", revision);
  const config = object(settings.data);
  const zone = string(config.timezone);
  function target(days: number) {
    const parts = new Intl.DateTimeFormat("en-CA", {
      timeZone: zone,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    }).formatToParts(new Date());
    const part = (type: string) =>
      parts.find((p) => p.type === type)?.value || "";
    const day = new Date(
      Date.UTC(
        Number(part("year")),
        Number(part("month")) - 1,
        Number(part("day")) + days,
      ),
    );
    return `${day.toISOString().slice(0, 10)}T09:00`;
  }
  return (
    <div>
      <p>
        {t(
          "站内提醒需本地服务运行才能处理，不会发送系统或日历通知。",
          "In-app reminders require local services to run; no system or calendar notification is sent.",
        )}
      </p>
      {settings.loading || settings.error ? (
        <Loading error={settings.error} retry={settings.refresh} />
      ) : (
        <>
          <p>
            {t("时区", "Time zone")}：{zone} · {t("免打扰", "Quiet hours")}：
            {number(config.quiet_start)}:00–{number(config.quiet_end)}:00
          </p>
          <div className="actions">
            {[1, 7].map((days) => (
              <Action
                key={days}
                disabled={
                  !zone ||
                  ["offer", "rejected", "withdrawn", "no_response"].includes(
                    string(application.status),
                  )
                }
                run={() =>
                  command(`/applications/${application.id}/reminders`, {
                    kind: "follow_up",
                    local_time: target(days),
                    timezone: zone,
                    quiet_start: number(config.quiet_start),
                    quiet_end: number(config.quiet_end),
                    confirmed: true,
                  })
                }
                successMessage={t(
                  "站内跟进提醒已设置。",
                  "In-app follow-up reminder set.",
                )}
                onDone={changed}
              >
                {days === 1
                  ? t("明天上午提醒", "Remind me tomorrow morning")
                  : t("一周后提醒", "Remind me in a week")}
              </Action>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

function TailorPreview({ resume }: { resume: Data }) {
  const t = useText();
  const { changed, revision } = useContext(Revision);
  const versions = useResource(`/resumes/${resume.id}/versions`, revision);
  const options = useContext(References);
  const [job, setJob] = useState("");
  const [preview, setPreview] = useState<Data>();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  return (
    <div>
      <label>
        {t("目标岗位", "Target job")}
        <select
          disabled={busy}
          value={job}
          onChange={(event) => {
            setJob(event.target.value);
            setPreview(undefined);
            setError("");
          }}
        >
          <option value="">{t("请选择", "Select")}</option>
          {options.job_id?.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      </label>
      <button
        type="button"
        disabled={!job || busy}
        onClick={async () => {
          setBusy(true);
          setError("");
          setPreview(undefined);
          try {
            setPreview(
              object(
                await request(
                  `/resumes/${resume.id}/tailor-preview?job_id=${encodeURIComponent(job)}`,
                ),
              ),
            );
          } catch (e) {
            setError(
              e instanceof Error ? e.message : t("预览失败", "Preview failed"),
            );
          } finally {
            setBusy(false);
          }
        }}
      >
        {t("预览调整", "Preview changes")}
      </button>
      {error && <p role="alert">{error}</p>}
      {preview && (
        <section>
          <h4>{t("确认后的事实顺序", "Fact order after tailoring")}</h4>
          <p>
            {[
              string(object(preview.job).company),
              string(object(preview.job).title),
            ]
              .filter(Boolean)
              .join(" · ")}
          </p>
          <ol>
            {array(preview.facts)
              .map(object)
              .map((fact, index) => {
                const current = records(versions.data || []).find(
                  (item) => item.id === resume.current_version_id,
                );
                const oldIndex = array(current?.facts)
                  .map(object)
                  .findIndex((item) => item.id === fact.id);
                return (
                  <li key={string(fact.id)}>
                    {string(fact.text)}{" "}
                    {oldIndex >= 0 && oldIndex !== index && (
                      <small>
                        {t(
                          `（原第 ${oldIndex + 1} 项）`,
                          ` (was item ${oldIndex + 1})`,
                        )}
                      </small>
                    )}
                  </li>
                );
              })}
          </ol>
          <p>
            {t(
              "仅调整已有事实的顺序，不补造缺失的内容。",
              "Only existing facts are reordered; missing information is not invented.",
            )}
          </p>
          <Action
            run={() =>
              command(
                `/resumes/${resume.id}/tailor`,
                {
                  job_id: job,
                  job_version: number(object(preview.job).version),
                  confirmed: true,
                },
                "POST",
                number(preview.resume_version),
              )
            }
            onDone={() => {
              setPreview(undefined);
              changed();
            }}
          >
            {t("确认生成新版本", "Confirm new version")}
          </Action>
        </section>
      )}
    </div>
  );
}

function ResumeVersions({ resume }: { resume: Data }) {
  const t = useText();
  const { revision } = useContext(Revision);
  const versions = useResource(`/resumes/${resume.id}/versions`, revision);
  const applications = useResource("/applications", revision);
  if (versions.loading || versions.error)
    return <Loading error={versions.error} retry={versions.refresh} />;
  const items = records(versions.data || []);
  const current = items.find((item) => item.id === resume.current_version_id);
  const previous = items.filter(
    (item) => item.id !== resume.current_version_id,
  );
  const show = (version: Data) => (
    <>
      <h4>{t("简历预览", "Resume preview")}</h4>
      {array(version.facts)
        .map(object)
        .map((fact, i) => (
          <p key={string(fact.id) || i}>
            <strong>
              {t(
                (
                  {
                    name: "姓名",
                    contact: "联系方式",
                    summary: "简介",
                    education: "教育",
                    experience: "经历",
                    project: "项目",
                    skill: "技能",
                  } as Record<string, string>
                )[string(fact.kind)] || "事实",
                string(fact.kind),
              )}
              ：
            </strong>
            {string(fact.text)}
          </p>
        ))}
      {applications.loading || applications.error ? (
        <Loading error={applications.error} retry={applications.refresh} />
      ) : (
        <p>
          {t("关联申请", "Linked applications")}：
          {records(applications.data || [])
            .filter((item) => item.resume_version_id === version.id)
            .map((item) =>
              [string(object(item.job).company), string(object(item.job).title)]
                .filter(Boolean)
                .join(" · "),
            )
            .join(t("、", ", ")) || t("暂无", "None")}
        </p>
      )}
      <ResumeVersion version={version} />
    </>
  );
  return (
    <section>
      <h3>{t("当前版本", "Current version")}</h3>
      {current ? (
        show(current)
      ) : (
        <p>
          {t(
            "当前版本不可用，请刷新。",
            "Current version unavailable; refresh.",
          )}
        </p>
      )}
      {previous.length > 0 && (
        <Disclosure
          label={t(
            `历史版本（${previous.length}）`,
            `Previous versions (${previous.length})`,
          )}
        >
          {previous.map((version) => (
            <Disclosure
              key={string(version.id)}
              label={`${t("版本", "Version")} ${string(version.sequence)} · ${string(version.created_at).slice(0, 10)}`}
            >
              {show(version)}
            </Disclosure>
          ))}
        </Disclosure>
      )}
    </section>
  );
}

function ResumeCheck({ version }: { version: Data }) {
  const t = useText();
  const { revision } = useContext(Revision);
  const result = useResource(`/resume-versions/${version.id}/ats`, revision);
  if (result.loading || result.error)
    return <Loading error={result.error} retry={result.refresh} />;
  const data = object(result.data);
  const issues = array(data.issues).map(object);
  const keywords = array(data.missing_keywords).map(string).filter(Boolean);
  return (
    <div>
      {issues.length ? (
        <ul>
          {issues.map((issue, i) => (
            <li key={i}>
              {string(issue.message)} {string(issue.suggestion)}
            </li>
          ))}
        </ul>
      ) : (
        <p>{t("未发现规则检查问题。", "No issues found by the rule check.")}</p>
      )}
      {keywords.length > 0 && (
        <p>
          {t("岗位要求中未体现的关键词", "Job keywords not reflected")}：
          {keywords.join("、")}
        </p>
      )}
      <p>{string(data.notice)}</p>
    </div>
  );
}

function ResumeVersion({ version }: { version: Data }) {
  const t = useText();
  const { changed } = useContext(Revision);
  return (
    <>
      <InformationDetail item={version} heading={informationTitle(version)} />
      <div className="actions">
        {["pdf", "docx"].map((format) => (
          <Action
            className="secondary"
            key={format}
            run={() =>
              download(
                `/resume-versions/${version.id}/export/${format}`,
                `resume.${format}`,
                { confirmed: true },
              )
            }
          >
            {t("导出", "Export")} {format.toUpperCase()}
          </Action>
        ))}
      </div>
      <Disclosure label={t("简历检查", "Resume check")}>
        <ResumeCheck version={version} />
      </Disclosure>
      <Disclosure label={t("生成求职材料", "Create application material")}>
        <Form
          schema="careerpilot__hiring_api__DraftInput"
          path={`/resume-versions/${version.id}/drafts`}
          onDone={changed}
        />
      </Disclosure>
    </>
  );
}

function Interview({ id }: { id: string }) {
  const t = useText();
  const { revision, changed } = useContext(Revision);
  const resource = useResource(`/interviews/${id}`, revision);
  const session = object(resource.data);
  const [evaluation, setEvaluation] = useState<Data>();
  const questions = Object.keys(object(session.current_question)).length
    ? [object(session.current_question)]
    : [];
  return resource.loading || resource.error ? (
    <Loading error={resource.error} retry={resource.refresh} />
  ) : (
    <>
      <DataView value={session} />
      {session.status === "running" && (
        <Form
          schema="AnswerInput"
          path={`/interviews/${id}/answers`}
          version={number(session.version)}
          defaults={{ question_id: string(questions[0]?.id) }}
          options={{
            question_id: questions.map((q) => ({
              value: string(q.id),
              label: string(q.prompt || q.text),
            })),
            choice: array(questions[0]?.choices).map((choice, index) => ({
              value: String(index),
              label: string(choice),
            })),
          }}
          onDone={changed}
        />
      )}
      {["completed", "abandoned"].includes(string(session.status)) && (
        <Action
          run={async () => {
            setEvaluation(
              object(
                await command(
                  `/interviews/${id}/evaluate`,
                  { confirmed: true },
                  "POST",
                  number(session.version),
                ),
              ),
            );
            changed();
          }}
        >
          {t("生成面试复盘", "Review interview")}
        </Action>
      )}
      {evaluation && (
        <>
          <DataView value={evaluation} />
          <InterviewReview evaluation={evaluation} />
        </>
      )}
    </>
  );
}

function InterviewReview({ evaluation }: { evaluation: Data }) {
  const { revision, changed } = useContext(Revision);
  const resource = useResource("/interview-reviews", revision);
  const current = records(resource.data || []).find(
    (review) => review.evaluation_id === evaluation.id,
  );
  return resource.loading || resource.error ? (
    <Loading error={resource.error} retry={resource.refresh} />
  ) : (
    <Form
      schema="careerpilot__interview_api__ReviewInput"
      path={`/interview-evaluations/${evaluation.id}/review`}
      method="PUT"
      version={number(current?.version)}
      defaults={current}
      draft={{ kind: "interview", key: string(evaluation.id) }}
      onDone={changed}
    />
  );
}

