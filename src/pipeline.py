"""AI earnings calendar pipeline: universe -> yfinance -> filter -> JSON + site.

Run from the project root:
    python -m src.pipeline                 # fetch + build site/
    python -m src.pipeline --throttle 0.3  # be gentle on Yahoo (delay/req)
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path

from src import config
from src.build_site import build_site
from src.fetch_earnings import Earnings, enrich_last_quarter_all, fetch_all


def _days_out(e: Earnings, today: dt.date) -> int:
    return (dt.date.fromisoformat(e.earnings_date) - today).days


def _load_last_good(outputs_dir: Path) -> list[Earnings]:
    """Reload the previously committed dataset (used if a fetch returns nothing,
    e.g. Yahoo rate-limited the CI runner) so we never publish an empty page."""
    path = outputs_dir / "earnings.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return [Earnings(**d) for d in data.get("all", [])]
    except Exception:  # noqa: BLE001
        return []


def run(
    companies_path: Path = config.AI_COMPANIES_PATH,
    outputs_dir: Path = config.OUTPUTS_DIR,
    site_dir: Path | None = None,
    window_days: int = config.WINDOW_DAYS,
    preview_days: int = config.PREVIEW_DAYS,
    throttle: float = 0.0,
    today: dt.date | None = None,
) -> tuple[list[Earnings], list[Earnings]]:
    today = today or dt.date.today()
    companies = config.load_companies(companies_path)
    print(f"[pipeline] AI universe: {len(companies)} companies")

    items = fetch_all(companies, throttle=throttle)
    fetched_ok = len(items) > 0
    if not fetched_ok:
        print("[pipeline] WARNING: fetched 0 companies (Yahoo blocked?); "
              "falling back to last committed data.")
        items = _load_last_good(outputs_dir)

    # Only FUTURE dates (>= 1 day out). A company that just reported briefly
    # keeps a stale same-day/past date in yfinance's calendar, so excluding
    # day 0 and past avoids showing already-reported names as "upcoming".
    this_week = [e for e in items if 1 <= _days_out(e, today) <= window_days]
    upcoming = [e for e in items
                if window_days < _days_out(e, today) <= preview_days]
    this_week.sort(key=lambda e: (e.earnings_date, e.name))
    upcoming.sort(key=lambda e: (e.earnings_date, e.name))

    # Add last-quarter actual-vs-estimate, but ONLY for the few in-window
    # companies (keeps the run fast — no extra calls for the other ~100).
    if fetched_ok:
        enrich_last_quarter_all(this_week + upcoming, throttle=throttle)

    # full raw output (committed for the record) — only overwrite on a real fetch
    if fetched_ok:
        outputs_dir.mkdir(parents=True, exist_ok=True)
        (outputs_dir / "earnings.json").write_text(
            json.dumps(
                {"generated": dt.datetime.now(dt.timezone.utc).isoformat(),
                 "today": today.isoformat(),
                 "all": [e.to_dict() for e in sorted(items, key=lambda x: x.earnings_date)]},
                ensure_ascii=False, indent=2),
            encoding="utf-8")

    index = build_site(this_week, upcoming, site_dir or config.SITE_DIR, today)

    print(f"[pipeline] this week (<= {window_days}d): {len(this_week)} | "
          f"upcoming ({window_days+1}-{preview_days}d): {len(upcoming)}")
    if this_week:
        print("[pipeline] this week: " +
              ", ".join(f"{e.ticker}({e.earnings_date})" for e in this_week))
    print(f"[pipeline] wrote {outputs_dir / 'earnings.json'} and {index}")
    return this_week, upcoming


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description="AI earnings calendar pipeline")
    p.add_argument("--companies", type=Path, default=config.AI_COMPANIES_PATH)
    p.add_argument("--outputs-dir", type=Path, default=config.OUTPUTS_DIR)
    p.add_argument("--site-dir", type=Path, default=config.SITE_DIR)
    p.add_argument("--window-days", type=int, default=config.WINDOW_DAYS)
    p.add_argument("--preview-days", type=int, default=config.PREVIEW_DAYS)
    p.add_argument("--throttle", type=float, default=0.0,
                   help="seconds to sleep between ticker requests")
    args = p.parse_args(argv)
    run(companies_path=args.companies, outputs_dir=args.outputs_dir,
        site_dir=args.site_dir, window_days=args.window_days,
        preview_days=args.preview_days, throttle=args.throttle)


if __name__ == "__main__":
    main()
