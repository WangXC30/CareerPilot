import { useContext, useRef, useState } from "react";
import { command, object, type Data } from "../shared/api";
import type { Options } from "../shared/forms";
import { Locale, useLabel, useText } from "../shared/ui";

export function AssistantComposer({
  options,
  message,
  onMessageChange,
  onSendingChange,
  onAccepted,
  ensureConversation,
  blocked = false,
}: {
  options: Options;
  message: string;
  onMessageChange: (message: string) => void;
  onSendingChange: (sending: boolean) => void;
  onAccepted: (run: Data) => void;
  ensureConversation: () => Promise<string>;
  blocked?: boolean;
}) {
  const t = useText();
  const label = useLabel();
  const locale = useContext(Locale);
  const [intent, setIntent] = useState("auto");
  const [jobs, setJobs] = useState<string[]>([]);
  const [plan, setPlan] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const sending = useRef(false);
  const attempt = useRef<{ body: string; key: string } | undefined>(undefined);
  return (
    <form
      className="assistant-composer"
      onSubmit={async (event) => {
        event.preventDefault();
        if (sending.current || blocked) return;
        if (!message.trim()) {
          setError(t("请先输入消息。", "Enter a message first."));
          return;
        }
        const body = {
          message: message.trim(),
          intent,
          job_ids: jobs,
          plan_id: plan || null,
          require_confirmation: true,
          locale,
        };
        const encoded = JSON.stringify(body);
        if (attempt.current?.body !== encoded)
          attempt.current = { body: encoded, key: crypto.randomUUID() };
        sending.current = true;
        setBusy(true);
        onSendingChange(true);
        setError("");
        try {
          const conversation_id = await ensureConversation();
          const run = object(
            await command(
              "/workflows/runs",
              { ...body, conversation_id },
              "POST",
              undefined,
              attempt.current.key,
            ),
          );
          onAccepted(run);
          onMessageChange("");
          attempt.current = undefined;
        } catch (error) {
          setError(
            error instanceof Error
              ? error.message
              : t("发送失败，请重试。", "Sending failed. Try again."),
          );
        } finally {
          sending.current = false;
          setBusy(false);
          onSendingChange(false);
        }
      }}
    >
      <fieldset disabled={busy || blocked} className="form-fields">
        <label className="composer-message">
          <span className="sr-only">{t("消息", "Message")}</span>
          <textarea
            aria-label={t("消息", "Message")}
            required
            maxLength={4000}
            rows={2}
            value={message}
            placeholder={t(
              "聊聊目标、补充资料，也可以安排学习或创建简历",
              "Discuss goals, add information, plan study, or create a resume",
            )}
            aria-describedby="composer-hint"
            onChange={(event) => onMessageChange(event.target.value)}
            onKeyDown={(event) => {
              if (
                event.key === "Enter" &&
                !event.shiftKey &&
                !event.nativeEvent.isComposing
              ) {
                event.preventDefault();
                event.currentTarget.form?.requestSubmit();
              }
            }}
          />
        </label>
        <div className="composer-toolbar">
          <details>
            <summary>{t("关联岗位或计划", "Link jobs or a plan")}</summary>
            <label>
              {t("操作", "Action")}
              <select
                aria-label={t("操作", "Action")}
                value={intent}
                onChange={(event) => setIntent(event.target.value)}
              >
                {[
                  "auto",
                  "diagnose",
                  "plan",
                  "progress",
                  "resources",
                  "replan",
                ].map((value) => (
                  <option key={value} value={value}>
                    {label(value)}
                  </option>
                ))}
              </select>
            </label>
            {(options.job_ids || []).map((job) => (
              <label className="check" key={job.value}>
                <input
                  type="checkbox"
                  checked={jobs.includes(job.value)}
                  onChange={(event) =>
                    setJobs(
                      event.target.checked
                        ? [...jobs, job.value]
                        : jobs.filter((id) => id !== job.value),
                    )
                  }
                />
                {job.label}
              </label>
            ))}
            <label>
              {t("学习计划", "Study plan")}
              <select
                value={plan}
                onChange={(event) => setPlan(event.target.value)}
              >
                <option value="">
                  {t("使用当前已发布计划", "Use the current published plan")}
                </option>
                {(options.plan_id || []).map((value) => (
                  <option key={value.value} value={value.value}>
                    {value.label}
                  </option>
                ))}
              </select>
            </label>
          </details>
          <button type="submit" disabled={busy || !message.trim()}>
            {busy ? (
              t("发送中…", "Sending…")
            ) : (
              <>
                {t("发送", "Send")} <span aria-hidden="true">↑</span>
              </>
            )}
          </button>
        </div>
      </fieldset>
      <p className="composer-hint" id="composer-hint">
        {blocked
          ? t(
              "请等待回复，或先确认、拒绝或取消当前操作。",
              "Wait for the reply, or confirm, decline or cancel the current action.",
            )
          : t(
              "Enter 发送 · Shift + Enter 换行",
              "Enter to send · Shift + Enter for a new line",
            )}
      </p>
      {error && <p role="alert">{error}</p>}
    </form>
  );
}
