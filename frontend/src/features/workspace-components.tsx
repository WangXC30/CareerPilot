import { useContext, useEffect, useId, useRef, useState, type ReactNode } from "react";
import { array, object, records, string, type Data, type Json } from "../shared/api";
import { CommandForm, type Options } from "../shared/forms";
import { DataView, Loading, useLabel, useResource, useText } from "../shared/ui";
import { InformationActions } from "./information-actions";
import { InformationDetail as DetailContent, informationTitle } from "./information-detail";
import { References, Revision } from "./references";

function careerNext(application: Data, t: (zh: string, en: string) => string) {
  return (
    (
      {
        draft: t("核对申请材料", "Review materials"),
        prepared: t("准备本人投递", "Prepare to submit"),
        awaiting_user_submission: t("记录投递结果", "Record submission"),
        reported_submitted: t("跟进申请进展", "Follow up"),
        submitted: t("跟进申请进展", "Follow up"),
        assessment: t("准备考核", "Prepare for assessment"),
        interviewing: t("准备面试", "Prepare for interview"),
        offer: t("已获 Offer", "Offer received"),
        rejected: t("已结束", "Closed"),
        withdrawn: t("已结束", "Closed"),
        no_response: t("已结束", "Closed"),
      } as Record<string, string>
    )[string(application.status)] || ""
  );
}

export function InformationDetail({
  item,
  heading,
  omit,
}: {
  item: Data;
  heading?: string;
  omit?: string[];
}) {
  const options = useContext(References);
  return (
    <DetailContent
      item={item}
      heading={heading}
      omit={omit}
      options={options}
    />
  );
}

export function Form({
  schema,
  path,
  method,
  version,
  defaults,
  onDone,
  draft,
  label,
  options: extra,
}: {
  schema: string;
  path: string;
  method?: string;
  version?: number;
  defaults?: Data;
  onDone?: (value: Json) => void;
  draft?: { kind: string; key: string };
  label?: string;
  options?: Options;
}) {
  const { changed } = useContext(Revision);
  const options = useContext(References);
  return (
    <CommandForm
      key={`${path}:${version || 0}`}
      schemaName={schema}
      path={path}
      method={method}
      version={version}
      defaults={defaults}
      options={{ ...options, ...extra }}
      draft={draft}
      label={label}
      onDone={
        onDone
          ? (value) => {
              changed();
              onDone(value);
            }
          : undefined
      }
    />
  );
}

export function Disclosure({
  label,
  children,
  open,
  recordId,
}: {
  label: ReactNode;
  children: ReactNode;
  open?: boolean;
  recordId?: string;
}) {
  return (
    <details className="panel" open={open} data-record-id={recordId}>
      <summary>{label}</summary>
      <div className="panel-content">{children}</div>
    </details>
  );
}

