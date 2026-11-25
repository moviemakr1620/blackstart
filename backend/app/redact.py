"""Strip secrets out of error text before it is stored, logged, or returned."""

from __future__ import annotations

import re

_QUERY = re.compile(r"(https?://[^\s'\"?]+)\?[^\s'\"]*", re.I)
_USERINFO = re.compile(r"(https?://)[^/\s:@'\"]+:[^/\s@'\"]+@", re.I)
_KV = re.compile(
    r"(?i)\b(passwd|password|pass|token|secret|_sid|sid|api[_-]?key|otp_code)=([^&\s'\"]+)"
)
_DISCORD = re.compile(r"(?i)(discord(?:app)?\.com/api/webhooks/\d+/)[\w-]+")
_PVE = re.compile(r"(PVEAPIToken=[^=\s]+=)[\w-]+")


def redact(text: str, limit: int = 600) -> str:
    out = str(text or "")
    out = _USERINFO.sub(r"\1***@", out)
    out = _QUERY.sub(r"\1?…", out)
    out = _KV.sub(r"\1=***", out)
    out = _DISCORD.sub(r"\1***", out)
    out = _PVE.sub(r"\1***", out)
    if len(out) > limit:
        out = out[:limit] + "…"
    return out
