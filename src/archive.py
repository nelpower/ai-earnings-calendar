"""Outcome archive — append expired events to ``outputs/archive.jsonl``.

Rationale (borrowed from a good line in someone else's checklist): "archive past
catalysts with the actual outcome — builds pattern recognition over time". This
module gives that idea an actual mechanism:

- Each pipeline run, events from the *previous* committed ``events.json`` whose
  date has passed (date < today) are appended to an append-only JSONL file,
  which the daily workflow commits alongside ``events.json``.
- Earnings events get best-effort outcomes: actual vs estimated EPS (same
  yfinance ``earnings_history`` API as fetch_earnings) plus closes around the
  report. Because we usually don't know BMO/AMC, we store prev/on/next closes
  and BOTH candidate reaction numbers — the analysis layer decides later.
- Failures never block: an event with ``outcome: null`` still gets archived.
"""
from __future__ import annotations

import datetime as dt
import json
import time
from pathlib import Path

from src import config

ARCHIVE_NAME = "archive.jsonl"


def _as_float(v):
    try:
        f = float(v)
        return f if f == f else None    # NaN guard
    except (TypeError, ValueError):
        return None


def _earnings_outcome(ticker: str, event_date: dt.date) -> dict | None:
    """Best-effort: actual-vs-estimate EPS + closes around the report date.
    Returns whatever it managed to fetch; None only if nothing at all."""
    out: dict = {}
    try:
        import yfinance as yf
    except ImportError:
        return None
    t = yf.Ticker(ticker)
    try:
        eh = t.earnings_history
        if eh is not None and len(eh) > 0:
            eh = eh.sort_index()
            for idx, row in eh.iterrows():
                d = dt.date.fromisoformat(str(idx)[:10])
                if abs((d - event_date).days) <= 3:
                    out["eps_actual"] = _as_float(row.get("epsActual"))
                    out["eps_estimate"] = _as_float(row.get("epsEstimate"))
                    out["reported_date"] = str(idx)[:10]
                    break
    except Exception as exc:  # noqa: BLE001
        print(f"[archive] {ticker}: eps lookup failed ({exc})")
    try:
        hist = t.history(start=(event_date - dt.timedelta(days=7)).isoformat(),
                         end=(event_date + dt.timedelta(days=6)).isoformat())
        closes = {}
        for i, c in hist["Close"].items():
            f = _as_float(c)
            if f is not None:
                closes[dt.date.fromisoformat(str(i)[:10])] = round(f, 4)
        before = [d for d in closes if d < event_date]
        after = [d for d in closes if d > event_date]
        pt = {}
        if before:
            b = max(before)
            pt["prev"] = {"date": b.isoformat(), "close": closes[b]}
        if event_date in closes:
            pt["on"] = {"date": event_date.isoformat(), "close": closes[event_date]}
        if after:
            a = min(after)
            pt["next"] = {"date": a.isoformat(), "close": closes[a]}
        if pt:
            out["closes"] = pt
            # 盘后(AMC)财报的反应 = 当日收盘 -> 次日收盘;盘前(BMO) = 前日 -> 当日。
            # 事件通常不带盘前盘后信息,两个口径都存,分析时再选。
            if "on" in pt and "next" in pt:
                out["reaction_pct_if_amc"] = round(
                    (pt["next"]["close"] / pt["on"]["close"] - 1) * 100, 3)
            if "prev" in pt and "on" in pt:
                out["reaction_pct_if_bmo"] = round(
                    (pt["on"]["close"] / pt["prev"]["close"] - 1) * 100, 3)
    except Exception as exc:  # noqa: BLE001
        print(f"[archive] {ticker}: price reaction failed ({exc})")
    return out or None


def _archived_keys(path: Path) -> set[str]:
    keys: set[str] = set()
    if not path.exists():
        return keys
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            ev = json.loads(line).get("event", {})
            keys.add(f"{ev.get('date')}|{ev.get('title')}")
        except Exception:  # noqa: BLE001
            continue
    return keys


def archive_expired(today: dt.date,
                    outputs_dir: Path = config.OUTPUTS_DIR,
                    throttle: float = 0.0,
                    fetch_outcomes: bool = True) -> int:
    """Archive events from the previous ``events.json`` whose date has passed.
    Must run BEFORE the pipeline overwrites ``events.json``. Returns the number
    of newly archived events (already-archived date|title pairs are skipped)."""
    events_path = outputs_dir / config.EVENTS_JSON.name
    if not events_path.exists():
        return 0
    try:
        prev = json.loads(events_path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return 0
    cutoff = today.isoformat()
    expired = [e for e in prev.get("events", [])
               if e.get("date") and e["date"] < cutoff]
    if not expired:
        return 0

    arch_path = outputs_dir / ARCHIVE_NAME
    seen = _archived_keys(arch_path)
    todo = [e for e in expired if f"{e['date']}|{e.get('title')}" not in seen]

    lines = []
    for i, e in enumerate(todo):
        outcome = None
        ticker = (e.get("meta") or {}).get("ticker")
        if fetch_outcomes and e.get("category") == "earnings" and ticker:
            outcome = _earnings_outcome(ticker, dt.date.fromisoformat(e["date"]))
            if throttle and i < len(todo) - 1:
                time.sleep(throttle)
        lines.append(json.dumps(
            {"archived_at": today.isoformat(), "event": e, "outcome": outcome},
            ensure_ascii=False))
    if lines:
        with arch_path.open("a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    print(f"[archive] {len(lines)} event(s) archived -> {arch_path.name}")
    return len(lines)
