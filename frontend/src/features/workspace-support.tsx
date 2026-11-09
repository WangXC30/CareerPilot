import { useContext, useState } from "react";
import { array, command, number, object, records, string, type Data } from "../shared/api";
import { Action, DataView, Loading, title, useResource, useText } from "../shared/ui";
import { useSystemDate } from "../shared/dates";
import { ProgressReview, Reflection } from "./progress-review";
import { Revision } from "./references";
import { Disclosure } from "./workspace-components";

export function Notifications() {
  const t = useText();
  const { revision, changed } = useContext(Revision);
  const resource = useResource("/notification-center", revision);
  const [showAll, setShowAll] = useState(false);
  const notices = records(resource.data || []).filter(
    (notice) => showAll || !notice.read,
  );
  return (
    <section className="today-notices" aria-label={t("提醒", "Reminders")}>
      <div className="notice-tools">
        <button className="subtle" onClick={() => setShowAll(!showAll)}>
          {showAll
            ? t("只看未读", "Unread only")
            : t("全部提醒", "All reminders")}
        </button>
        <button className="subtle" onClick={resource.refresh}>
          {t("刷新", "Refresh")}
        </button>
      </div>
      {resource.loading || resource.error ? (
        <Loading error={resource.error} retry={resource.refresh} />
      ) : notices.length ? (
        notices.map((notice) => (
          <article className="notice-row" key={string(notice.id)}>
            <NoticeContent notice={notice} />
            {!notice.read && (
              <Action
                run={() =>
                  command(
                    notice.record_kind === "job_digest"
                      ? `/job-notifications/${notice.id}`
                      : notice.record_kind === "application_notice"
                        ? `/application-notifications/${notice.id}/read`
                        : `/personal-notices/${notice.id}/read`,
                    notice.record_kind === "job_digest"
                      ? { confirmed: true, read: true }
                      : { confirmed: true },
                    notice.record_kind === "job_digest" ? "PATCH" : "POST",
                    number(notice.version),
                  )
                }
                onDone={changed}
              >
                {t("标记已读", "Mark read")}
              </Action>
            )}
          </article>
        ))
      ) : (
        <p className="empty">
          {showAll
            ? t("暂无提醒。", "No reminders.")
            : t("暂无未读提醒。", "No unread reminders.")}
        </p>
      )}
    </section>
  );
}

function NoticeContent({ notice }: { notice: Data }) {
  const t = useText();
  const content = object(notice.content);
  const heading =
    string(
      content.title || notice.subscription_name || object(notice.job).title,
    ) || t("申请提醒", "Application reminder");
  const items = array(
    content.tasks || content.items || content.jobs || notice.entries,
  );
  const itemTitles = items.slice(0, 2).map(object).map(title).filter(Boolean);
  const period = object(object(content.report).period);
  const summary = items.length
    ? `${t(`共 ${items.length} 项`, `${items.length} items`)}${itemTitles.length ? ` · ${itemTitles.join(t("、", ", "))}` : ""}`
    : string(notice.message || content.notice) ||
      (period.start && period.end ? `${period.start} — ${period.end}` : "");
  const detail = notice.content || {
    message: notice.message,
    job: notice.job,
    entries: notice.entries,
  };
  return (
    <div className="notice-content">
      <strong dir="auto">{heading}</strong>
      {summary && (
        <p className="muted" dir="auto">
          {summary}
        </p>
      )}
      <details className="notice-detail">
        <summary>{t("查看详情", "View details")}</summary>
        {content.report ? (
          <ProgressReview report={object(content.report)} />
        ) : (
          <DataView value={detail} />
        )}
      </details>
    </div>
  );
}

function ReportSummary({ report }: { report: Data }) {
  const t = useText();
  const learning = object(report.learning);
  return (
    <dl className="review-summary">
      {[
        [
          t("学习分钟（本人记录）", "Study minutes (self-reported)"),
          number(learning.self_reported_minutes),
        ],
        [
          t("完成任务", "Completed tasks"),
          array(learning.completed_tasks).length,
        ],
        [
          t("逾期待办", "Overdue tasks"),
          array(learning.overdue_pending).length,
        ],
        [
          t("新增证据", "New evidence"),
          array(object(report.evidence).new_records).length,
        ],
      ].map(([label, value]) => (
        <div key={label}>
          <dt>{label}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  );
}

export function Report({ compact = false }: { compact?: boolean }) {
  const t = useText();
  const today = useSystemDate();
  const defaultStart = today.slice(0, 8) + "01";
  const [dates, setDates] = useState<{ start?: string; end?: string }>({});
  const start = dates.start ?? defaultStart;
  const end = dates.end ?? today;
  const [selectedRange, setRange] = useState<{ start: string; end: string }>();
  const range = selectedRange ?? { start: defaultStart, end: today };
  const { revision, changed } = useContext(Revision);
  const path = `/progress-report?start=${range.start}&end=${range.end}`;
  const resource = useResource(path, revision);
  const report = object(resource.data);
  const details = (
    <>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          setRange(
            start === defaultStart && end === today
              ? undefined
              : { start, end },
          );
          resource.refresh();
        }}
      >
        <div className="period">
          <label>
            {t("开始日期", "Start date")}
            <input
              type="date"
              value={start}
              max={end}
              required
              onChange={(e) => setDates({ start: e.target.value, end })}
            />
          </label>
          <label>
            {t("结束日期", "End date")}
            <input
              type="date"
              value={end}
              min={start}
              required
              onChange={(e) => setDates({ start, end: e.target.value })}
            />
          </label>
        </div>
        <button>{t("查看复盘", "View review")}</button>
        <button
          type="button"
          className="subtle"
          onClick={() => {
            setDates({});
            setRange(undefined);
            resource.refresh();
          }}
        >
          {t("使用系统日期", "Use system date")}
        </button>
        <p className="review-period">
          {t("系统日期：", "System date: ")}
          <time dateTime={today}>{today}</time>
        </p>
      </form>
      {resource.loading || resource.error ? (
        <Loading error={resource.error} retry={resource.refresh} />
      ) : (
        <ProgressReview report={report} />
      )}
      <Disclosure label={t("记录阶段反思", "Record a reflection")}>
        <Reflection
          start={range.start}
          end={range.end}
          revision={revision}
          onSaved={changed}
        />
      </Disclosure>
    </>
  );
  return (
    <section>
      {compact ? (
        <>
          <p className="review-period">
            {range.start} — {range.end}
          </p>
          {resource.loading || resource.error ? (
            <Loading error={resource.error} retry={resource.refresh} />
          ) : (
            <ReportSummary report={report} />
          )}
          <Disclosure label={t("详细复盘", "Full review")}>
            {details}
          </Disclosure>
        </>
      ) : (
        details
      )}
    </section>
  );
}

