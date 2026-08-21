let currentTz = "";

export function setAppTimezone(tz: string) {
  currentTz = tz || "";
}

export function appTimezone() {
  return currentTz;
}

function opts(extra: Intl.DateTimeFormatOptions = {}): Intl.DateTimeFormatOptions {
  return currentTz ? { timeZone: currentTz, ...extra } : extra;
}

function extraWithoutZone(options: Intl.DateTimeFormatOptions): Intl.DateTimeFormatOptions {
  const { timeZone: _ignored, ...rest } = options;
  return rest;
}

function safeFormat(d: Date, options: Intl.DateTimeFormatOptions, timeOnly = false) {
  const fmt = (o: Intl.DateTimeFormatOptions) =>
    timeOnly ? d.toLocaleTimeString(undefined, o) : d.toLocaleString(undefined, o);
  try {
    return fmt(options);
  } catch {
    return fmt(extraWithoutZone(options));
  }
}

export function formatDateTime(value: string | number | Date) {
  const d = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(d.getTime())) return "—";
  return safeFormat(d, opts());
}

export function formatTime(value: string | number | Date, withSeconds = false) {
  const d = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(d.getTime())) return "—";
  return safeFormat(
    d,
    opts({
      hour: "2-digit",
      minute: "2-digit",
      ...(withSeconds ? { second: "2-digit" } : {}),
    }),
    true,
  );
}

export function browserTimezone() {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

export const COMMON_TIMEZONES = [
  "UTC",
  "America/New_York",
  "America/Chicago",
  "America/Denver",
  "America/Los_Angeles",
  "America/Phoenix",
  "America/Anchorage",
  "Pacific/Honolulu",
  "America/Toronto",
  "America/Vancouver",
  "Europe/London",
  "Europe/Paris",
  "Europe/Berlin",
  "Europe/Amsterdam",
  "Asia/Tokyo",
  "Asia/Seoul",
  "Asia/Shanghai",
  "Asia/Singapore",
  "Australia/Sydney",
  "Pacific/Auckland",
];

export function listTimezones(): string[] {
  try {
    const intl = Intl as typeof Intl & { supportedValuesOf?: (key: string) => string[] };
    if (typeof intl.supportedValuesOf === "function") {
      return intl.supportedValuesOf("timeZone");
    }
  } catch {
    /* ignore */
  }
  return COMMON_TIMEZONES;
}
