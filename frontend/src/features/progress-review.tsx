import { useContext, useEffect, useRef, useState } from "react";
import {
  array,
  command,
  number,
  object,
  records,
  string,
  type Data,
} from "../shared/api";
import { Feedback, Loading, useResource, useText } from "../shared/ui";

export function ProgressReview({ report }: { report: Data }) {
  const t = useText();
  const learning = object(report.learning);
  const minutes = number(learning.self_reported_minutes);
  const completed = array(learning.completed_tasks).length;
  const overdue = array(learning.overdue_pending).length;
  const evidence = object(report.evidence);
  const added = array(evidence.new_records).length;
  const verified = array(evidence.currently_verified_foundation).length;
  const funnel = object(report.funnel);
  const counts = object(funnel.counts);
  const created = number(counts.created);
  const unconfirmed = array(funnel.unsubmitted_or_unconfirmed_ids).length;
  const changes = array(report.job_changes).length;
  const hours = Math.floor(minutes / 60);
  const remainder = minutes % 60;
  const duration = hours
    ? t(
        `${hours} 小时${remainder ? ` ${remainder} 分钟` : ""}`,
        `${hours} h${remainder ? ` ${remainder} min` : ""}`,
      )
    : t(`${minutes} 分钟`, `${minutes} min`);
  return (
    <article
      className="progress-review"
      aria-label={t("复盘解读", "Your progress review")}
    >
      <section>
        <h3>{t("学习进展", "Learning")}</h3>
        <p>
          {minutes || completed
            ? t(
                `这段时间，你记录了 ${duration}的学习，完成了 ${completed} 项任务。`,
                `You logged ${duration} of study and completed ${completed} tasks during this period.`,
              )
            : t(
                "这段时间还没有学习时长或任务完成记录。学过的内容可以到学习区补记。",
                "No study time or completed tasks have been recorded for this period. You can add a study entry in Learning.",
              )}
        </p>
        {overdue > 0 && (
          <p className="review-attention">
            {t(
              `所选期间安排的任务中，还有 ${overdue} 项已逾期。可以先看哪些需要继续，哪些需要调整时间。`,
              `${overdue} tasks scheduled in this period are now overdue. Review which to continue and which to reschedule.`,
            )}
          </p>
        )}
      </section>
      <section>
        <h3>{t("能力积累", "Skills")}</h3>
        <p>
          {added
            ? t(
                `这段时间新增了 ${added} 条能力证据。`,
                `${added} skill evidence records were added during this period.`,
              )
            : t(
                "这段时间没有新增能力证据。完成测验或项目评估后，这里会更新。",
                "No new skill evidence was recorded during this period. Assessments and project evaluations can add evidence.",
              )}{" "}
          {verified
            ? t(
                `目前有 ${verified} 项基础技能通过验证。`,
                `${verified} foundation skills are currently verified.`,
              )
            : t(
                "目前还没有通过验证的基础技能。",
                "No foundation skills are currently verified.",
              )}
        </p>
      </section>
      <section>
        <h3>{t("求职进展", "Job search")}</h3>
        <p>
          {created
            ? t(
                `这段时间新建了 ${created} 份申请，其中 ${number(counts.submitted)} 份已确认投递，${number(counts.interviewing)} 份进入过面试，${number(counts.offer)} 份收到过录用。`,
                `You created ${created} applications during this period: ${number(counts.submitted)} have confirmed submissions, ${number(counts.interviewing)} reached interviews and ${number(counts.offer)} received offers.`,
              )
            : t(
                "这段时间没有新建申请记录。",
                "No applications were created during this period.",
              )}
        </p>
        {unconfirmed > 0 && (
          <p>
            {t(
              `其中 ${unconfirmed} 份尚未投递或缺少确认回执，可以到求职区核对。`,
              `${unconfirmed} of these applications are unsubmitted or lack a confirmed receipt. Check them in Career.`,
            )}
          </p>
        )}
        {changes > 0 && (
          <p>
            {t(
              `岗位信息发生过 ${changes} 次变更，申请前记得核对最新要求与截止日期。`,
              `${changes} job information changes were recorded. Check current requirements and deadlines before applying.`,
            )}
          </p>
        )}
      </section>
      <div className="review-links">
        <a href="/app/learn">
          {t("继续学习与记录", "Continue learning and logging")}
        </a>
        <a href="/app/career">{t("查看求职记录", "View applications")}</a>
      </div>
      <details className="review-basis">
        <summary>
          {t("这些数字怎么算", "How these numbers are counted")}
        </summary>
        <p>
          {t(
            "学习时长来自你的投入记录，不包含计划安排的分钟数。完成任务按首次完成时间统计；逾期项来自当前已发布计划。",
            "Study time comes from your logs, excluding planned time. Tasks count when first completed; overdue tasks come from the currently published plan.",
          )}
        </p>
        <p>
          {t(
            "新增证据不等于能力已通过验证；验证状态以当前有效考核为准。求职进展只跟进所选期间新建的申请，观察到结束日期或今天（取较早者），投递回执由你确认。",
            "New evidence does not automatically verify a skill; verification depends on current valid assessments. Job search figures follow applications created in the selected period up to the earlier of its end date or today. Submission receipts are confirmed by you.",
          )}
        </p>
      </details>
    </article>
  );
}

