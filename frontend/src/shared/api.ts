export type Json =
  null | boolean | number | string | Json[] | { [key: string]: Json };
export type Data = { [key: string]: Json };
export const object = (value: Json | undefined): Data =>
  value && typeof value === "object" && !Array.isArray(value) ? value : {};
export const array = (value: Json | undefined): Json[] =>
  Array.isArray(value) ? value : [];
export const string = (value: Json | undefined): string =>
  typeof value === "string"
    ? value
    : typeof value === "number"
      ? String(value)
      : "";
export const number = (value: Json | undefined): number =>
  typeof value === "number" ? value : 0;
export const records = (value: Json): Data[] => {
  if (Array.isArray(value)) return value.map(object);
  const data = object(value);
  for (const key of [
    "items",
    "results",
    "records",
    "notifications",
    "tasks",
    "skills",
  ]) {
    if (Array.isArray(data[key])) return array(data[key]).map(object);
  }
  return Object.keys(data).length ? [data] : [];
};

export class ApiError extends Error {
  constructor(
    public code: string,
    message: string,
    public status: number,
    public trace: string,
  ) {
    super(message);
  }
}

let workspace = "";
let language = "zh-CN";
export function configure(owner: string, locale: string) {
  // Keep the old context when a reset unmounts the UI. Late callbacks must
  // still be rejected as stale, rather than sending an unscoped write.
  if (owner) workspace = owner;
  language = locale;
}
export function currentWorkspace() {
  return workspace;
}
export function headers(): Record<string, string> {
  return { "Accept-Language": language, "X-Workspace-ID": workspace };
}

export async function request(
  path: string,
  init: RequestInit = {},
): Promise<Json> {
  const response = await fetch(
    path.startsWith("/api/") ? path : `/api/v1${path}`,
    {
      ...init,
      headers: { ...headers(), ...init.headers },
    },
  );
  if (!response.ok) {
    let data: Data = {};
    try {
      data = object(await response.json());
    } catch {
      /* A connection proxy can return plain text. */
    }
    throw new ApiError(
      string(data.code),
      string(data.message) || `${response.status} ${response.statusText}`,
      response.status,
      string(data.trace_id),
    );
  }
  return response.status === 204 ? null : await response.json();
}

export async function command(
  path: string,
  body: Json,
  method = "POST",
  version?: number,
  key: string = crypto.randomUUID(),
): Promise<Json> {
  return request(path, {
    method,
    headers: {
      "Content-Type": "application/json",
      "Idempotency-Key": key,
      ...(version !== undefined ? { "If-Match": `"${version}"` } : {}),
    },
    body: JSON.stringify(body),
  });
}

export async function download(
  path: string,
  name: string,
  body?: Json,
  version?: number,
) {
  const response = await fetch(`/api/v1${path}`, {
    method: body ? "POST" : "GET",
    headers: {
      ...headers(),
      ...(body ? { "Content-Type": "application/json" } : {}),
      ...(version !== undefined ? { "If-Match": `"${version}"` } : {}),
    },
    ...(body ? { body: JSON.stringify(body) } : {}),
  });
  if (!response.ok)
    throw new Error(`${response.status} ${response.statusText}`);
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  try {
    const link = document.createElement("a");
    link.href = url;
    link.download = name;
    link.click();
  } finally {
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
}
