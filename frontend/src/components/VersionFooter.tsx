import { useEffect, useState } from "react";

type VersionInfo = {
  version: string;
  commit: string;
  build_date?: string;
  source?: string;
  update_available?: boolean;
  latest_commit?: string;
};

export function VersionFooter({ className = "" }: { className?: string }) {
  const [info, setInfo] = useState<VersionInfo | null>(null);
  useEffect(() => {
    fetch("/api/version")
      .then((r) => (r.ok ? r.json() : null))
      .then((body) => body && setInfo(body))
      .catch(() => undefined);
  }, []);
  if (!info) return null;
  const commit = (info.commit || "").slice(0, 7);
  const latest = (info.latest_commit || "").slice(0, 7);
  return (
    <div className={`text-[10px] leading-4 text-slate-500 ${className}`}>
      <div className="mono">
        v{info.version}
        {commit ? ` · ${commit}` : ""}
      </div>
      {info.update_available && (
        <a
          href="https://github.com/moviemakr1620/blackstart#updates"
          className="text-amber hover:underline"
          title={latest ? `main is at ${latest}` : "A newer commit is on GitHub"}
        >
          Update available{latest ? ` · ${latest}` : ""}
        </a>
      )}
    </div>
  );
}
