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
from pathlib import Path

from src import config
from src.build_site import build_site
from src.events_model import Event
from src.providers import (
    deterministic_events,
    earnings_events,
    ipo_events,
    static_events,
)


def _d(s: str) -> dt.date:
    return dt.date.fromisoformat(s)


def _last_good_earnings(outputs_dir: Path) -> list[Event]:
    """Reuse previously committed earnings if a live fetch returns nothing
    (e.g. Yahoo blocked the runner) so the calendar never loses that section."""
    path = outputs_dir / config.EVENTS_JSON.name
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return [Event.from_dict(e) for e in data.get("events", [])
                if e.get("category") == "earnings"]
    except Exception:  # noqa: BLE001
        return []


def run(
    outputs_dir: Path = config.OUTPUTS_DIR,
    site_dir: Path | None = None,
    window_days: int = config.WINDOW_DAYS,
    horizon_days: int = config.PREVIEW_DAYS,
    throttle: float = 0.0,
    use_ipo: bool = True,
    today: dt.date | None = None,
) -> tuple[list[Event], list[Event]]:
    today = today or config.today_et()   # US-Eastern calendar day
    end = today + dt.timedelta(days=horizon_days)

    events: list[Event] = []
    events += static_events()
    events += deterministic_events(today, end)
    if use_ipo:
        events += ipo_events(today, horizon_days)

    earn = earnings_events(today, horizon_days, throttle=throttle)
    if not earn:
        print("[pipeline] no earnings fetched; reusing last committed earnings.")
        earn = _last_good_earnings(outputs_dir)
    events += earn

    # keep only events inside the display window [today, today+horizon]
    events = [e for e in events if today <= _d(e.date) <= end]
    events.sort(key=lambda e: (e.date, -e.importance, e.category))

    def days(e: Event) -> int:
        return (_d(e.date) - today).days

    this_week = [e for e in events if days(e) <= window_days]
    upcoming = [e for e in events if window_days < days(e) <= horizon_days]

    outputs_dir.mkdir(parents=True, exist_ok=True)
    (outputs_dir / config.EVENTS_JSON.name).write_text(
        json.dumps({"generated": dt.datetime.now(dt.timezone.utc).isoformat(),
                    "today": today.isoformat(),
                    "events": [e.to_dict() for e in events]},
                   ensure_ascii=False, indent=2),
        encoding="utf-8")

    index = build_site(this_week, upcoming, site_dir or config.SITE_DIR, today)

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
    args = p.parse_args(argv)
    run(outputs_dir=args.outputs_dir, site_dir=args.site_dir,
        window_days=args.window_days, horizon_days=args.horizon_days,
        throttle=args.throttle, use_ipo=not args.no_ipo)


if __name__ == "__main__":
    main()
