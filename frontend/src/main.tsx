import React, { useEffect, useLayoutEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { configure, object, request, string } from "./shared/api";
import { Feedback, Locale, Loading } from "./shared/ui";
import { Today } from "./features/assistant-workspace";
import { SidebarVisibility } from "./features/assistant-chat";
import { ReferenceProvider, Revision } from "./features/references";
import "./style.css";
import { clearCachedDrafts } from "./shared/drafts";

const Goals = React.lazy(() => import("./features/workspace").then((m) => ({ default: m.Goals })));
const Learn = React.lazy(() => import("./features/learning").then((m) => ({ default: m.Learn })));
const Career = React.lazy(() => import("./features/career").then((m) => ({ default: m.Career })));
const Profile = React.lazy(() => import("./features/workspace").then((m) => ({ default: m.Profile })));
const Settings = React.lazy(() => import("./features/workspace").then((m) => ({ default: m.Settings })));

const routes = [
  "today",
  "goals",
  "learn",
  "career",
  "profile",
  "settings",
] as const;
type Route = (typeof routes)[number];
const legacy: Record<string, Route> = {
  dashboard: "today",
  explore: "goals",
  market: "goals",
  directions: "goals",
  skills: "goals",
  learning: "learn",
  sources: "learn",
  progress: "learn",
  portfolio: "profile",
  recruiting: "career",
  settings: "profile",
  "local-tools": "settings",
  collaboration: "settings",
  operations: "settings",
};
function locationRoute(): Route {
  if (window.location.pathname.endsWith("/github-callback.html"))
    return "profile";
  const path = window.location.pathname.replace(/^\/app\/?/, "").split("/")[0];
  return routes.includes(path as Route) ? (path as Route) : "today";
}

class Boundary extends React.Component<
  { children: React.ReactNode },
  { failed: boolean }
> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  render() {
    return this.state.failed ? (
      <main>
        <p role="alert">页面无法显示 / Unable to display this page</p>
        <button onClick={() => window.location.reload()}>
          重新读取 / Reload
        </button>
      </main>
    ) : (
      this.props.children
    );
  }
}

