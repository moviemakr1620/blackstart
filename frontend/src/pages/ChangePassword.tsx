import { KeyRound } from "lucide-react";
import { FormEvent, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, setToken } from "../api";

export function ChangePassword() {
  const nav = useNavigate();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [again, setAgain] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError("");
    if (next !== again) {
      setError("The new passwords do not match.");
      return;
    }
    setBusy(true);
    try {
      const res = await api.post<{ token: string }>("/api/auth/password", {
        current_password: current,
        new_password: next,
      });
      setToken(res.token);
      nav("/");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not change the password");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="min-h-screen grid place-items-center px-4">
      <form onSubmit={onSubmit} className="card w-full max-w-md p-8 space-y-5 shadow-glow">
        <div className="flex items-center gap-3">
          <div className="h-11 w-11 rounded-2xl bg-amber/15 text-amber grid place-items-center">
            <KeyRound />
          </div>
          <div>
            <h1 className="text-xl font-semibold">Choose a new password</h1>
            <p className="text-sm text-slate-500">
              This account still has a first-run or default password. Pick your own before continuing.
            </p>
          </div>
        </div>
        <div className="space-y-1">
          <label>Current password</label>
          <input className="w-full" type="password" value={current} onChange={(e) => setCurrent(e.target.value)} />
        </div>
        <div className="space-y-1">
          <label>New password (10+ characters)</label>
          <input className="w-full" type="password" value={next} onChange={(e) => setNext(e.target.value)} />
        </div>
        <div className="space-y-1">
          <label>New password again</label>
          <input className="w-full" type="password" value={again} onChange={(e) => setAgain(e.target.value)} />
        </div>
        {error && <div className="text-sm text-rose">{error}</div>}
        <button className="btn-primary w-full" disabled={busy || next.length < 10}>
          {busy ? "Saving…" : "Save and continue"}
        </button>
      </form>
    </div>
  );
}
