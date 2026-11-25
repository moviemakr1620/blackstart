import { Zap } from "lucide-react";
import { FormEvent, useState } from "react";
import { useNavigate } from "react-router-dom";
import { VersionFooter } from "../components/VersionFooter";
import { api, setToken } from "../api";

export function Login() {
  const nav = useNavigate();
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const res = await api.post<{ token: string; must_change_password?: boolean }>("/api/auth/login", {
        username,
        password,
      });
      setToken(res.token);
      nav(res.must_change_password ? "/change-password" : "/");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Login failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="relative min-h-screen grid place-items-center px-4">
      <form onSubmit={onSubmit} className="card w-full max-w-md p-8 space-y-5 shadow-glow">
        <div className="flex items-center gap-3">
          <div className="h-11 w-11 rounded-2xl bg-mint/15 text-mint grid place-items-center">
            <Zap />
          </div>
          <div>
            <h1 className="text-xl font-semibold">Blackstart</h1>
            <p className="text-sm text-slate-500">Bring the lab down clean. Bring it back alive.</p>
          </div>
        </div>
        <div className="space-y-1">
          <label>Username</label>
          <input className="w-full" value={username} onChange={(e) => setUsername(e.target.value)} />
        </div>
        <div className="space-y-1">
          <label>Password</label>
          <input
            className="w-full"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </div>
        {error && <div className="text-sm text-rose">{error}</div>}
        <button className="btn-primary w-full" disabled={busy}>
          {busy ? "Signing in…" : "Enter control room"}
        </button>
        <p className="text-[11px] text-slate-500">
          First start? The admin password is in <span className="mono text-slate-300">initial-admin-password.txt</span>{" "}
          in the data folder (or the password you set in the environment). You will choose a new one after signing in.
        </p>
      </form>
      <VersionFooter className="absolute left-4 bottom-4" />
    </div>
  );
}
