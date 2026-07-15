// Fetch wrapper carrying the per-session token.
//
// In the desktop app the window is opened at /?token=<secret>; we capture
// it into memory and immediately strip it from the address bar. In `npm
// run dev` the Vite proxy injects the header itself, but we also fall
// back to the fixed dev token so EventSource/audio URLs (query-param
// auth) work identically.

let token = "";

export function initToken(): void {
  const url = new URL(window.location.href);
  const fromQuery = url.searchParams.get("token");
  if (fromQuery) {
    token = fromQuery;
    url.searchParams.delete("token");
    history.replaceState(
      null,
      "",
      url.pathname + (url.searchParams.size ? `?${url.searchParams}` : "") + url.hash,
    );
  } else if (import.meta.env.DEV) {
    token = "dev";
  }
}

/** For the two GETs that cannot send headers (EventSource, <audio>). */
export function tokenQuery(): string {
  return `token=${encodeURIComponent(token)}`;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: {
      "X-Transcribr-Token": token,
      ...(init?.body !== undefined ? { "Content-Type": "application/json" } : {}),
      ...(init?.headers ?? {}),
    },
  });
  if (!res.ok) {
    let code = "http_error";
    let message = res.statusText;
    try {
      const body = await res.json();
      code = body?.error?.code ?? code;
      message = body?.error?.message ?? message;
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, code, message);
  }
  return res.json() as Promise<T>;
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  put: <T>(path: string, body: unknown) =>
    request<T>(path, { method: "PUT", body: JSON.stringify(body) }),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, {
      method: "POST",
      body: body === undefined ? undefined : JSON.stringify(body),
    }),
};
