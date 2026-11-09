import { useContext, useEffect, useRef, useState } from "react";
import { array, command, headers, number, object, request, string, type Data } from "../shared/api";
import { Action, DataView, Locale, useLabel, useText } from "../shared/ui";
import { AssistantChat } from "./assistant-chat";
import { AssistantRender } from "./assistant-render";
import { AssistantResult, hasAssistantResult } from "./assistant-result";
import { References, Revision } from "./references";
export function Today() {
  const t = useText();
  const options = useContext(References);
  return (
    <>
      <section
        className="assistant"
        aria-label={t("成长助手", "Growth assistant")}
      >
        <AssistantChat
          options={options}
          renderRun={(run) => (
            <Run
              key={string(run.run_id)}
              id={string(run.run_id)}
              initial={run}
            />
          )}
        />
      </section>
    </>
  );
}

function Run({ id, initial }: { id: string; initial?: Data }) {
  const t = useText();
  const label = useLabel();
  const [run, setRun] = useState<Data>(initial || {});
  const [connection, setConnection] = useState("");
  const [subscription, setSubscription] = useState(0);
  const [confirmationPending, setConfirmationPending] = useState(false);
  const [confirmationError, setConfirmationError] = useState("");
  const confirmationInFlight = useRef(false);
  const cursor = useRef(0);
  const locale = useContext(Locale);
  const references = useContext(References);
  const { changed } = useContext(Revision);
  async function decide(approved: boolean) {
    if (confirmationInFlight.current) return;
    confirmationInFlight.current = true;
    setConfirmationPending(true);
    setConfirmationError("");
    try {
      const next = object(
        await command(
          `/workflows/runs/${id}/resume`,
          { approved },
          "POST",
          number(run.task_version),
        ),
      );
      cursor.current = Math.max(cursor.current, number(next.sequence));
      setRun(next);
      changed();
    } catch (error) {
      setConfirmationError(
        error instanceof Error
          ? error.message
          : t(
              "确认提交失败，请重试。",
              "Confirmation could not be submitted. Try again.",
            ),
      );
      throw error;
    } finally {
      confirmationInFlight.current = false;
      setConfirmationPending(false);
    }
  }
  useEffect(() => {
    const controller = new AbortController();
    let retry: ReturnType<typeof setTimeout>;
    let poll: ReturnType<typeof setTimeout>;
    let terminal = false;
    let notified = false;
    function applySnapshot(data: Data) {
      if (
        controller.signal.aborted ||
        data.run_id !== id ||
        number(data.sequence) < cursor.current
      )
        return;
      const finished = ["completed", "failed", "cancelled"].includes(
        string(data.status),
      );
      if (terminal && !finished) return;
      setRun(data);
      cursor.current = number(data.sequence);
      terminal = finished;
      if (terminal) {
        clearTimeout(poll);
        clearTimeout(retry);
        if (!notified) {
          notified = true;
          changed();
        }
      }
    }
    async function syncSnapshot() {
      try {
        applySnapshot(
          object(
            await request(`/workflows/runs/${id}`, {
              signal: controller.signal,
            }),
          ),
        );
        if (!controller.signal.aborted) setConnection("");
      } catch (error) {
        if (!controller.signal.aborted && !terminal)
          setConnection(
            error instanceof Error
              ? error.message
              : t("正在重新读取回复…", "Retrying the reply…"),
          );
      }
      if (!controller.signal.aborted && !terminal)
        poll = setTimeout(syncSnapshot, 1500);
    }
    async function connect() {
      try {
        const response = await fetch(
          `/api/v1/workflows/runs/${id}/events?after=${cursor.current}`,
          { headers: headers(), signal: controller.signal },
        );
        if (!response.ok || !response.body)
          throw new Error(t("无法读取运行进度", "Could not read run progress"));
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let pending = "";
        while (true) {
          const { value, done } = await reader.read();
          if (done) break;
          pending += decoder.decode(value, { stream: true });
          const parts = pending.replaceAll("\r\n", "\n").split("\n\n");
          pending = parts.pop() || "";
          for (const part of parts) {
            const event = part
              .split("\n")
              .find((line) => line.startsWith("event:"))
              ?.slice(6)
              .trim();
            const raw = part
              .split("\n")
              .filter((line) => line.startsWith("data:"))
              .map((line) => line.slice(5).trim())
              .join("\n");
            if (!raw) continue;
            const data = object(JSON.parse(raw));
            if (event === "workspace_changed") {
              controller.abort();
              setConnection(
                t(
                  "工作区已变化，请刷新。",
                  "Workspace changed. Refresh this page.",
                ),
              );
              return;
            }
            if (event === "snapshot" && data.run_id === id) {
              applySnapshot(data);
              setConnection("");
              if (
                ["completed", "failed", "cancelled"].includes(
                  string(data.status),
                )
              )
                return;
            } else if (
              event === "update" &&
              data.run_id === id &&
              number(data.sequence) > cursor.current
            )
              cursor.current = number(data.sequence);
          }
        }
      } catch (error) {
        if (!controller.signal.aborted && !terminal)
          setConnection(
            error instanceof Error
              ? error.message
              : t("连接中断，正在重连…", "Connection lost. Reconnecting…"),
          );
      }
      if (!controller.signal.aborted && !terminal)
        retry = setTimeout(connect, 1200);
    }
    syncSnapshot();
    if (!["completed", "failed", "cancelled"].includes(string(initial?.status)))
      connect();
    return () => {
      controller.abort();
      clearTimeout(retry);
      clearTimeout(poll);
    };
  }, [id, locale, subscription]);
  const status = string(run.status);
  const confirmationState = object(run.confirmation_response);
  const confirmedPlan = object(
    array(object(run.result).tools)
      .map(object)
      .find((tool) => object(tool.result).id === confirmationState.result_id)
      ?.result,
  );
  const confirmationMessage = confirmationPending
    ? t("正在提交你的决定…", "Submitting your decision…")
    : confirmationError ||
      {
        accepted: t(
          "已收到确认，正在保存，请稍候…",
          "Confirmation received. Saving…",
        ),
        saved:
          confirmationState.action === "plan" &&
          (confirmedPlan.current_status || confirmedPlan.status) === "draft"
            ? t(
                "已确认并保存为草稿。发布后才开始执行，发布需要单独确认。",
                "Confirmed and saved as a draft. Publishing requires a separate confirmation before execution.",
              )
            : t("已确认并保存。", "Confirmed and saved."),
        declined: t(
          "已拒绝本次操作，未执行。",
          "This action was declined and was not executed.",
        ),
        failed: t(
          "确认已收到，但保存未成功，请查看错误。",
          "Confirmation was received, but saving failed. Review the error.",
        ),
      }[string(confirmationState.status)] ||
      "";
  const replyText =
    string(run.summary) ||
    (status === "failed"
      ? t("执行未完成，请查看错误并重试。", "The run did not finish. Review the error and retry.")
      : status === "waiting_confirmation"
        ? t(
            "这项操作需要你的确认，请核对下方内容后继续。",
            "Review the details below and confirm before continuing.",
          )
        : status === "cancelled"
          ? t("已取消此次任务。", "This task was cancelled.")
          : "");
  return (
    <article className="run" data-status={status}>
      <p className="user-message" dir="auto">
        {string(object(run.input).message)}
      </p>
      <div
        className="assistant-reply"
        aria-label={t("助手回复", "Assistant reply")}
        dir="auto"
      >
        {["queued", "running"].includes(status) ? (
          <div className="reply-thinking" role="status">
            <span className="reply-thinking-spark" aria-hidden="true">
              ✦
            </span>
            <span className="reply-thinking-label">
              {status === "queued"
                ? t("正在准备", "Getting ready")
                : t("正在思考", "Thinking")}
            </span>
            <span className="reply-thinking-dots" aria-hidden="true">
              <i />
              <i />
              <i />
            </span>
          </div>
        ) : (
          <p
            className={status === "failed" ? "reply-failure-label" : undefined}
            role="status"
            hidden={status !== "failed"}
          >
            {label(status)}
          </p>
        )}
        {connection && (
          <p className="reply-connection" role="status">
            {connection}
          </p>
        )}
        <AssistantRender text={replyText} />
        {confirmationMessage && (
          <p
            className="confirmation-feedback"
            role={
              confirmationError || confirmationState.status === "failed"
                ? "alert"
                : "status"
            }
          >
            {confirmationMessage}
            {status === "waiting_confirmation" &&
              confirmationState.status === "saved" &&
              t(
                "下方是另一项待确认操作。",
                "The next action below requires a separate confirmation.",
              )}
          </p>
        )}
        {run.result && hasAssistantResult(run.result) && (
          <AssistantResult value={run.result} references={references} />
        )}
      </div>
      {status === "waiting_confirmation" && (
        <div className="notice chat-confirmation">
          {object(run.confirmation).preview ? (
            <>
              <p>{string(object(run.confirmation).summary)}</p>
              {hasAssistantResult(object(run.confirmation).preview) ? (
                <AssistantResult
                  value={object(run.confirmation).preview!}
                  references={references}
                />
              ) : (
                <p className="preview-text">
                  {string(object(object(run.confirmation).preview).text)}
                </p>
              )}
            </>
          ) : (
            <DataView value={run.confirmation} />
          )}
          <Action
            disabled={confirmationPending}
            successMessage={t("确认已提交", "Confirmation submitted")}
            run={() => decide(true)}
          >
            {t("确认继续", "Confirm and continue")}
          </Action>
          <Action
            className="secondary"
            disabled={confirmationPending}
            successMessage={t("已拒绝本次操作", "Action declined")}
            run={() => decide(false)}
          >
            {t("拒绝", "Decline")}
          </Action>
        </div>
      )}
      {["queued", "running", "waiting_confirmation"].includes(status) && (
        <Action
          className="secondary"
          run={async () => {
            setRun(
              object(
                await command(
                  `/workflows/runs/${id}/cancel`,
                  {},
                  "POST",
                  number(run.task_version),
                ),
              ),
            );
            changed();
          }}
        >
          {t("取消此运行", "Cancel run")}
        </Action>
      )}
      {status === "failed" && run.graph_version === "growth.v1" && (
        <Action
          className="reply-retry"
          run={async () => {
            setRun(
              object(
                await command(
                  `/workflows/runs/${id}/retry`,
                  {},
                  "POST",
                  number(run.task_version),
                ),
              ),
            );
            setSubscription((x) => x + 1);
            changed();
          }}
        >
          <span aria-hidden="true">↻</span> {t("重新尝试", "Try again")}
        </Action>
      )}
    </article>
  );
}

