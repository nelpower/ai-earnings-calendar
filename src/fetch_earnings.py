"""Fetch next earnings date + consensus estimates via yfinance (no API key).

For each ticker we read ``yf.Ticker(tk).calendar``, which yields:
  Earnings Date      -> list of date(s); 2 dates means an *estimated* range
  Earnings Average   -> consensus EPS estimate
  Revenue Average    -> consensus revenue estimate (plus High/Low)

Everything is best-effort and per-ticker isolated: a ticker that fails (Yahoo
rate-limit, missing data) is skipped with a warning, never crashing the run.
"""
from __future__ import annotations

import datetime as dt
import time
from dataclasses import dataclass, asdict


@dataclass
class Earnings:
    ticker: str
    name: str
    subsector: str
    earnings_date: str = ""        # ISO date (earliest if a range)
    date_confirmed: bool = False   # True if a single date, False if estimated range
    eps_estimate: float | None = None
    revenue_estimate: float | None = None
    eps_low: float | None = None
    eps_high: float | None = None
    revenue_low: float | None = None
    revenue_high: float | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def _as_float(v) -> float | None:
    try:
        if v is None:
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def fetch_one(company: dict) -> Earnings | None:
    """Fetch earnings info for one company dict {ticker,name,subsector}."""
    import yfinance as yf

    ticker = company["ticker"]
    try:
        cal = yf.Ticker(ticker).calendar or {}
    except Exception as exc:  # noqa: BLE001
        print(f"[earnings] {ticker}: fetch failed ({exc})")
        return None

    dates = cal.get("Earnings Date") or []
    if not isinstance(dates, (list, tuple)):
        dates = [dates]
    dates = [d for d in dates if isinstance(d, dt.date)]
    if not dates:
        return None  # no scheduled date

    return Earnings(
        ticker=ticker,
        name=company.get("name", ticker),
        subsector=company.get("subsector", ""),
        earnings_date=min(dates).isoformat(),
        date_confirmed=len(dates) == 1,
        eps_estimate=_as_float(cal.get("Earnings Average")),
        revenue_estimate=_as_float(cal.get("Revenue Average")),
        eps_low=_as_float(cal.get("Earnings Low")),
        eps_high=_as_float(cal.get("Earnings High")),
        revenue_low=_as_float(cal.get("Revenue Low")),
        revenue_high=_as_float(cal.get("Revenue High")),
    )


def fetch_all(companies: list[dict], throttle: float = 0.0) -> list[Earnings]:
    """Fetch all companies; skip failures. ``throttle`` adds a delay between
    requests to be gentle on Yahoo."""
    out: list[Earnings] = []
    total = len(companies)
    for i, company in enumerate(companies, 1):
        item = fetch_one(company)
        if item and item.earnings_date:
            out.append(item)
        if throttle and i < total:
            time.sleep(throttle)
    print(f"[earnings] fetched dates for {len(out)}/{total} companies")
    return out
