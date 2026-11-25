const TOKEN_KEY = "blackstart.token";

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string | null) {
  if (token) localStorage.setItem(TOKEN_KEY, token);
  else localStorage.removeItem(TOKEN_KEY);
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (!headers.has("Content-Type") && init.body) headers.set("Content-Type", "application/json");
  const token = getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const resp = await fetch(path, { ...init, headers });
  if (resp.status === 401) {
    setToken(null);
    if (!path.includes("/api/auth/login")) {
      window.location.href = "/login";
    }
  }
  if (resp.status === 403 && window.location.pathname !== "/change-password") {
    const text = await resp.clone().text();
    if (text.includes("password_change_required")) {
      window.location.href = "/change-password";
    }
  }
  if (!resp.ok) {
    let detail = resp.statusText;
    try {
      const body = await resp.json();
      detail = body.detail || JSON.stringify(body);
    } catch {
      detail = await resp.text();
    }
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  if (resp.status === 204) return undefined as T;
  return resp.json();
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) }),
  put: <T>(path: string, body: unknown) => request<T>(path, { method: "PUT", body: JSON.stringify(body) }),
  del: <T>(path: string) => request<T>(path, { method: "DELETE" }),
};

export function wsUrl(): string {
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${window.location.host}/api/ws`;
}

/** Open the live-updates socket. The token goes in the first message, not the URL. */
export function openLiveSocket(onMessage: (data: any) => void): WebSocket {
  const ws = new WebSocket(wsUrl());
  ws.onopen = () => ws.send(JSON.stringify({ token: getToken() || "" }));
  ws.onmessage = (msg) => {
    try {
      onMessage(JSON.parse(msg.data));
    } catch {
      /* ignore */
    }
  };
  return ws;
}
