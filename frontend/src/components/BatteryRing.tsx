export function BatteryRing({
  charge,
  onBattery,
  size = 220,
}: {
  charge: number | null | undefined;
  onBattery: boolean;
  size?: number;
}) {
  const pct = Math.max(0, Math.min(100, charge ?? 0));
  const r = 84;
  const c = 2 * Math.PI * r;
  const dash = (pct / 100) * c;
  const color = onBattery ? (pct <= 20 ? "#ff5c7a" : "#f5b942") : "#5ce1c5";
  return (
    <svg width={size} height={size} viewBox="0 0 220 220" className="mx-auto">
      <circle cx="110" cy="110" r={r} fill="none" stroke="#1a222e" strokeWidth="14" />
      <circle
        cx="110"
        cy="110"
        r={r}
        fill="none"
        stroke={color}
        strokeWidth="14"
        strokeLinecap="round"
        strokeDasharray={`${dash} ${c - dash}`}
        transform="rotate(-90 110 110)"
        style={{ filter: `drop-shadow(0 0 10px ${color}66)` }}
      />
      <text x="110" y="104" textAnchor="middle" fill="#e8eef4" fontSize="42" fontWeight="600" fontFamily="IBM Plex Sans">
        {charge == null ? "—" : Math.round(pct)}
      </text>
      <text x="110" y="128" textAnchor="middle" fill="#8b9bb0" fontSize="12" letterSpacing="3">
        {charge == null ? "NO DATA" : "% CHARGE"}
      </text>
    </svg>
  );
}

export function formatRuntime(sec?: number | null) {
  if (sec == null) return "—";
  const m = Math.max(0, Math.round(sec / 60));
  const h = Math.floor(m / 60);
  const mm = m % 60;
  if (h <= 0) return `${mm}m`;
  return `${h}h ${mm}m`;
}

export function statusLabel(status?: string, onBattery?: boolean) {
  if (onBattery && status === "low_battery") return "Low battery";
  if (onBattery) return "On battery";
  if (status === "charging") return "Online · charging";
  if (status === "online") return "Online";
  return status || "Unknown";
}
