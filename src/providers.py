"""Event providers. Each returns a list of unified Event objects.

  static_events()        -> macro_events.yaml + events.yaml (curated)
  deterministic_events() -> options expiry / quad-witching / index rebalance (math)
  earnings_events()      -> yfinance earnings -> Event (with estimates + last-qtr)
  ipo_events()           -> best-effort Nasdaq IPO calendar scrape
"""
from __future__ import annotations

import calendar
import datetime as dt

from src import config
from src.events_model import Event
from src.fetch_earnings import enrich_last_quarter_all, fetch_all

# --------------------------------------------------------------------------- #
# 1) Static curated events (macro / Fed / conferences / IPOs)
# --------------------------------------------------------------------------- #
def _iso(v) -> str:
    """YAML parses unquoted dates into date objects — normalise back to ISO str."""
    if isinstance(v, (dt.date, dt.datetime)):
        return v.isoformat()[:10]
    return str(v) if v is not None else ""


def static_events() -> list[Event]:
    out: list[Event] = []
    for path in (config.DATA_DIR / "macro_events.yaml",
                 config.DATA_DIR / "events.yaml"):
        data = config.load_yaml(path)
        for e in (data.get("events", []) if isinstance(data, dict) else []):
            if not e.get("date"):
                continue
            e = dict(e)
            e["date"] = _iso(e["date"])
            if e.get("end_date"):
                e["end_date"] = _iso(e["end_date"])
            out.append(Event.from_dict(e))
    return out


# --------------------------------------------------------------------------- #
# 2) Deterministic events (computable from the calendar)
# --------------------------------------------------------------------------- #
def _nth_weekday(year: int, month: int, weekday: int, n: int) -> dt.date:
    first = dt.date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + dt.timedelta(days=offset + 7 * (n - 1))


def _nth_friday(year: int, month: int, n: int) -> dt.date:
    return _nth_weekday(year, month, 4, n)      # 4 = Friday


def _last_weekday(year: int, month: int, weekday: int) -> dt.date:
    last_day = calendar.monthrange(year, month)[1]
    d = dt.date(year, month, last_day)
    return d - dt.timedelta(days=(d.weekday() - weekday) % 7)


