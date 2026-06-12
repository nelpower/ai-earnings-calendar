"""Generate an iCalendar feed (events.ics) for the event timeline, so the
calendar can be subscribed to from Apple/Google Calendar via the GitHub
Pages URL.

Timed events (time_et like "08:30") become 1-hour events converted ET -> UTC;
everything else becomes an all-day event spanning date..end_date.
"""
from __future__ import annotations

import datetime as dt
import hashlib
from pathlib import Path

from src.events_model import CATEGORIES, Event

_CAL_NAME = "AI 市场事件日历"


def _esc(s: str) -> str:
    return (str(s).replace("\\", "\\\\").replace(";", "\\;")
            .replace(",", "\\,").replace("\n", "\\n"))


def _fold(line: str) -> str:
    """RFC 5545 折行: lines over 75 octets continue on the next line after a
    space. Splits on UTF-8 character boundaries."""
    enc = line.encode("utf-8")
    if len(enc) <= 74:
        return line
    parts = []
    while enc:
        cut = min(74, len(enc))
        while cut < len(enc) and (enc[cut] & 0xC0) == 0x80:
            cut -= 1
        parts.append(enc[:cut].decode("utf-8"))
        enc = enc[cut:]
    return "\r\n ".join(parts)


def _et_to_utc(date_iso: str, hhmm: str) -> dt.datetime | None:
    try:
        from zoneinfo import ZoneInfo
        h, m = hhmm.split(":")
        local = dt.datetime.fromisoformat(date_iso).replace(
            hour=int(h), minute=int(m), tzinfo=ZoneInfo("America/New_York"))
        return local.astimezone(dt.timezone.utc)
    except Exception:  # noqa: BLE001  ("盘后" etc. -> treat as all-day)
        return None


def _vevent(e: Event, dtstamp: str) -> list[str]:
    uid = (f"{e.date}-{e.category}-"
           f"{hashlib.md5(e.title.encode('utf-8')).hexdigest()[:10]}"
           "@ai-earnings-calendar")
    summary = f"【{CATEGORIES.get(e.category, e.category)}】{e.title}"
    desc_bits = []
    if e.watch:
        desc_bits.append(f"看点: {e.watch}")
    if e.tickers:
        desc_bits.append("相关: " + " ".join(e.tickers))
    if e.source_url:
        desc_bits.append(e.source_url)

    lines = ["BEGIN:VEVENT", f"UID:{uid}", f"DTSTAMP:{dtstamp}"]
    start_utc = _et_to_utc(e.date, e.time_et) if e.time_et else None
    if start_utc:
        end_utc = start_utc + dt.timedelta(hours=1)
        lines.append(f"DTSTART:{start_utc:%Y%m%dT%H%M%SZ}")
        lines.append(f"DTEND:{end_utc:%Y%m%dT%H%M%SZ}")
    else:
        start = dt.date.fromisoformat(e.date)
        end = dt.date.fromisoformat(e.end_date) if e.end_date else start
        lines.append(f"DTSTART;VALUE=DATE:{start:%Y%m%d}")
        lines.append(f"DTEND;VALUE=DATE:{end + dt.timedelta(days=1):%Y%m%d}")
    lines.append(f"SUMMARY:{_esc(summary)}")
    if desc_bits:
        lines.append(f"DESCRIPTION:{_esc(chr(10).join(desc_bits))}")
    if e.source_url:
        lines.append(f"URL:{e.source_url}")
    lines.append("END:VEVENT")
    return lines


def build_ics_text(events: list[Event], today: dt.date) -> str:
    # DTSTAMP derived from the data day (not wall clock) so re-runs on the
    # same day produce byte-identical output -> no churn in git.
    dtstamp = f"{today:%Y%m%d}T000000Z"
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//ai-earnings-calendar//ZH//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{_esc(_CAL_NAME)}",
        "X-WR-TIMEZONE:Asia/Shanghai",
        "REFRESH-INTERVAL;VALUE=DURATION:PT12H",
        "X-PUBLISHED-TTL:PT12H",
    ]
    for e in sorted(events, key=lambda x: (x.date, x.category, x.title)):
        lines += _vevent(e, dtstamp)
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(l) for l in lines) + "\r\n"


def build_ics(events: list[Event], site_dir: Path, today: dt.date) -> Path:
    site_dir = Path(site_dir)
    site_dir.mkdir(parents=True, exist_ok=True)
    path = site_dir / "events.ics"
    path.write_text(build_ics_text(events, today), encoding="utf-8", newline="")
    return path