export function Reflection({
  start,
  end,
  revision,
  onSaved,
}: {
  start: string;
  end: string;
  revision: number;
  onSaved: () => void;
}) {
  const t = useText();
  const notify = useContext(Feedback);
  const [text, setText] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState("");
  const guard = useRef(false);
  const resource = useResource("/quarter-reviews", revision);
  useEffect(() => setConfirmed(false), [start, end]);
  const notes = records(resource.data || []);
  const renderNote = (note: Data) => {
    const period = object(object(note.report).period);
    return (
      <article className="reflection-note" key={string(note.id)}>
        <p className="review-period">
          {string(period.start)} — {string(period.end)}
        </p>
        <p dir="auto">{string(note.reflection)}</p>
      </article>
    );
  };
  return (
    <div className="reflection">
      <p className="review-period">
        {t(`记录期间：${start} — ${end}`, `Save for: ${start} — ${end}`)}
      </p>
      <form
        onSubmit={async (event) => {
          event.preventDefault();
          if (guard.current || !confirmed) return;
          if (!text.trim()) {
            setFeedback(t("请先写下反思", "Please write a reflection first"));
            return;
          }
          guard.current = true;
          setBusy(true);
          setFeedback("");
          try {
            await command("/quarter-reviews", {
              start,
              end,
              reflection: text.trim(),
              confirmed: true,
            });
            setText("");
            setConfirmed(false);
            const message = t("反思已保存", "Reflection saved");
            setFeedback(message);
            notify(message);
            onSaved();
          } catch (error) {
            const message =
              error instanceof Error
                ? error.message
                : t("保存失败，请重试", "Could not save. Please retry.");
            setFeedback(message);
            notify(message);
          } finally {
            guard.current = false;
            setBusy(false);
          }
        }}
      >
        <label>
          {t(
            "这段时间有什么收获，下次想调整什么？",
            "What did you learn, and what would you change next time?",
          )}
          <textarea
            required
            maxLength={4000}
            rows={3}
            value={text}
            disabled={busy}
            onChange={(event) => setText(event.target.value)}
          />
        </label>
        <label className="check">
          <input
            type="checkbox"
            required
            checked={confirmed}
            disabled={busy}
            onChange={(event) => setConfirmed(event.target.checked)}
          />
          {t("确认保存这段反思", "Confirm saving this reflection")}
        </label>
        <button disabled={busy}>
          {busy ? t("保存中…", "Saving…") : t("保存反思", "Save reflection")}
        </button>
        <p role="status">{feedback}</p>
      </form>
      {resource.loading || resource.error ? (
        <Loading error={resource.error} retry={resource.refresh} />
      ) : (
        <>
          {notes.slice(0, 3).map(renderNote)}
          {notes.length > 3 && (
            <details className="review-basis">
              <summary>{t("更早的反思", "Earlier reflections")}</summary>
              {notes.slice(3).map(renderNote)}
            </details>
          )}
        </>
      )}
    </div>
  );
}