function Workspace() {
  const [route, setRoute] = useState<Route>(locationRoute);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [revision, setRevision] = useState(0);
  const [owner, setOwner] = useState("");
  const [error, setError] = useState("");
  const [language, setLanguage] = useState(
    localStorage.getItem("careerpilot-language") === "en-US"
      ? "en-US"
      : "zh-CN",
  );
  const [navigationNotice, setNavigationNotice] = useState("");
  const [feedback, setFeedback] = useState("");
  useEffect(() => {
    let current = true;
    request("/local-workspace")
      .then(async (value) => {
        const id = string(object(value).user_id);
        const previous = localStorage.getItem("careerpilot-workspace");
        if (previous && previous !== id) await clearCachedDrafts();
        if (current) {
          localStorage.setItem("careerpilot-workspace", id);
          configure(id, language);
          setOwner(id);
        }
      })
      .catch((e: Error) => {
        if (current) setError(e.message);
      });
    return () => {
      current = false;
    };
  }, []);
  useLayoutEffect(() => {
    configure(owner, language);
    document.documentElement.lang = language;
    localStorage.setItem("careerpilot-language", language);
  }, [owner, language]);
  useEffect(() => {
    function changed(event: StorageEvent) {
      if (
        event.key === "careerpilot-workspace" &&
        owner &&
        event.newValue !== owner
      ) {
        setOwner("");
        setError(
          "本地工作区已重置，请刷新。 / Workspace reset. Reload this page.",
        );
      }
    }
    window.addEventListener("storage", changed);
    return () => window.removeEventListener("storage", changed);
  }, [owner]);
  useEffect(() => {
    function navigate() {
      const hash = window.location.hash.slice(1).split(/[/?]/)[0];
      if (legacy[hash]) {
        setRoute(legacy[hash]);
        setNavigationNotice(
          hash === "collaboration" ||
            hash === "local-tools" ||
            hash === "operations"
            ? "historical"
            : "moved",
        );
        history.replaceState(null, "", `/app/${legacy[hash]}`);
      } else setRoute(locationRoute());
    }
    navigate();
    window.addEventListener("popstate", navigate);
    window.addEventListener("hashchange", navigate);
    return () => {
      window.removeEventListener("popstate", navigate);
      window.removeEventListener("hashchange", navigate);
    };
  }, []);
  const labels: Record<Route, [string, string]> = {
    today: ["AI 助手", "AI assistant"],
    goals: ["目标与岗位", "Goals and jobs"],
    learn: ["学习", "Learning"],
    career: ["求职", "Career"],
    profile: ["我的资料", "My information"],
    settings: ["设置", "Settings"],
  };
  const Page = {
    today: Today,
    goals: Goals,
    learn: Learn,
    career: Career,
    profile: Profile,
    settings: Settings,
  }[route];
  return (
    <Locale.Provider value={language}>
      <Feedback.Provider value={setFeedback}>
        <Revision.Provider
          value={{ revision, changed: () => setRevision((x) => x + 1) }}
        >
          <SidebarVisibility.Provider
            value={{
              open: route !== "today" || sidebarOpen,
              setOpen: setSidebarOpen,
              showConversation: () => {
                if (route !== "today") {
                  history.pushState(null, "", "/app/today");
                  setRoute("today");
                  setNavigationNotice("");
                  setSidebarOpen(true);
                }
              },
            }}
          >
            <div
              className={`workspace${route === "today" ? " workspace-today" : ""}${!sidebarOpen && route === "today" ? " sidebar-collapsed" : ""}`}
            >
              <a className="skip" href="#main">
                {language === "en-US" ? "Skip to content" : "跳转至内容"}
              </a>
              <aside
                className="sidebar"
                id="workspace-sidebar"
                hidden={!sidebarOpen && route === "today"}
              >
                <a className="brand" href="/app/">
                  CareerPilot
                  <span className="brand-dot" />
                </a>
                <nav
                  className="sidebar-footer"
                  aria-label={
                    language === "en-US" ? "Main navigation" : "主导航"
                  }
                >
                  {(["today", "learn", "career", "settings"] as const).map((key) => (
                    <a
                      className="settings-link"
                      href={`/app/${key}`}
                      key={key}
                      aria-current={
                        route === key ? "page" : undefined
                      }
                      onClick={(e) => {
                        e.preventDefault();
                        history.pushState(null, "", `/app/${key}`);
                        setRoute(key);
                        setNavigationNotice("");
                      }}
                    >
                      <span className={`nav-icon nav-icon-${key}`} aria-hidden="true">
                        <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
                          {key === "today" ? <><path d="M4 5.5h16v11H9l-5 3v-14Z" /><path d="M8 10h8M8 13h5" /></> : key === "learn" ? <><path d="M4 5.5c3-1 5.5-.5 8 1.2 2.5-1.7 5-2.2 8-1.2v13c-3-1-5.5-.5-8 1.2-2.5-1.7-5-2.2-8-1.2v-13Z" /><path d="M12 6.7v13" /></> : key === "career" ? <><rect x="3.5" y="7" width="17" height="12.5" rx="2" /><path d="M8.5 7V5.5A1.5 1.5 0 0 1 10 4h4a1.5 1.5 0 0 1 1.5 1.5V7M3.5 12h17M10 12v1.5h4V12" /></> : <><circle cx="12" cy="12" r="3" /><path d="M19 12a7 7 0 0 0-.1-1l1.5-1.2-1.5-2.6-1.8.6a7 7 0 0 0-1.7-1L15 5h-3l-.5 1.8a7 7 0 0 0-1.7 1L8 7.2 6.5 9.8 8 11a7 7 0 0 0 0 2l-1.5 1.2L8 16.8l1.8-.6a7 7 0 0 0 1.7 1L12 19h3l.4-1.8a7 7 0 0 0 1.7-1l1.8.6 1.5-2.6-1.5-1.2a7 7 0 0 0 .1-1Z" transform="translate(-1.5)" /></>}
                        </svg>
                      </span>
                      {language === "en-US" ? labels[key][1] : labels[key][0]}
                    </a>
                  ))}
                </nav>
                <div id="sidebar-history" className="sidebar-history-slot" />
              </aside>
              <div className="main-area">
                <main
                  id="main"
                  className={route === "today" ? "today-page" : "content-page"}
                  tabIndex={-1}
                >
                  <h1 className={route === "today" ? "sr-only" : undefined}>
                    {language === "en-US" ? labels[route][1] : labels[route][0]}
                  </h1>
                  {navigationNotice && (
                    <p className="notice">
                      {navigationNotice === "historical"
                        ? language === "en-US"
                          ? "This feature is deferred. Its personal records remain in historical information and export."
                          : "此功能已延期，个人记录仍可在历史资料中读取或导出。"
                        : language === "en-US"
                          ? "This previous link now belongs to this section."
                          : "原入口已合并到此分区。"}
                    </p>
                  )}
                  {route === "settings" && (
                    <section
                      className="settings-shortcuts"
                      aria-label={
                        language === "en-US"
                          ? "Workspace settings"
                          : "工作区设置"
                      }
                    >
                      <div className="settings-shortcut-links">
                        {(["goals", "profile"] as const).map((key) => (
                          <a
                            href={`/app/${key}`}
                            key={key}
                            onClick={(e) => {
                              e.preventDefault();
                              history.pushState(null, "", `/app/${key}`);
                              setRoute(key);
                              setNavigationNotice("");
                            }}
                          >
                            {language === "en-US"
                              ? labels[key][1]
                              : labels[key][0]}{" "}
                            <span aria-hidden="true">→</span>
                          </a>
                        ))}
                      </div>
                      <label className="settings-language">
                        {language === "en-US" ? "Language" : "语言"}
                        <select
                          aria-label={
                            language === "en-US" ? "Language" : "语言"
                          }
                          value={language}
                          onChange={(e) => setLanguage(e.target.value)}
                        >
                          <option value="zh-CN">中文</option>
                          <option value="en-US">English</option>
                        </select>
                      </label>
                    </section>
                  )}
                  {!owner ? (
                    <Loading
                      error={error}
                      retry={() => window.location.reload()}
                    />
                  ) : (
                    <ReferenceProvider>
                      <div className="assistant-host" hidden={route !== "today"}>
                        <Today key={owner} />
                      </div>
                      {route !== "today" && (
                        <React.Suspense
                          fallback={<p role="status">{language === "en-US" ? "Loading…" : "正在加载…"}</p>}
                        >
                          <Page key={`${owner}:${route}`} />
                        </React.Suspense>
                      )}
                    </ReferenceProvider>
                  )}
                </main>
              </div>
              <div className="feedback" role="status" aria-live="polite">
                {feedback}
              </div>
            </div>
          </SidebarVisibility.Provider>
        </Revision.Provider>
      </Feedback.Provider>
    </Locale.Provider>
  );
}

createRoot(document.getElementById("root")!).render(
  <Boundary>
    <Workspace />
  </Boundary>,
);
