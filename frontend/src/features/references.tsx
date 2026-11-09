import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { array, object, records, request, string } from "../shared/api";
import type { Options } from "../shared/forms";
import { Locale, title, useText } from "../shared/ui";
export const Revision = createContext({ revision: 0, changed: () => {} });
export const References = createContext<Options>({});
export function ReferenceProvider({ children }: { children: ReactNode }) {
  const t = useText();
  const locale = useContext(Locale);
  const { revision } = useContext(Revision);
  const [options, setOptions] = useState<Options>({});
  const [failed, setFailed] = useState(false);
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    let current = true;
    let hasFailure = false;
    const sources: Record<string, string> = {
      job_id: "/jobs",
      fact_ids: "/resume-facts",
      source_id: "/memories",
      material_id: "/materials",
      plan_id: "/plans",
      skill_id: "/catalogue/skill",
      resume_id: "/resumes",
      resume_version_id: "/resume-version-options",
      application_id: "/applications",
    };
    Promise.all(
      Object.entries(sources).map(async ([key, path]) => {
        try {
          return [key, records(await request(path))] as const;
        } catch {
          hasFailure = true;
          return [key, []] as const;
        }
      }),
    ).then(async (entries) => {
      if (!current) return;
      const next: Options = {};
      const resumes = entries.find(([key]) => key === "resume_id")?.[1] || [];
      const resumeTitles = new Map(resumes.map((resume) => [string(resume.id), title(resume)]));
      for (const [key, values] of entries)
        next[key] = values
          .filter((v) => key !== "fact_ids" || v.source_status === "current")
          .map((v) => ({
            value: string(v.id),
            label: (key === "resume_version_id"
              ? `${resumeTitles.get(string(v.resume_id)) || ""} · ${v.sequence || v.version}`
              : key === "application_id"
              ? `${string(object(v.job).title) || t("申请", "Application")} · ${string(object(v.job).company)} · ${string(v.status)}`
              : title(v)
            ).slice(0, 130),
          }));
      const plans = entries.find(([key]) => key === "plan_id")?.[1] || [];
      for (const plan of plans) {
        next[`plan_tasks:${string(plan.id)}`] = array(plan.tasks)
          .map(object)
          .map((task) => ({
            value: string(task.id),
            label: [string(task.title || task.skill_id), string(task.date)]
              .filter(Boolean)
              .join(" · "),
          }))
          .filter((task) => task.value);
      }
      next.job_ids = next.job_id;
      next.fact_id = next.fact_ids;
      next.favorite_ids = next.job_id;
      next.skill_ids = next.skill_id;
      if (current) {
        setOptions(next);
        setFailed(hasFailure);
      }
    });
    return () => {
      current = false;
    };
  }, [revision, locale, retry]);
  return (
    <References.Provider value={options}>
      {failed && (
        <div role="alert">
          {t("部分关联资料加载失败，选项可能不完整。", "Some related information could not be loaded; options may be incomplete.")}
          {" "}
          <button type="button" onClick={() => setRetry((value) => value + 1)}>
            {t("重试", "Retry")}
          </button>
        </div>
      )}
      {children}
    </References.Provider>
  );
}

