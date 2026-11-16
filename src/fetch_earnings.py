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
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, asdict
from types import ModuleType

from src.source_health import record_status

_YF_CONFIG_LOCK = threading.RLock()


@contextmanager
def _strict_calendar_errors(yf):
    """Surface HTTP failures that yfinance otherwise converts to an empty dict.

    yfinance 1.8 uses ``config.debug.hide_exceptions``; older configurations may
    expose a boolean ``raise_errors`` instead. Probe existing controls rather
    than inventing attributes on its dynamic config object, then restore every
    setting even if ticker construction or the calendar request raises.
    """
    with _YF_CONFIG_LOCK:
        config = getattr(yf, "config", None)
        if isinstance(config, ModuleType):
            config = getattr(config, "YfConfig", config)
        debug = getattr(config, "debug", None)
        owners = [debug]
        # Do not getattr arbitrary keys on ConfigMgr: it creates new sections.
        if "raise_errors" in getattr(config, "__dict__", {}):
            owners.append(config)
        changes = []
        for owner in owners:
            for field, strict_value in (("hide_exceptions", False), ("raise_errors", True)):
                original = getattr(owner, field, None)
                if isinstance(original, bool):
                    changes.append((owner, field, original, strict_value))
        if not changes:
            raise RuntimeError("Unsupported yfinance exception policy; calendar emptiness cannot be trusted")
        applied = []
        try:
            for owner, field, original, strict_value in changes:
                setattr(owner, field, strict_value)
                applied.append((owner, field, original))
            yield
        finally:
            for owner, field, original in reversed(applied):
                setattr(owner, field, original)


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
    # Yahoo revenue estimates come in the company's REPORTING currency (e.g.
    # TWD for TSM, EUR for ASML), not USD — filled during enrichment.
    revenue_currency: str = ""
    # Most recent REPORTED quarter (actual vs estimate) — filled on demand.
    last_quarter: str = ""
    last_eps_actual: float | None = None
    last_eps_estimate: float | None = None

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
    ticker = str(company["ticker"]).upper()
    try:
        import yfinance as yf
        with _strict_calendar_errors(yf):
            cal = yf.Ticker(ticker).calendar
    except Exception as exc:  # noqa: BLE001
        print(f"[earnings] {ticker}: fetch failed ({exc})")
        record_status("earnings", ticker, "failed", detail=str(exc))
        return None

    if cal is None:
        cal = {}
    if not isinstance(cal, dict):
        record_status("earnings", ticker, "invalid_schema", fetched=1,
                      detail="calendar must be an object", rejected=1)
        return None
    dates = cal.get("Earnings Date")
    if dates is None:
        dates = []
    if not isinstance(dates, (list, tuple)):
        dates = [dates]
    if any(not isinstance(d, dt.date) for d in dates):
        record_status("earnings", ticker, "invalid_schema", fetched=1,
                      detail="Earnings Date must contain date objects", rejected=1)
        return None
    dates = [d.date() if isinstance(d, dt.datetime) else d for d in dates]
    if not dates:
        record_status("earnings", ticker, "empty", fetched=1,
                      detail="No scheduled earnings date")
        return None  # no scheduled date

    record_status("earnings", ticker, "ok", fetched=1, accepted=1)
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


def enrich_last_quarter(item: Earnings) -> Earnings:
    """Add the most recent reported quarter's actual vs estimate EPS, plus the
    reporting currency of the revenue estimate.

    Uses yfinance ``earnings_history`` / ``info`` (API-based, no HTML scrape).
    Best-effort: failures leave the fields empty.
    """
    try:
        import yfinance as yf
        t = yf.Ticker(item.ticker)
    except Exception as exc:  # noqa: BLE001
        print(f"[earnings] {item.ticker}: enrichment unavailable ({exc})")
        return item
    try:
        eh = t.earnings_history
        if eh is not None and len(eh) > 0:
            eh = eh.sort_index()
            row = eh.iloc[-1]
            item.last_quarter = str(eh.index[-1])[:10]
            item.last_eps_actual = _as_float(row.get("epsActual"))
            item.last_eps_estimate = _as_float(row.get("epsEstimate"))
    except Exception as exc:  # noqa: BLE001
        print(f"[earnings] {item.ticker}: last-quarter lookup failed ({exc})")
    try:
        cur = (t.info or {}).get("financialCurrency")
        if cur:
            item.revenue_currency = str(cur).upper()
    except Exception:  # noqa: BLE001
        pass  # currency stays unknown; site falls back to no symbol
    return item


def enrich_last_quarter_all(items: list[Earnings], throttle: float = 0.0) -> list[Earnings]:
    """Enrich a (small) list of in-window items with last-quarter actuals."""
    for i, item in enumerate(items):
        enrich_last_quarter(item)
        if throttle and i < len(items) - 1:
            time.sleep(throttle)
    return items


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

