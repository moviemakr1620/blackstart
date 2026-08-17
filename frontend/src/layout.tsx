import {
  Activity,
  BookOpen,
  History as HistoryIcon,
  LayoutDashboard,
  Settings as SettingsIcon,
  Server,
  Zap,
} from "lucide-react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { setToken } from "./api";

const links = [
  { to: "/", label: "Dashboard", icon: LayoutDashboard, end: true },
  { to: "/playbooks", label: "Playbooks", icon: BookOpen },
  { to: "/inventory", label: "Inventory", icon: Server },
  { to: "/history", label: "History", icon: HistoryIcon },
  { to: "/settings", label: "Settings", icon: SettingsIcon },
];

export function Layout() {
  const navigate = useNavigate();
  return (
    <div className="min-h-screen grid grid-cols-[240px_1fr]">
      <aside className="border-r border-white/5 bg-ink-900/70 backdrop-blur px-4 py-6 flex flex-col">
        <div className="flex items-center gap-2 px-2 mb-8">
          <div className="h-9 w-9 rounded-xl bg-mint/15 text-mint grid place-items-center">
            <Zap size={18} />
          </div>
          <div>
            <div className="font-semibold tracking-wide">BLACKSTART</div>
            <div className="text-[10px] uppercase tracking-[0.2em] text-slate-500">Power orchestrator</div>
          </div>
        </div>
        <nav className="space-y-1">
          {links.map((l) => (
            <NavLink
              key={l.to}
              to={l.to}
              end={l.end}
              className={({ isActive }) =>
                `flex items-center gap-3 px-3 py-2 rounded-xl text-sm ${
                  isActive ? "bg-mint/10 text-mint" : "text-slate-400 hover:bg-white/5 hover:text-slate-100"
                }`
              }
            >
              <l.icon size={16} />
              {l.label}
            </NavLink>
          ))}
        </nav>
        <div className="mt-auto px-2 text-[11px] text-slate-500 space-y-3">
          <div className="flex items-center gap-2">
            <Activity size={12} className="text-mint" />
            Engine watching UPS
          </div>
          <button
            className="text-slate-500 hover:text-slate-200"
            onClick={() => {
              setToken(null);
              navigate("/login");
            }}
          >
            Sign out
          </button>
        </div>
      </aside>
      <main className="min-w-0">
        <Outlet />
      </main>
    </div>
  );
}
