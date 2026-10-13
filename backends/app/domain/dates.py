"""발행일 파싱·KST 변환. Tavily published_date(RFC 2822), ISO, "2025.01.15.", 상대 표기("19 hours ago", "3일 전")."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

KST = timezone(timedelta(hours=9))

_REL_EN = re.compile(r"(\d+)\s*(minute|min|hour|day|week|month)s?\s+ago", re.I)
_REL_KO = re.compile(r"(\d+)\s*(분|시간|일|주|개월)\s*전")
_DOTTED = re.compile(r"(\d{4})[.\-/]\s*(\d{1,2})[.\-/]\s*(\d{1,2})\.?(?:\s+(\d{1,2}):(\d{2}))?")
_UNIT = {"minute": "minutes", "min": "minutes", "hour": "hours", "day": "days", "week": "weeks",
         "분": "minutes", "시간": "hours", "일": "days", "주": "weeks"}


def parse_published(s: str | None, *, now: datetime | None = None) -> datetime | None:
    """KST aware datetime. 못 읽으면 None."""
    if not s:
        return None
    s = s.strip()
    now = (now or datetime.now(KST)).astimezone(KST)
    try:  # RFC 2822: "Tue, 11 Mar 2025 17:00:00 GMT"
        dt = parsedate_to_datetime(s)
        if dt is not None:
            return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).astimezone(KST)
    except (TypeError, ValueError, IndexError):
        pass
    try:  # ISO 8601
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return (dt if dt.tzinfo else dt.replace(tzinfo=KST)).astimezone(KST)
    except ValueError:
        pass
    m = _DOTTED.search(s)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        hh, mm = int(m.group(4) or 0), int(m.group(5) or 0)
        try:
            return datetime(y, mo, d, hh, mm, tzinfo=KST)
        except ValueError:
            return None
    for rx in (_REL_EN, _REL_KO):
        m = rx.search(s)
        if m:
            n, unit = int(m.group(1)), m.group(2).lower()
            if unit in ("month", "개월"):
                return now - timedelta(days=30 * n)
            return now - timedelta(**{_UNIT[unit]: n})
    return None

