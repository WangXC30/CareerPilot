import { createPortal } from "react-dom";
import {
  createContext,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import {
  command,
  ApiError,
  currentWorkspace,
  object,
  number,
  request,
  records,
  string,
  type Data,
} from "../shared/api";
import type { Options } from "../shared/forms";
import { Action, useResource, useText } from "../shared/ui";
import { AssistantComposer } from "./assistant-composer";
import { Revision } from "./references";

export const SidebarVisibility = createContext({
  open: true,
  setOpen: (_open: boolean) => {},
  showConversation: () => {},
});

export function AssistantChat({
  options,
  renderRun,
}: {
  options: Options;
  renderRun: (run: Data) => ReactNode;
}) {
  const t = useText();
  const { revision, changed } = useContext(Revision);
  const storage = `careerpilot.conversation:${currentWorkspace()}`;
  const [selected, setSelected] = useState(
    () => localStorage.getItem(storage) || "",
  );
  const { open: historyOpen, setOpen: setHistoryOpen, showConversation } =
    useContext(SidebarVisibility);
  const [search, setSearch] = useState("");
  const draftKey = (id: string) =>
    `careerpilot.chat-draft:${currentWorkspace()}:${id || "new"}`;
  const [draft, setDraft] = useState(
    () =>
      sessionStorage.getItem(draftKey(localStorage.getItem(storage) || "")) ||
      "",
  );
  function updateDraft(value: string) {
    setDraft(value);
    if (value) sessionStorage.setItem(draftKey(selected), value);
    else sessionStorage.removeItem(draftKey(selected));
  }
  const [sending, setSending] = useState(false);
  const [deleting, setDeleting] = useState("");
  const [deleteFeedback, setDeleteFeedback] = useState("");
  const [deleted, setDeleted] = useState<string[]>([]);
  const deleteInFlight = useRef(false);
  const toggle = useRef<HTMLButtonElement>(null);
  const stage = useRef<HTMLDivElement>(null);
  const [accepted, setAccepted] = useState<Data[]>([]);
  const [earlier, setEarlier] = useState<Data[]>([]);
  const [before, setBefore] = useState<number | null>(null);
  const [more, setMore] = useState<boolean | null>(null);
  const transcript = useRef<HTMLDivElement>(null);
  const creation = useRef({ key: crypto.randomUUID(), id: "" });
  const conversations = useResource("/workflows/conversations", revision);
  const guidance = useResource("/workflows/guidance", revision);
  const [guidanceError, setGuidanceError] = useState("");
  const [guidanceBusy, setGuidanceBusy] = useState(false);
  const guidanceFlight = useRef(false);
  const latest = useResource(
    selected ? `/workflows/conversations/${selected}/runs?limit=30` : null,
    revision,
  );
  const messages = [
    ...new Map(
      [
        ...earlier,
        ...accepted,
        ...records(object(latest.data || {}).items || []).reverse(),
      ]
        .filter((run) => object(run.input).conversation_id === selected)
        .map((run) => [string(run.run_id), run]),
    ).values(),
  ].sort((a, b) => string(a.created_at).localeCompare(string(b.created_at)));
  const busy = messages.some((run) =>
    ["queued", "running", "waiting_confirmation"].includes(string(run.status)),
  );
  const chats = records(conversations.data || []).filter(
    (chat) => !deleted.includes(string(chat.id)),
  );
  const activeTitle =
    string(chats.find((chat) => chat.id === selected)?.title) ||
    t("新对话", "New conversation");
  const filtered = chats.filter((chat) =>
    string(chat.title)
      .toLocaleLowerCase()
      .includes(search.trim().toLocaleLowerCase()),
  );
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const week = new Date(today);
  week.setDate(week.getDate() - 7);
  function group(chat: Data) {
    const created = new Date(string(chat.created_at)).getTime();
    return created >= today.getTime() ? 0 : created >= week.getTime() ? 1 : 2;
  }
  function closeHistory() {
    setHistoryOpen(false);
    toggle.current?.focus();
  }
  async function updateGuidance(
    action:
      | "explore"
      | "learn"
      | "career"
      | "defer"
      | "resume"
      | "answered"
      | "reset",
    runId?: string,
  ) {
    if (guidanceFlight.current) return;
    guidanceFlight.current = true;
    const workspace = currentWorkspace();
    setGuidanceBusy(true);
    setGuidanceError("");
    try {
      await command(
        "/workflows/guidance",
        { action, ...(runId ? { run_id: runId } : {}) },
        "PUT",
        number(object(guidance.data).version),
      );
      guidance.refresh();
    } catch (error) {
      if (
        action === "answered" &&
        error instanceof ApiError &&
        error.status === 412 &&
        runId
      ) {
        try {
          const fresh = object(await request("/workflows/guidance"));
          if (currentWorkspace() !== workspace) return;
          if (
            fresh.status === "in_progress" &&
            fresh.purpose === object(guidance.data).purpose &&
            fresh.response_received !== true
          ) {
            await command(
              "/workflows/guidance",
              { action, run_id: runId },
              "PUT",
              number(fresh.version),
            );
          }
          guidance.refresh();
          return;
        } catch (retryError) {
          error = retryError;
        }
      }
      setGuidanceError(
        error instanceof Error
          ? error.message
          : t("保存失败，请重试。", "Could not save. Try again."),
      );
      guidance.refresh();
    } finally {
      guidanceFlight.current = false;
      setGuidanceBusy(false);
    }
  }
  function select(id: string) {
    if (sending) return;
    setDraft(sessionStorage.getItem(draftKey(id)) || "");
    setSearch("");
    setSelected(id);
    setAccepted([]);
    setEarlier([]);
    setBefore(null);
    setMore(null);
    localStorage.setItem(storage, id);
    creation.current = { key: crypto.randomUUID(), id: "" };
  }
  async function remove(chat: Data) {
    if (sending || deleteInFlight.current) return;
    const id = string(chat.id);
    setDeleteFeedback("");
    if (
      !window.confirm(
        t(
          `删除对话“${string(chat.title)}”？\n对话及其历史将永久删除，正在处理的回复会停止。已保存的档案、计划和偏好保留。`,
          `Delete “${string(chat.title)}”?\nThis permanently removes its chat history and stops pending replies. Saved profiles, plans and preferences remain.`,
        ),
      )
    )
      return;
    const workspace = currentWorkspace();
    deleteInFlight.current = true;
    setDeleting(id);
    setDeleteFeedback("");
    try {
      try {
        await command(
          `/workflows/conversations/${id}`,
          { confirmed: true },
          "DELETE",
          number(chat.version),
        );
      } catch (error) {
        if (!(error instanceof ApiError && error.status === 404)) throw error;
      }
      if (currentWorkspace() !== workspace) return;
      sessionStorage.removeItem(draftKey(id));
      setDeleted((previous) => [...previous, id]);
      if (selected === id) select("");
      setDeleteFeedback(t("对话已删除。", "Conversation deleted."));
      changed();
      window.requestAnimationFrame(() => {
        if (selected === id) stage.current?.querySelector("textarea")?.focus();
        else
          document
            .getElementById("chat-history")
            ?.querySelector("input")
            ?.focus();
      });
    } catch (error) {
      setDeleteFeedback(
        error instanceof Error
          ? error.message
          : t("删除失败，请重试。", "Could not delete. Try again."),
      );
      conversations.refresh();
    } finally {
      deleteInFlight.current = false;
      setDeleting("");
    }
  }
  useEffect(() => {
    const log = transcript.current;
    if (log) log.scrollTop = log.scrollHeight;
  }, [selected, messages.length]);
  return (
    <div
      className={`chat-workspace${historyOpen ? " history-open" : ""}`}
      onKeyDown={(event) => {
        if (event.key === "Escape" && historyOpen) {
          event.preventDefault();
          closeHistory();
        }
      }}
    >
      {createPortal(
        <aside
          className="chat-history"
          id="chat-history"
          aria-label={t("历史对话", "Chat history")}
          hidden={!historyOpen}
        >
          <div className="history-heading">
            <span>{t("你的对话", "Your conversations")}</span>
          </div>
          <label className="history-search">
            <span className="sr-only">
              {t("搜索对话", "Search conversations")}
            </span>
            <svg
              viewBox="0 0 24 24"
              width="16"
              height="16"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.7"
              aria-hidden="true"
            >
              <circle cx="10.5" cy="10.5" r="6.5" />
              <path d="m16 16 4 4" />
            </svg>
            <input
              type="search"
              value={search}
              placeholder={t("搜索对话", "Search conversations")}
              onChange={(event) => setSearch(event.target.value)}
            />
          </label>
          <div className="history-list">
            {conversations.loading && !chats.length ? (
              <p className="history-empty" role="status">
                {t("正在读取对话…", "Loading conversations…")}
              </p>
            ) : (
              !filtered.length && (
                <p className="history-empty">
                  {search
                    ? t("未找到匹配的对话。", "No matching conversations.")
                    : t(
                        "开始聊天后，对话会保存在这里。",
                        "Your conversations will appear here after you start chatting.",
                      )}
                </p>
              )
            )}
            {[
              t("今天", "Today"),
              t("过去 7 天", "Previous 7 days"),
              t("更早", "Earlier"),
            ].map((label, index) => {
              const items = filtered.filter((chat) => group(chat) === index);
              return (
                items.length > 0 && (
                  <section className="history-group" key={index}>
                    <h3>{label}</h3>
                    {items.map((chat) => (
                      <div className="conversation-row" key={string(chat.id)}>
                        <button
                          type="button"
                          className="conversation-item"
                          data-conversation-id={string(chat.id)}
                          aria-current={
                            chat.id === selected ? "true" : undefined
                          }
                          disabled={sending || !!deleting}
                          title={string(chat.title)}
                          onClick={() => {
                            select(string(chat.id));
                            showConversation();
                          }}
                        >
                          <svg
                            viewBox="0 0 24 24"
                            width="16"
                            height="16"
                            fill="none"
                            stroke="currentColor"
                            strokeWidth="1.6"
                            aria-hidden="true"
                          >
                            <path d="M20 11a8 8 0 0 1-8 8H5l-3 3V11a9 9 0 0 1 18 0Z" />
                          </svg>
                          <span>
                            {string(chat.title) ||
                              t("新对话", "New conversation")}
                          </span>
                        </button>
                        <button
                          type="button"
                          className="conversation-delete"
                          disabled={sending || !!deleting}
                          aria-label={t(
                            `删除对话：${string(chat.title)}`,
                            `Delete conversation: ${string(chat.title)}`,
                          )}
                          title={t("删除对话", "Delete conversation")}
                          onClick={() => void remove(chat)}
                        >
                          {deleting === chat.id ? (
                            <span aria-hidden="true">…</span>
                          ) : (
                            <svg
                              viewBox="0 0 24 24"
                              width="16"
                              height="16"
                              fill="none"
                              stroke="currentColor"
                              strokeWidth="1.7"
                              aria-hidden="true"
                            >
                              <path d="M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7M14 10v7" />
                            </svg>
                          )}
                        </button>
                      </div>
                    ))}
                  </section>
                )
              );
            })}
          </div>
          {deleteFeedback && (
            <p className="history-empty" role="status">
              {deleteFeedback}
            </p>
          )}
          <p className="history-footnote">
            {t(
              "每次对话，离目标更近一步。",
              "One conversation closer to your goals.",
            )}
          </p>
        </aside>,
        document.getElementById("sidebar-history")!,
      )}
      <div className="chat-stage" ref={stage}>
        <header className="chat-header">
          <button
            type="button"
            className="subtle history-toggle"
            ref={toggle}
            aria-controls="workspace-sidebar"
            aria-expanded={historyOpen}
            aria-label={
              historyOpen
                ? t("收起侧边栏", "Collapse sidebar")
                : t("展开侧边栏", "Expand sidebar")
            }
            onClick={() => setHistoryOpen(!historyOpen)}
          >
            <svg
              viewBox="0 0 24 24"
              width="20"
              height="20"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.7"
              aria-hidden="true"
            >
              <rect x="3" y="4" width="18" height="16" rx="3" />
              <path d="M9 4v16M5.5 8h1M5.5 12h1" />
            </svg>
          </button>
          <div className="chat-heading">
            <h2>{t("成长助手", "Growth assistant")}</h2>
            <p title={activeTitle}>{activeTitle}</p>
          </div>
          <button
            type="button"
            className="subtle new-conversation"
            disabled={sending || !!deleting}
            onClick={() => {
              sessionStorage.removeItem(draftKey(""));
              select("");
              window.requestAnimationFrame(() =>
                stage.current?.querySelector("textarea")?.focus(),
              );
            }}
          >
            <svg
              viewBox="0 0 24 24"
              width="17"
              height="17"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.7"
              aria-hidden="true"
            >
              <path d="M12 5H5a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-7M15 3l6 6M10 14l1-5 8-8 4 4-8 8-5 1Z" />
            </svg>
            <span>{t("新建对话", "New conversation")}</span>
          </button>
        </header>
        <div
          className="assistant-transcript"
          ref={transcript}
          role="region"
          aria-label={t("对话记录", "Conversation history")}
          tabIndex={0}
        >
          {guidance.error && (
            <div role="alert">
              <p>
                {t(
                  "无法读取资料引导，请重试。普通对话仍可使用。",
                  "Could not load guidance. You can still chat; retry when ready.",
                )}
              </p>
              <button type="button" onClick={guidance.refresh}>
                {t("重试", "Retry")}
              </button>
            </div>
          )}
          {!guidance.loading &&
            !guidance.error &&
            [
              "not_started",
              "in_progress",
              "deferred",
              "draft_pending",
            ].includes(string(object(guidance.data).status)) && (
              <section
                className="guidance-card"
                aria-label={t("资料引导", "Getting started")}
              >
                {object(guidance.data).status === "draft_pending" ? (
                  <>
                    <p>
                      {t(
                        "发现未完成的资料草稿，可以继续填写，也可以暂时跳过。",
                        "An unfinished information draft is available. Continue editing or skip for now.",
                      )}
                    </p>
                    <a href="/app/profile">
                      {t(
                        "继续未完成的资料草稿",
                        "Continue your information draft",
                      )}
                    </a>
                  </>
                ) : object(guidance.data).status === "not_started" ? (
                  <>
                    <p>
                      {t(
                        "你现在最想做什么？这会帮助我只询问当前需要的信息。",
                        "What would you like to do? This helps me ask only what is needed now.",
                      )}
                    </p>
                    {object(guidance.data).profile_draft === true && (
                      <a href="/app/profile">
                        {t(
                          "继续未完成的资料草稿",
                          "Continue your information draft",
                        )}
                      </a>
                    )}
                    {(
                      [
                        ["explore", "确定方向", "Explore directions"],
                        ["learn", "学习提升", "Improve skills"],
                        ["career", "准备求职", "Prepare for a job search"],
                      ] as const
                    ).map(([action, zh, en]) => (
                      <button
                        type="button"
                        key={action}
                        disabled={guidanceBusy}
                        onClick={() => void updateGuidance(action)}
                      >
                        {t(zh, en)}
                      </button>
                    ))}
                  </>
                ) : object(guidance.data).status === "in_progress" ? (
                  <>
                    <p>
                      {object(guidance.data).response_received === true
                        ? t(
                            "已收到你的对话，资料仍需预览和确认。可以继续补充或更换目的。",
                            "Your message was received. Information still needs review and confirmation; you can continue or change your purpose.",
                          )
                        : string(object(guidance.data).purpose) === "explore"
                          ? t(
                              "你目前处于什么阶段，有哪些兴趣或经历？不确定也可以一起探索。",
                              "What stage are you at, and what interests or experience do you have? It is fine to be unsure.",
                            )
                          : string(object(guidance.data).purpose) === "learn"
                            ? t(
                                "你想达到什么目标，每周能投入多少时间？",
                                "What goal are you working towards, and how much time can you spend each week?",
                              )
                            : t(
                                "你想准备什么岗位？已有岗位描述可以直接粘贴。",
                                "What role are you preparing for? You can paste a job description if you have one.",
                              )}
                    </p>
                    {object(guidance.data).response_received !== true && (
                      <p>
                        {t(
                          "可在下方对话回答；资料写入仍需预览和确认。",
                          "Answer below; saving information still requires review and confirmation.",
                        )}
                      </p>
                    )}
                    <button
                      type="button"
                      disabled={guidanceBusy}
                      onClick={() => void updateGuidance("reset")}
                    >
                      {t("更换目的", "Change purpose")}
                    </button>
                    {object(guidance.data).profile_draft === true && (
                      <a href="/app/profile">
                        {t(
                          "继续未完成的资料草稿",
                          "Continue your information draft",
                        )}
                      </a>
                    )}
                  </>
                ) : (
                  <p>
                    {t(
                      "引导已暂缓，你可以随时继续对话。",
                      "Getting started is paused; you can continue chatting.",
                    )}
                  </p>
                )}
                {object(guidance.data).status !== "deferred" && (
                  <button
                    type="button"
                    disabled={guidanceBusy}
                    onClick={() => void updateGuidance("defer")}
                  >
                    {t("暂时跳过", "Skip for now")}
                  </button>
                )}
                {object(guidance.data).status === "deferred" && (
                  <button
                    type="button"
                    disabled={guidanceBusy}
                    onClick={() => void updateGuidance("resume")}
                  >
                    {t("继续引导", "Resume guidance")}
                  </button>
                )}
                {guidanceError && <p role="alert">{guidanceError}</p>}
              </section>
            )}
          {(more ?? object(latest.data || {}).has_more) === true && (
            <Action
              className="subtle"
              run={async () => {
                const page = object(
                  await request(
                    `/workflows/conversations/${selected}/runs?limit=30&before=${before ?? number(object(latest.data || {}).before)}`,
                  ),
                );
                setEarlier((previous) => [
                  ...records(page.items || []).reverse(),
                  ...previous,
                ]);
                setBefore(number(page.before));
                setMore(page.has_more === true);
              }}
            >
              {t("查看更早消息", "Load earlier messages")}
            </Action>
          )}
          {!messages.length &&
            (latest.loading ? (
              <p className="chat-loading" role="status">
                {t("正在读取对话…", "Loading messages…")}
              </p>
            ) : (
              !latest.error && (
                <div className="chat-welcome">
                  <div className="assistant-emblem" aria-hidden="true">
                    <svg
                      viewBox="0 0 32 32"
                      width="32"
                      height="32"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="1.5"
                    >
                      <path d="M16 3v6m0 14v6M3 16h6m14 0h6M7 7l4 4m10 10 4 4M7 25l4-4M21 11l4-4" />
                      <path d="m16 10 6 6-6 6-6-6Z" />
                    </svg>
                  </div>
                  <h3>
                    {t(
                      "今天，想向哪个目标迈进一步？",
                      "What would you like to move forward today?",
                    )}
                  </h3>
                  <p>
                    {t(
                      "聊聊你的想法，一起把目标变成可以开始的行动。",
                      "Share what is on your mind. Let’s turn your goals into a next step.",
                    )}
                  </p>
                  <div className="chat-suggestions">
                    {[
                      [
                        t("找到今天的重点", "Find today’s focus"),
                        t(
                          "查看当前进展，帮我找到今天可以完成的一步。",
                          "Review my progress and help me find one step I can take today.",
                        ),
                      ],
                      [
                        t("梳理学习方向", "Plan my learning"),
                        t(
                          "根据我的目标，帮我拆解本周学习计划。",
                          "Help me break down this week’s study plan based on my goals.",
                        ),
                      ],
                      [
                        t("聊聊求职准备", "Prepare for my career"),
                        t(
                          "我想梳理求职准备，应该从哪里开始？",
                          "I would like to prepare for my job search. Where should I start?",
                        ),
                      ],
                    ].map(([title, prompt]) => (
                      <button
                        type="button"
                        className="chat-suggestion"
                        key={title}
                        disabled={sending}
                        onClick={() => {
                          updateDraft(prompt);
                          stage.current?.querySelector("textarea")?.focus();
                        }}
                      >
                        <span>{title}</span>
                        <span aria-hidden="true">↗</span>
                      </button>
                    ))}
                  </div>
                </div>
              )
            ))}
          {messages.map(renderRun)}
        </div>
        {(latest.error || conversations.error) && (
          <div role="alert">
            <p>{latest.error || conversations.error}</p>
            <button
              type="button"
              className="secondary"
              onClick={() => {
                latest.refresh();
                conversations.refresh();
              }}
            >
              {t("重新读取对话", "Reload messages")}
            </button>
          </div>
        )}
        <AssistantComposer
          key={selected}
          options={options}
          message={draft}
          onMessageChange={updateDraft}
          onSendingChange={setSending}
          blocked={busy || !!deleting || latest.loading || !!latest.error}

          ensureConversation={async () => {
            if (selected) return selected;
            if (creation.current.id) return creation.current.id;
            const result = object(
              await command(
                "/workflows/conversations",
                {},
                "POST",
                undefined,
                creation.current.key,
              ),
            );
            const id = string(result.id);
            creation.current.id = id;
            return id;
          }}
          onAccepted={(run) => {
            const id = string(object(run.input).conversation_id);
            const guidanceState = object(guidance.data);
            if (
              guidanceState.status === "in_progress" &&
              guidanceState.response_received !== true
            )
              void updateGuidance("answered", string(run.id || run.run_id));
            setSelected(id);
            localStorage.setItem(storage, id);
            setAccepted((previous) => [...previous, run]);
            changed();
          }}
        />
      </div>
    </div>
  );
}