def _easter(year: int) -> dt.date:
    """Gregorian Easter Sunday (anonymous computus)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    g = (8 * b + 13) // 25
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return dt.date(year, month, day + 1)


def _nyse_holidays(year: int) -> set[dt.date]:
    """Full NYSE holiday set for one year (observed dates)."""
    def observed(d: dt.date) -> dt.date:
        if d.weekday() == 5:                    # Sat -> Friday before
            return d - dt.timedelta(days=1)
        if d.weekday() == 6:                    # Sun -> Monday after
            return d + dt.timedelta(days=1)
        return d

    hol = {
        _nth_weekday(year, 1, 0, 3),            # MLK Day, 3rd Monday Jan
        _nth_weekday(year, 2, 0, 3),            # Washington's Birthday
        _easter(year) - dt.timedelta(days=2),   # Good Friday
        _last_weekday(year, 5, 0),              # Memorial Day, last Monday May
        observed(dt.date(year, 6, 19)),         # Juneteenth
        observed(dt.date(year, 7, 4)),          # Independence Day
        _nth_weekday(year, 9, 0, 1),            # Labor Day
        _nth_weekday(year, 11, 3, 4),           # Thanksgiving, 4th Thursday
        observed(dt.date(year, 12, 25)),        # Christmas
    }
    ny = dt.date(year, 1, 1)                    # New Year's: no observance if Sat
    if ny.weekday() == 6:
        hol.add(ny + dt.timedelta(days=1))
    elif ny.weekday() < 5:
        hol.add(ny)
    return hol


def _prev_trading_day(d: dt.date) -> dt.date:
    while d.weekday() >= 5 or d in _nyse_holidays(d.year):
        d -= dt.timedelta(days=1)
    return d


def _months_between(start: dt.date, end: dt.date):
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        yield y, m
        m = 1 if m == 12 else m + 1
        y = y + 1 if m == 1 else y


def deterministic_events(start: dt.date, end: dt.date) -> list[Event]:
    out: list[Event] = []
    for y, m in _months_between(start, end):
        fri3 = _nth_friday(y, m, 3)             # 3rd Friday
        opex = _prev_trading_day(fri3)          # holiday -> prior trading day
        shifted = f";注意:第三个周五 {fri3:%m/%d} 美股休市,到期结算提前至 {opex:%m/%d}" \
            if opex != fri3 else ""
        if start <= opex <= end:
            quad = m in (3, 6, 9, 12)
            out.append(Event(
                date=opex.isoformat(), category="options",
                title="期权四巫日 (季度衍生品到期)" if quad else "月度期权到期日 (OpEx)",
                importance=3 if quad else 1,
                watch=("股指期货/期权/个股期权同日到期结算,成交量巨大、尾盘波动诡异"
                       if quad else "月度期权到期,尾盘波动可能放大") + shifted,
                source_url="",
            ))
            if quad:
                out.append(Event(
                    date=opex.isoformat(), category="index",
                    title="标普 500 / 中小盘 季度再平衡生效",
                    importance=2,
                    watch="标普指数季度调整当日生效;新纳入个股被动买入需求,尾盘成交放大" + shifted,
                    source_url="https://www.spglobal.com/spdji/en/governance/methodology/",
                ))
        # Russell US reconstitution — semi-annual since 2026 (FTSE Russell notice
        # 2025-11-05): June = 4th Friday of June; December = 2nd Friday of December.
        rus_days = []
        if m == 6:
            rus_days.append((_prev_trading_day(_nth_friday(y, 6, 4)), "6月"))
        if m == 12 and y >= 2026:
            rus_days.append((_prev_trading_day(_nth_friday(y, 12, 2)), "12月"))
        for rus, label in rus_days:
            if start <= rus <= end:
                out.append(Event(
                    date=rus.isoformat(), category="index",
                    title=f"Russell 指数重构生效 ({label},半年度)",
                    importance=2,
                    watch="收盘后生效,大批被动资金当日尾盘强制调仓,小盘股异动多为对账而非基本面;"
                          "2026 起改为每年 6 月+12 月两次",
                    source_url="https://www.lseg.com/en/ftse-russell/russell-reconstitution",
                ))
    return out


# --------------------------------------------------------------------------- #
# 3) Earnings -> Event
# --------------------------------------------------------------------------- #
_WATCH_BY_SUBSECTOR = {
    "AI semiconductors": "AI 芯片收入、数据中心需求、custom ASIC、下季指引",
    "Semiconductor equipment": "设备订单、WFE 资本开支、对中国出口",
    "EDA / chip design": "设计活动、AI 芯片设计需求",
    "Networking / optical": "光通信/CPO、AI-era 带宽需求、网络设备订单",
    "Servers / power / cooling": "AI 服务器订单与收入、电力/散热需求",
    "Memory / storage (AI)": "HBM/存储价格与供给、AI 拉动",
    "Hyperscale / cloud": "云增速、AI capex 资本开支、AI 变现",
    "AI software / security / data": "AI 软件 ARR、净留存、平台扩张、估值",
    "AI cloud / GPU": "GPU 算力供给、客户集中度、合同与积压订单",
    "Power / nuclear (AI data centers)": "数据中心电力需求、PPA 长约",
    "Consumer / platform AI": "AI 功能落地、用户与变现",
}
_HIGH_SUBSECTORS = {
    "AI semiconductors", "Networking / optical", "Semiconductor equipment",
    "AI cloud / GPU", "Hyperscale / cloud",
}


def earnings_events(today: dt.date, horizon_days: int, throttle: float = 0.0) -> list[Event]:
    companies = config.load_companies()
    items = fetch_all(companies, throttle=throttle)
    # day 0 included: a US after-hours report is still upcoming on its own date
    in_window = [
        e for e in items
        if 0 <= (dt.date.fromisoformat(e.earnings_date) - today).days <= horizon_days
    ]
    enrich_last_quarter_all(in_window, throttle=throttle)
    events: list[Event] = []
    for e in in_window:
        events.append(Event(
            date=e.earnings_date,
            category="earnings",
            title=f"{e.name} 财报",
            importance=3 if e.subsector in _HIGH_SUBSECTORS else 2,
            tickers=[e.ticker],
            watch=_WATCH_BY_SUBSECTOR.get(e.subsector, "财报与下季指引"),
            source_url=f"https://finance.yahoo.com/quote/{e.ticker}",
            meta={
                "ticker": e.ticker, "subsector": e.subsector,
                "date_confirmed": e.date_confirmed,
                "eps_estimate": e.eps_estimate, "revenue_estimate": e.revenue_estimate,
                "revenue_currency": e.revenue_currency,
                "last_quarter": e.last_quarter,
                "last_eps_actual": e.last_eps_actual,
                "last_eps_estimate": e.last_eps_estimate,
            },
        ))
    return events


# --------------------------------------------------------------------------- #
# 4) IPO calendar (best-effort Nasdaq scrape; graceful on failure)
# --------------------------------------------------------------------------- #
_AI_IPO_KW = [
    "artificial intelligence", "quant", "semiconductor", "chip", "data center",
    "datacenter", "cloud", "cyber", "robot", "gpu", "neural", "autonomous",
    "comput", "machine learning", "intelligence", "fabless", "lidar", "photonic",
    "infrastructure",
]


def _is_ai_ipo(text: str) -> bool:
    """Keyword filter so the (unfiltered) IPO feed only surfaces AI-relevant names.
    Imperfect by design — favours precision; curate notable misses in events.yaml."""
    import re
    text = text.lower()
    if re.search(r"\bai\b", text) or "a.i." in text:
        return True
    return any(k in text for k in _AI_IPO_KW)


def ipo_events(today: dt.date, horizon_days: int) -> list[Event]:
    try:
        import requests
    except ImportError:
        return []
    end = today + dt.timedelta(days=horizon_days)
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
    }
    out: list[Event] = []
    seen: set[str] = set()
    for y, m in _months_between(today, end):
        url = f"https://api.nasdaq.com/api/ipo/calendar?date={y}-{m:02d}"
        try:
            resp = requests.get(url, headers=headers, timeout=20)
            resp.raise_for_status()
            data = resp.json().get("data", {}) or {}
        except Exception as exc:  # noqa: BLE001
            print(f"[ipo] {y}-{m:02d}: fetch failed ({exc})")
            continue
        rows = ((data.get("upcoming", {}) or {}).get("upcomingTable", {}) or {}).get("rows") or []
        rows += (data.get("priced", {}) or {}).get("rows") or []
        for r in rows:
            sym = (r.get("proposedTickerSymbol") or r.get("symbol") or "").strip()
            name = (r.get("companyName") or "").strip()
            raw_date = (r.get("expectedPriceDate") or r.get("pricedDate") or "").strip()
            d = _parse_us_date(raw_date)
            if not d or not (today <= d <= end):
                continue
            if not _is_ai_ipo(f"{name} {sym}"):
                continue  # skip non-AI IPOs (silver miners, insurers, etc.)
            key = f"{sym}-{d}"
            if key in seen:
                continue
            seen.add(key)
            label = f"{name} IPO" + (f" ({sym})" if sym else "")
            out.append(Event(
                date=d.isoformat(), category="ipo", title=label, importance=1,
                tickers=[sym] if sym else [],
                watch="新股定价/上市;留意是否 AI 相关及炒作风险",
                source_url="https://www.nasdaq.com/market-activity/ipos",
            ))
    print(f"[ipo] {len(out)} IPO(s) in window")
    return out


def _parse_us_date(s: str) -> dt.date | None:
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(s, fmt).date()
        except (ValueError, TypeError):
            continue
    return None
