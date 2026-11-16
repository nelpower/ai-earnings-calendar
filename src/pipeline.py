"""Unified AI market-event calendar pipeline.

Merges every provider (earnings, macro/Fed, deterministic options/index,
conferences/IPOs) into one timeline, then writes JSON + the static site.

    python -m src.pipeline                  # full run
    python -m src.pipeline --throttle 0.2   # gentler on Yahoo
    python -m src.pipeline --no-ipo         # skip the Nasdaq IPO scrape
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
from dataclasses import replace
from pathlib import Path

from src import config
from src.archive import archive_expired
from src.build_ics import build_ics
from src.build_site import build_site
from src.events_model import Event
from src.event_state import same_event, track_events
from src.quality import (build_report, carry_source_history, read_json,
                         recover_failed, workflow_summary, write_report)
from src.source_health import reports, reset_health
from src.discovery import discover
from src.providers import (
    deterministic_events,
    earnings_events,
    industry_events,
    ipo_events,
    lockup_events,
    static_events,
)


def _d(s: str) -> dt.date:
    return dt.date.fromisoformat(s)


def _merge_ipo_events(curated: list[Event], scraped: list[Event],
                      conflicts: list[dict] | None = None) -> list[Event]:
    """Backstop each IPO phase, upgrading an estimate when actual pricing arrives.

    Match issuer + phase even when the estimated date changes. Pricing and
    listing remain distinct; lockups are supplied separately.
    """
    merged = list(curated)
    for event in scraped:
        phase = event.meta.get("event_type")
        symbols = {str(t).upper() for t in event.tickers}
        matches = [e for e in merged if phase and e.category == "ipo"
                   and e.meta.get("event_type") == phase
                   and symbols.intersection(str(t).upper() for t in e.tickers)]
        if not matches:
            merged.append(event)
        elif (event.meta.get("date_confirmed") is True
              and not any(e.meta.get("date_confirmed") is True for e in matches)):
            merged = [e for e in merged if e not in matches]
            merged.append(replace(event, importance=max(
                event.importance, *(e.importance for e in matches))))
        elif conflicts is not None:
            for old in matches:
                if old.date != event.date:
                    conflicts.append({"title": old.title, "current_date": old.date,
                                      "other_date": event.date,
                                      "source_url": old.source_url,
                                      "other_source_url": event.source_url})
    return merged


_ESTIMATE_FIELDS = ("eps_estimate", "revenue_estimate", "revenue_currency",
                    "last_quarter", "last_eps_actual", "last_eps_estimate",
                    "eps_low", "eps_high", "revenue_low", "revenue_high")


def _ticker(event: Event) -> str:
    return str(event.meta.get("ticker") or next(iter(event.tickers), "")).upper()


def _has_estimates(event: Event) -> bool:
    return any(event.meta.get(field) is not None and event.meta.get(field) != ""
               for field in _ESTIMATE_FIELDS)


def _earnings_provenance(event: Event, day: str) -> Event:
    """Keep date and consensus verification independent within the same card."""
    meta = dict(event.meta)
    source = meta.get("source") or "earnings"
    verified = meta.get("last_verified") or day
    meta.setdefault("source", source)
    meta.setdefault("source_partition", _ticker(event))
    meta.setdefault("date_source_type", source)
    meta.setdefault("date_source", event.source_url)
    meta.setdefault("date_source_partition", meta["source_partition"])
    meta.setdefault("date_last_verified", verified)
    meta.setdefault("date_source_stale", bool(meta.get("source_stale")))
    if meta.get("source_stale"):
        meta["date_source_stale"] = True
    if _has_estimates(event):
        meta.setdefault("estimates_source_type", "earnings")
        meta.setdefault("estimates_source_partition", _ticker(event))
        # Migrating an old combined official/Yahoo card can only preserve its
        # last known data day; loading it again is not a new consensus check.
        meta.setdefault("estimates_last_verified", verified)
        meta.setdefault("estimates_stale", bool(meta.get("source_stale")))
    return replace(event, meta=meta)


def _earnings_conflict(official: Event, yahoo: Event, conflicts: list[dict] | None,
                       resolution: str) -> None:
    if conflicts is not None and official.date != yahoo.date:
        row = {"category": "earnings", "tickers": list(official.tickers),
               "title": official.title, "current_date": official.date,
               "other_date": yahoo.date, "source_url": official.source_url,
               "other_source_url": yahoo.source_url, "resolution": resolution}
        if row not in conflicts:
            conflicts.append(row)


def _merge_official_events(current: list[Event], official: list[Event],
                           conflicts: list[dict] | None = None,
                           today: dt.date | None = None) -> list[Event]:
    """Official earnings dates override Yahoo while retaining consensus figures."""
    day = (today or config.today_local()).isoformat()
    merged = [_earnings_provenance(e, day) if e.category == "earnings" else e for e in current]
    for event in official:
        if event.category == "earnings":
            event = _earnings_provenance(event, day)
        matches = [e for e in merged if event.category == "earnings"
                   and e.category == "earnings" and _ticker(e) == _ticker(event)]
        if matches:
            base = matches[0]
            meta = {**base.meta, **event.meta}
            meta.update(date_source=event.source_url,
                        date_source_type=event.meta.get("source", "official_discovery"),
                        date_source_partition=event.meta.get("source_partition", ""),
                        date_last_verified=event.meta.get("last_verified") or day,
                        date_source_stale=False, source_stale=False)
            # Date-only official records must not claim to re-verify estimates.
            for field in ("estimates_source_type", "estimates_source_partition",
                          "estimates_last_verified", "estimates_stale"):
                if field in base.meta:
                    meta[field] = base.meta[field]
            _earnings_conflict(event, base, conflicts, "official_calendar")
            merged = [e for e in merged if e not in matches]
            merged.append(replace(base, date=event.date, time_et=event.time_et or base.time_et,
                                  source_url=event.source_url, meta=meta,
                                  importance=max(base.importance, event.importance)))
        elif event.category == "ipo":
            merged = _merge_ipo_events(merged, [event], conflicts)
        else:
            same = [e for e in merged if same_event(e, event)]
            if same:
                merged = [e for e in merged if e not in same]
                merged.append(replace(event, importance=max(event.importance, *(e.importance for e in same))))
            else:
                merged.append(event)
    return merged


def _restore_earnings_components(events: list[Event], previous: list[Event],
                                 source_reports: list[dict], today: dt.date,
                                 previous_day: str = "",
                                 conflicts: list[dict] | None = None) -> list[Event]:
    """A source outage cannot undo the other source's last verified component.

    Only still-future previous reports can backstop a current one. An expired
    quarter must never replace a newly announced future quarter for its ticker.
    """
    statuses = {(r["source"], r["partition"]): r for r in source_reports}
    failed = {(r["source"], r["partition"]) for r in source_reports
              if r.get("status") in {"failed", "invalid_schema"}}
    result = []
    for item in events:
        if item.category != "earnings":
            result.append(item)
            continue
        event = _earnings_provenance(item, today.isoformat())
        olds = [old for old in previous if old.category == "earnings"
                and old.date >= today.isoformat() and same_event(old, event)
                and abs((_d(old.date) - _d(event.date)).days) <= 45]
        old = min(olds, key=lambda e: abs((_d(e.date) - _d(event.date)).days)) if olds else None
        if old:
            old = _earnings_provenance(old, previous_day or "未知")
            old_date_key = (old.meta.get("date_source_type"), old.meta.get("date_source_partition"))
            current_official = (event.meta.get("date_source_type") == "official_discovery"
                                and not event.meta.get("date_source_stale"))
            if (old_date_key[0] == "official_discovery" and old_date_key in failed
                    and not current_official):
                # Keep fresh Yahoo consensus, while restoring the cached date's
                # source, URL, identity, time and original verification day.
                yahoo = event
                meta = dict(event.meta)
                for field in ("event_id", "identity_generated", "date_source", "date_source_type",
                              "date_source_partition", "date_last_verified", "date_confirmed",
                              "date_label", "status"):
                    if field in old.meta:
                        meta[field] = old.meta[field]
                meta.update(source="official_discovery", source_partition=old_date_key[1],
                            last_verified=old.meta["date_last_verified"],
                            date_source_stale=True, source_stale=True,
                            stale_since=old.meta.get("stale_since") or today.isoformat())
                event = replace(event, date=old.date, end_date=old.end_date, time_et=old.time_et,
                                source_url=old.meta.get("date_source") or old.source_url, meta=meta)
                _earnings_conflict(event, yahoo, conflicts, "needs_review")

            estimates_key = ("earnings", _ticker(event))
            if estimates_key in failed and _has_estimates(old):
                meta = dict(event.meta)
                for field in _ESTIMATE_FIELDS:
                    if field in old.meta:
                        meta[field] = old.meta[field]
                meta.update(estimates_source_type="earnings", estimates_source_partition=_ticker(event),
                            estimates_last_verified=old.meta.get("estimates_last_verified")
                            or old.meta.get("last_verified") or previous_day or "未知",
                            estimates_stale=True)
                event = replace(event, meta=meta)
        estimate_report = statuses.get(("earnings", _ticker(event)), {})
        if (estimate_report.get("status") == "empty"
                or (estimate_report.get("status") == "ok" and estimate_report.get("accepted") == 0)):
            # A successful empty/no-in-window Yahoo result clears its component,
            # even when the date itself is retained during an official outage.
            meta = dict(event.meta)
            for field in _ESTIMATE_FIELDS:
                meta.pop(field, None)
            meta.update(estimates_source_type="earnings", estimates_source_partition=_ticker(event),
                        estimates_last_verified=estimate_report.get("checked_on") or today.isoformat(),
                        estimates_stale=False)
            event = replace(event, meta=meta)
        # Generic source recovery also handles Yahoo-only records. Reflect its
        # provenance without advancing the age of a reused consensus snapshot.
        if event.meta.get("date_source_type") == "earnings" and ("earnings", _ticker(event)) in failed:
            meta = dict(event.meta)
            meta["date_source_stale"] = bool(meta.get("source_stale"))
            if _has_estimates(event):
                meta["estimates_stale"] = True
            event = replace(event, meta=meta)
        result.append(event)
    return result


def _coalesce_earnings(events: list[Event]) -> list[Event]:
    # Do not reintroduce an old official-calendar record beside a fresh Yahoo
    # record solely because the official source failed during a date change.
    groups: dict[tuple, list[Event]] = {}
    other = []
    for e in events:
        if e.category == "earnings" and e.tickers:
            groups.setdefault(tuple(sorted(e.tickers)), []).append(e)
        else:
            other.append(e)
    for group in groups.values():
        other.append(max(group, key=lambda e: (
            e.meta.get("date_source_type", e.meta.get("source")) == "official_discovery",
            not e.meta.get("date_source_stale", e.meta.get("source_stale")),
            e.meta.get("date_confirmed") is True, e.importance)))
    return other


def run(
    outputs_dir: Path = config.OUTPUTS_DIR,
    site_dir: Path | None = None,
    window_days: int = config.WINDOW_DAYS,
    horizon_days: int = config.PREVIEW_DAYS,
    throttle: float = 0.0,
    use_ipo: bool = True,
    use_discovery: bool = True,
    today: dt.date | None = None,
) -> tuple[list[Event], list[Event]]:
    today = today or config.today_local()   # Beijing calendar day
    end = today + dt.timedelta(days=horizon_days)
    reset_health(today)
    previous_payload = read_json(outputs_dir / config.EVENTS_JSON.name, {})
    previous = [Event.from_dict(e) for e in previous_payload.get("events", [])]
    previous_report = read_json(outputs_dir / "source_status.json", {})
    previous_candidates = read_json(outputs_dir / "candidates.json", {}).get("candidates", [])
    conflicts: list[dict] = []

    # Archive yesterday's expired events BEFORE events.json gets overwritten —
    # the outcome library compounds daily; a failure here never blocks the run.
    try:
        archive_expired(today, outputs_dir, throttle=throttle)
    except Exception as exc:  # noqa: BLE001
        print(f"[archive] skipped ({exc})")

    events: list[Event] = []
    static = static_events()
    events += static
    events += deterministic_events(today, end)
    events += industry_events(today, end)
    if use_ipo:
        events = _merge_ipo_events(events, ipo_events(today, horizon_days), conflicts)
        events += lockup_events(today, horizon_days)

    events += earnings_events(today, horizon_days, throttle=throttle)
    candidates = previous_candidates
    official = []
    if use_discovery:
        official, candidates = discover(today, horizon_days, previous_candidates)
    source_reports = carry_source_history(reports(), previous_report)
    events = _merge_official_events(events, official, conflicts, today)
    events = recover_failed(events, previous, source_reports, today,
                            previous_payload.get("generated", ""))
    events = _restore_earnings_components(events, previous, source_reports, today,
                                          previous_payload.get("generated", ""), conflicts)
    # A cached Nasdaq record must not duplicate a curated IPO backstop.
    scraped_ipos = [e for e in events if e.category == "ipo" and e.meta.get("source") == "nasdaq_ipo"]
    rest = [e for e in events if e not in scraped_ipos]
    events = _merge_ipo_events(rest, scraped_ipos, conflicts)
    events = _coalesce_earnings(events)

    # keep only events inside the display window [today, today+horizon]
    events = [e for e in events if today <= _d(e.date) <= end]
    events.sort(key=lambda e: (e.date, -e.importance, e.category))
    events = track_events(events, previous, today)

    issuers = config.load_yaml(config.DATA_DIR / "issuer_watchlist.yaml").get("issuers", [])
    health = build_report(events, previous, source_reports, candidates, static, today,
                          len(config.load_companies()), len(issuers), conflicts)

    def days(e: Event) -> int:
        return (_d(e.date) - today).days

    this_week = [e for e in events if days(e) <= window_days]
    upcoming = [e for e in events if window_days < days(e) <= horizon_days]

    outputs_dir.mkdir(parents=True, exist_ok=True)
    # "generated" is the data day, not a wall-clock timestamp — re-runs on the
    # same day with unchanged data produce identical JSON, so the CI commit
    # step has nothing to push (was: 3 no-op commits/day).
    (outputs_dir / config.EVENTS_JSON.name).write_text(
        json.dumps({"generated": today.isoformat(),
                    "today": today.isoformat(),
                    "events": [e.to_dict() for e in events]},
                   ensure_ascii=False, indent=2),
        encoding="utf-8")

    target_site = site_dir or config.SITE_DIR
    index = build_site(this_week, upcoming, target_site, today, health=health,
                       candidates=candidates)
    build_ics(this_week + upcoming, site_dir or config.SITE_DIR, today)
    write_report(health, candidates, outputs_dir, Path(target_site))
    workflow_summary(health)

    from collections import Counter
    by_cat = Counter(e.category for e in events)
    print(f"[pipeline] {len(events)} event(s) in next {horizon_days}d "
          f"| this week: {len(this_week)} | by category: {dict(by_cat)}")
    print(f"[pipeline] wrote {outputs_dir / config.EVENTS_JSON.name} and {index}")
    return this_week, upcoming


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description="AI market-event calendar pipeline")
    p.add_argument("--outputs-dir", type=Path, default=config.OUTPUTS_DIR)
    p.add_argument("--site-dir", type=Path, default=config.SITE_DIR)
    p.add_argument("--window-days", type=int, default=config.WINDOW_DAYS)
    p.add_argument("--horizon-days", type=int, default=config.PREVIEW_DAYS)
    p.add_argument("--throttle", type=float, default=0.0)
    p.add_argument("--no-ipo", action="store_true", help="skip the Nasdaq IPO scrape")
    p.add_argument("--no-discovery", action="store_true", help="skip official-source discovery")
    args = p.parse_args(argv)
    run(outputs_dir=args.outputs_dir, site_dir=args.site_dir,
        window_days=args.window_days, horizon_days=args.horizon_days,
        throttle=args.throttle, use_ipo=not args.no_ipo, use_discovery=not args.no_discovery)


if __name__ == "__main__":
    main()

