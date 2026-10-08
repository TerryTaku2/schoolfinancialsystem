// Thin fetch wrapper: JSON in/out, CSRF header, unified errors.
export class ApiError extends Error {
  constructor(message, status, fields) {
    super(message);
    this.status = status;
    this.fields = fields || {};
  }
}

// Path the app is served under ("" standalone, "/school" inside the chatbot).
const ROOT = document.querySelector('meta[name="app-root"]')?.content || "";

let onUnauthorized = () => {};
export function setUnauthorizedHandler(fn) { onUnauthorized = fn; }

async function request(method, url, data) {
  const opts = { method, headers: { "X-Requested-With": "SchoolMS" }, credentials: "same-origin" };
  if (data !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(data);
  }
  let res;
  try {
    res = await fetch(ROOT + "/api" + url, opts);
  } catch {
    throw new ApiError("Cannot reach the server. Check your connection.", 0);
  }
  let payload = null;
  try { payload = await res.json(); } catch { /* non-JSON */ }
  if (!res.ok) {
    if (res.status === 401 && !url.startsWith("/auth/login")) onUnauthorized();
    throw new ApiError(payload?.error || `Request failed (${res.status})`, res.status, payload?.fields);
  }
  return payload;
}

export function qs(params = {}) {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== "") p.set(k, v);
  const s = p.toString();
  return s ? "?" + s : "";
}

export const api = {
  get: (url, params) => request("GET", url + qs(params)),
  post: (url, data = {}) => request("POST", url, data),
  put: (url, data = {}) => request("PUT", url, data),
  del: (url) => request("DELETE", url),
};
