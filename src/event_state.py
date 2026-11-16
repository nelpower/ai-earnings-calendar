"""Keep one event identity across date corrections and calendar subscriptions."""
from __future__ import annotations

import datetime as dt
import hashlib
import re
from dataclasses import replace

from src.events_model import Event


def legacy_uid(event: Event) -> str:
    return (f"{event.date}-{event.category}-"
            f"{hashlib.md5(event.title.encode('utf-8')).hexdigest()[:10]}@ai-earnings-calendar")


def _explicit_id(event: Event) -> str:
    value = event.meta.get("event_id")
    return str(value) if value and not event.meta.get("identity_generated") else ""


def _natural_key(event: Event) -> tuple:
    symbols = tuple(sorted({str(t).upper() for t in event.tickers}))
    if event.category == "earnings":
        ticker = event.meta.get("ticker")
        return event.category, (str(ticker).upper(),) if ticker else symbols, "report"
    if event.category == "ipo":
        # Infer phases for the feed generated before phase metadata existed.
        phase = event.meta.get("event_type")
        if not phase and (event.meta.get("ipo_date") or "解禁" in event.title):
            phase = "lockup"
        if not phase and "nasdaq.com" in event.source_url:
            phase = "pricing"
        if phase:
            issuer = event.meta.get("issuer_id") or event.meta.get("issuer_name")
            if not issuer:
                issuer = symbols or re.split(r"\s+IPO\b|\s+解禁", event.title,
                                             maxsplit=1, flags=re.IGNORECASE)[0].casefold().strip()
            return event.category, issuer, phase
    # Repeated monthly expiries/rebalances are separate occurrences. Keeping
    # the month in their key permits holiday corrections without reusing a UID
    # for the following month's occurrence.
    period = event.date[:7] if event.category in {"options", "index", "industry"} else ""
    return event.category, event.title, symbols, period


def semantic_key(event: Event) -> tuple:
    explicit = _explicit_id(event)
    return ("id", explicit) if explicit else _natural_key(event)


def same_event(left: Event, right: Event) -> bool:
    """An explicit source identity wins; generated tracking IDs do not hide keys.

    A source may add an explicit ID to an existing naturally identified event.
    Two different explicit IDs always describe different events.
    """
    left_id, right_id = _explicit_id(left), _explicit_id(right)
    if left_id and right_id:
        return left_id == right_id
    return _natural_key(left) == _natural_key(right)


def _calendar_content(event: Event) -> tuple:
    return (event.category, event.date, event.end_date, event.time_et, event.title,
            tuple(event.tickers), event.watch, event.source_url,
            event.meta.get("date_confirmed"), event.meta.get("date_label"),
            event.meta.get("status"),
            bool(event.meta.get("source_stale")), bool(event.meta.get("date_source_stale")),
            bool(event.meta.get("estimates_stale")),
            (event.meta.get("date_last_verified") or event.meta.get("last_verified"))
            if event.meta.get("source_stale") or event.meta.get("date_source_stale") else None,
            event.meta.get("estimates_last_verified") if event.meta.get("estimates_stale") else None)


def track_events(events: list[Event], previous: list[Event], today: dt.date) -> list[Event]:
    # Match globally by closest date, using each old occurrence at most once.
    # This prevents two current occurrences from inheriting one subscription UID.
    candidates = []
    for index, event in enumerate(events):
        for old_index, old in enumerate(previous):
            if not same_event(old, event):
                continue
            distance = abs((dt.date.fromisoformat(old.date) - dt.date.fromisoformat(event.date)).days)
            same_source_id = bool(_explicit_id(event) and _explicit_id(event) == _explicit_id(old))
            if (event.category == "earnings" and old.date < today.isoformat() <= event.date
                    and not same_source_id):
                continue  # A new quarter cannot inherit an expired report by ticker alone.
            if same_source_id or distance <= 45:
                candidates.append((0 if same_source_id else 1, distance, index, old_index))
    matched, used = {}, set()
    for _, _, index, old_index in sorted(candidates):
        if index not in matched and old_index not in used:
            matched[index] = previous[old_index]
            used.add(old_index)
    out = []
    for index, event in enumerate(events):
        old = matched.get(index)
        meta = dict(event.meta)
        old_meta = old.meta if old else {}
        seed = f"{event.category}|{semantic_key(event)}|{event.date}"
        explicit = _explicit_id(event) or (_explicit_id(old) if old else "")
        meta["event_id"] = explicit or old_meta.get("event_id") or meta.get("event_id") or hashlib.sha256(seed.encode()).hexdigest()[:24]
        meta["identity_generated"] = not bool(explicit)
        # First migration retains the UID already downloaded by subscribers.
        meta["ics_uid"] = old_meta.get("ics_uid") or (legacy_uid(old) if old else f'{meta["event_id"]}@ai-earnings-calendar')
        revision = old_meta.get("revision", 0)
        changed = bool(old and _calendar_content(old) != _calendar_content(event))
        meta["revision"] = revision + int(changed)
        meta["modified_on"] = today.isoformat() if changed or not old else old_meta.get("modified_on", today.isoformat())
        if old and old.date != event.date:
            meta.setdefault("original_proposed_date", old.meta.get("original_proposed_date") or old.date)
            meta["previous_date"] = old.date
        elif old:
            for field in ("original_proposed_date", "previous_date"):
                if field in old_meta:
                    meta.setdefault(field, old_meta[field])
        out.append(replace(event, meta=meta))
    return out