export function Collection({
  path,
  children,
  empty,
  singleton = false,
  open = false,
  hideEmpty = false,
  firstOpen = false,
}: {
  path: string;
  children?: (item: Data, refresh: () => void) => ReactNode;
  empty?: string;
  singleton?: boolean;
  open?: boolean;
  hideEmpty?: boolean;
  firstOpen?: boolean;
}) {
  const t = useText();
  const label = useLabel();
  const { revision } = useContext(Revision);
  const { changed } = useContext(Revision);
  const options = useContext(References);
  const [query, setQuery] = useState("");
  const managedKinds: Record<string, string> = {
    "/materials": "material",
    "/experiences": "memory",
    "/memories": "memory",
    "/resume-facts": "resume_fact",
    "/source-documents": "source_document",
    "/projects": "practice_project",
    "/jobs": "job",
    "/resumes": "resume",
    "/applications": "job_application",
    "/application-drafts": "application_draft",
    "/plans": "plan",
    "/study-logs": "study_log",
    "/quarter-reviews": "quarter_review",
    "/career-goals": "career_goal",
    "/interviews": "interview_session",
    "/interview-reviews": "interview_review",
    "/resume-translations": "resume_translation",
    "/offer-comparisons": "offer_comparison",
    "/information?kind=match_snapshot": "match_snapshot",
    "/plan-previews": "plan_preview",
    "/review-pipelines": "review_pipeline",
  };
  const paginated = ["/jobs", "/career-goals", "/agent-tasks"].includes(path);
  const [cursors, setCursors] = useState([""]);
  const [includeHistory, setIncludeHistory] = useState(false);
  const [includeClosed, setIncludeClosed] = useState(false);
  const cursor = cursors[cursors.length - 1];
  const resource = useResource(
    paginated
      ? `${path}?limit=20${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ""}${path === "/jobs" ? `&q=${encodeURIComponent(query)}` : ""}`
      : path,
    revision,
  );
  const recordId = new URLSearchParams(window.location.search).get("record");
  const targetJob = useResource(
    path === "/jobs" && window.location.hash === "#jobs" && recordId
      ? `/jobs/${encodeURIComponent(recordId)}`
      : null,
    revision,
  );
  const rawItems = singleton ? [object(resource.data)] : records(resource.data);
  const target = object(targetJob.data);
  const displayItems =
    path === "/jobs" &&
    recordId &&
    target.id &&
    !rawItems.some((item) => item.id === target.id)
      ? [target, ...rawItems]
      : rawItems;
  const items = displayItems.filter(
    (item) =>
      (includeHistory ||
        (!item.user_archived &&
          !(path === "/career-goals" && item.status === "archived"))) &&
      (path !== "/applications" ||
        includeClosed ||
        !["offer", "rejected", "withdrawn", "no_response"].includes(
          string(item.status),
        ) ||
        string(item.id) === recordId) &&
      (path !== "/memories" || !item.structured_experience) &&
      (path === "/jobs" ||
        !query ||
        JSON.stringify(item)
          .toLocaleLowerCase()
          .includes(query.toLocaleLowerCase())),
  );
  const collectionRef = useRef<HTMLElement>(null);
  const located = useRef("");
  useEffect(() => {
    const hashPaths: Record<string, string | string[]> = {
      plans: "/plans",
      applications: "/applications",
      resumes: "/resumes",
      jobs: "/jobs",
      "learning-projects": "/projects",
      facts: "/resume-facts",
      materials: ["/experiences", "/materials", "/memories"],
      "interview-review": "/interview-reviews",
      "plan-previews": "/plan-previews",
    };
    function locate(force = false) {
      const key =
        window.location.pathname +
        window.location.search +
        window.location.hash;
      if (!force && located.current === key) return;
      const hash = window.location.hash.slice(1);
      const allowed = hashPaths[hash];
      if (!(Array.isArray(allowed) ? allowed.includes(path) : allowed === path))
        return;
      const params = new URLSearchParams(window.location.search);
      const record = params.get("record");
      const task = params.get("task");
      if (!record && (hash === "resumes" || hash === "applications")) return;
      const root = collectionRef.current;
      if (!root) return;
      const panel = record
        ? Array.from(
            root.querySelectorAll<HTMLElement>("[data-record-id]"),
          ).find((el) => el.dataset.recordId === record)
        : root.querySelector<HTMLElement>("[data-record-id]");
      if (!panel) return;
      const target = task
        ? Array.from(
            panel.querySelectorAll<HTMLElement>("[data-task-id]"),
          ).find((el) => el.dataset.taskId === task) || panel
        : panel;
      located.current = key;
      target.scrollIntoView({ block: "center" });
      const focus =
        target === panel ? panel.querySelector<HTMLElement>("summary") : target;
      focus?.focus({ preventScroll: true });
    }
    const timer = window.setTimeout(() => locate(), 100);
    function onHash() {
      window.setTimeout(() => locate(true), 100);
    }
    function onClick(event: MouseEvent) {
      const link = (event.target as Element).closest("a");
      if (
        link &&
        link.origin === location.origin &&
        link.pathname === location.pathname &&
        link.search === location.search &&
        link.hash === location.hash
      )
        window.setTimeout(() => locate(true), 100);
    }
    window.addEventListener("hashchange", onHash);
    document.addEventListener("click", onClick);
    return () => {
      window.clearTimeout(timer);
      window.removeEventListener("hashchange", onHash);
      document.removeEventListener("click", onClick);
    };
  }, [path, resource.data, targetJob.data]);
  const displayTitle = (item: Data) => {
    if (path !== "/plans")
      return informationTitle(item) || t("详情", "Details");
    const name = string(item.title || item.name).trim();
    if (name) return name;
    const jobs = array(item.job_ids)
      .map((id) => options.job_id?.find((job) => job.value === id)?.label)
      .filter(Boolean);
    const heading = t("学习计划", "Study plan");
    return jobs.length ? `${jobs.join(t("、", ", "))} · ${heading}` : heading;
  };
  return (
    <section className="collection" ref={collectionRef}>
      {path === "/applications" &&
        rawItems.some((item) =>
          ["offer", "rejected", "withdrawn", "no_response"].includes(
            string(item.status),
          ),
        ) && (
          <label className="check career-closed-toggle">
            <input
              type="checkbox"
              checked={includeClosed}
              onChange={(event) => setIncludeClosed(event.target.checked)}
            />
            {t("显示已结束申请", "Show closed applications")}
          </label>
        )}
      {(rawItems.length > 0 || query || includeHistory) && (
        <details className="collection-filter">
          <summary>{t("搜索与历史", "Search and history")}</summary>
          <div className="list-tools">
            {managedKinds[path] && (
              <label className="check">
                <input
                  type="checkbox"
                  checked={includeHistory}
                  onChange={(event) => setIncludeHistory(event.target.checked)}
                />
                {t("显示历史记录", "Show historical records")}
              </label>
            )}
            <label>
              {t(
                paginated && path !== "/jobs" ? "搜索当前页" : "搜索资料",
                paginated && path !== "/jobs"
                  ? "Search this page"
                  : "Search information",
              )}
              <input
                type="search"
                maxLength={200}
                value={query}
                onChange={(event) => {
                  setQuery(event.target.value);
                  setCursors([""]);
                }}
              />
            </label>
            <button className="subtle" onClick={resource.refresh}>
              {t("刷新", "Refresh")}
            </button>
          </div>
        </details>
      )}
      {(resource.loading && resource.data === null) || resource.error ? (
        <Loading error={resource.error} retry={resource.refresh} />
      ) : items.length ? (
        items.map((item, index) => (
          <Disclosure
            key={string(item.id) || index}
            label={
              path === "/applications" ? (
                <span className="application-summary">
                  <strong>
                    {[
                      string(object(item.job).company),
                      string(object(item.job).title),
                    ]
                      .filter(Boolean)
                      .join(" · ") || displayTitle(item)}
                  </strong>
                  <span className="application-status">
                    {label(string(item.status))}
                    {item.verification === "pending_verification" &&
                      ` · ${t("投递待核验", "Submission unverified")}`}
                  </span>
                  <span className="application-next">
                    {string(object(item.last_event).at).slice(0, 10) ||
                      string(item.created_at).slice(0, 10)}{" "}
                    · {careerNext(item, t)}
                  </span>
                </span>
              ) : (
                displayTitle(item)
              )
            }
            recordId={string(item.id)}
            open={
              (firstOpen &&
                index === 0 &&
                !new URLSearchParams(window.location.search).has("record")) ||
              (open &&
                (path !== "/plans" ||
                  ["active", "draft"].includes(string(item.status)))) ||
              new URLSearchParams(window.location.search).get("record") ===
                string(item.id)
            }
          >
            {children ? (
              children(item, resource.refresh)
            ) : (
              <InformationDetail item={item} heading={displayTitle(item)} />
            )}
            {managedKinds[path] && (
              <InformationActions
                kind={managedKinds[path]}
                item={item}
                options={options}
                onChanged={changed}
                edit={!["/experiences", "/memories"].includes(path)}
              />
            )}
          </Disclosure>
        ))
      ) : hideEmpty && !query && !includeHistory ? null : (
        <p className="empty">{empty || t("暂无记录。", "No records yet.")}</p>
      )}
      {paginated && (
        <div className="actions">
          <button
            className="secondary"
            disabled={cursors.length === 1 || resource.loading}
            onClick={() => setCursors(cursors.slice(0, -1))}
          >
            {t("上一页", "Previous page")}
          </button>
          <button
            className="secondary"
            disabled={rawItems.length < 20 || resource.loading}
            onClick={() =>
              setCursors([
                ...cursors,
                string(rawItems[rawItems.length - 1]?.id),
              ])
            }
          >
            {t("下一页", "Next page")}
          </button>
        </div>
      )}
    </section>
  );
}

