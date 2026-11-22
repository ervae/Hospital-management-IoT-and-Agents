// Sunucu ile konuşan tek yer. Oturum anahtarı burada tutulur.
const BASE = "/api/v1";
const KEY = "hastane.token";

export const auth = {
  get token() { try { return localStorage.getItem(KEY); } catch { return null; } },
  set token(v) { try { v ? localStorage.setItem(KEY, v) : localStorage.removeItem(KEY); } catch { /* yok say */ } },
  user: null,
  async login(username, password) {
    const r = await request("POST", "/auth/login", { username, password }, false);
    this.token = r.access_token;
    this.user = r.user;
    return r.user;
  },
  logout() { this.token = null; this.user = null; },
};

async function request(method, path, body, withAuth = true) {
  const headers = { "Content-Type": "application/json" };
  if (withAuth && auth.token) headers.Authorization = `Bearer ${auth.token}`;
  const res = await fetch(BASE + path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  if (res.status === 401 && withAuth) {
    auth.logout();
    window.dispatchEvent(new Event("auth:expired"));
  }
  if (!res.ok) {
    let msg = `Hata ${res.status}`;
    try {
      const j = await res.json();
      msg = typeof j.detail === "string" ? j.detail : (j.detail?.[0]?.msg ?? msg);
    } catch { /* gövde yok */ }
    const err = new Error(msg); err.status = res.status; throw err;
  }
  return res.status === 204 ? null : res.json();
}

export const api = {
  get: (p) => request("GET", p),
  post: (p, b) => request("POST", p, b ?? {}),
  patch: (p, b) => request("PATCH", p, b),
};