export function Reader({ path }: { path: string }) {
  const { revision } = useContext(Revision);
  const resource = useResource(path, revision);
  return resource.loading || resource.error ? (
    <Loading error={resource.error} retry={resource.refresh} />
  ) : (
    <DataView value={resource.data} />
  );
}

export function Subtabs({
  labels,
  children,
}: {
  labels: [string, string][];
  children: (index: number) => ReactNode;
}) {
  const t = useText();
  function linkedTab() {
    const titles: Record<string, string> = {
      projects: "Project outcomes",
      plans: "Plans and tasks",
      "plan-previews": "Plans and tasks",
      assessments: "Assessment and review",
      "learning-projects": "Project practice",
      reflection: "Study and reflection",
      resumes: "Resumes and documents",
      applications: "Applications",
      "career-notices": "Applications",
      interviews: "Interviews",
      "interview-review": "Interviews",
      jobs: "Jobs",
      facts: "Career facts",
      materials: "Materials and experience",
      history: "Sources and history",
      "all-information": "All information",
    };
    const expected =
      titles[window.location.hash.slice(1)] ||
      (new URLSearchParams(window.location.search).has("code")
        ? "Sources and history"
        : undefined);
    return labels.findIndex((label) => label[1] === expected);
  }
  const [active, setActive] = useState(() => Math.max(0, linkedTab()));
  const tabId = useId();
  const tabRefs = useRef<(HTMLButtonElement | null)[]>([]);
  useEffect(() => {
    function followHash() {
      const next = linkedTab();
      if (next >= 0) setActive(next);
    }
    window.addEventListener("hashchange", followHash);
    return () => window.removeEventListener("hashchange", followHash);
  }, [labels]);
  return (
    <>
      <div className="subtabs" role="tablist">
        {labels.map((label, index) => (
          <button
            type="button"
            role="tab"
            key={index}
            id={`${tabId}-tab-${index}`}
            aria-selected={index === active}
            aria-controls={`${tabId}-panel`}
            tabIndex={index === active ? 0 : -1}
            ref={(node) => {
              tabRefs.current[index] = node;
            }}
            onKeyDown={(event) => {
              const next =
                event.key === "ArrowRight"
                  ? (index + 1) % labels.length
                  : event.key === "ArrowLeft"
                    ? (index + labels.length - 1) % labels.length
                    : event.key === "Home"
                      ? 0
                      : event.key === "End"
                        ? labels.length - 1
                        : -1;
              if (next >= 0) {
                event.preventDefault();
                setActive(next);
                tabRefs.current[next]?.focus();
              }
            }}
            onClick={() => setActive(index)}
          >
            {t(...label)}
          </button>
        ))}
      </div>
      <div
        role="tabpanel"
        id={`${tabId}-panel`}
        aria-labelledby={`${tabId}-tab-${active}`}
        tabIndex={0}
        key={active}
      >
        {children(active)}
      </div>
    </>
  );
}

