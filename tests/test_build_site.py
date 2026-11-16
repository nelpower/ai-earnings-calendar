"""Tests for the HTML renderer and the ICS feed (no network)."""
import datetime as dt

from src.build_ics import build_ics_text
from src.build_site import _fmt_eps, _fmt_rev, build_html
from src.events_model import Event

TODAY = dt.date(2026, 6, 12)


def test_fmt_rev_currency_aware():
    assert _fmt_rev(34.29e9) == "$34.29B"
    assert _fmt_rev(1261.64e9, "TWD") == "NT$1.26T"      # TSM: TWD, not "$"
    assert _fmt_rev(8.89e9, "EUR") == "€8.89B"
    assert _fmt_rev(5.0e9, "SEK") == "SEK 5.00B"          # unknown -> code prefix


def test_fmt_eps_non_usd_shows_bare_number():
    assert _fmt_eps(3.74) == "$3.74"
    assert _fmt_eps(-0.5) == "-$0.50"
    assert _fmt_eps(6.85, "EUR") == "6.85"   # don't assert a wrong currency


def test_card_no_dangling_et_and_no_empty_ticker_row():
    ev = Event(date="2026-06-18", category="options",
               title="期权四巫日", importance=3, watch="到期结算")
    html = build_html([ev], [], today=TODAY)
    assert "> ET<" not in html and " ET</span>" not in html
    # no tickers -> the ticker row div is omitted entirely
    assert html.count('class="tk"') == 0


def test_card_renders_date_range_and_time():
    ev = Event(date="2026-06-15", end_date="2026-06-18", time_et="13:00",
               category="conference", title="Summit", importance=2, watch="w")
    html = build_html([ev], [], today=TODAY)
    assert "06/15–06/18 · 13:00 ET" in html


def test_unconfirmed_earnings_date_shows_badge():
    ev = Event(date="2026-06-24", category="earnings", title="Micron 财报",
               importance=3, tickers=["MU"], watch="w",
               meta={"date_confirmed": False, "eps_estimate": 1.0,
                     "revenue_estimate": 1e9})
    html = build_html([ev], [], today=TODAY)
    assert "日期待确认" in html


def test_tiny_estimate_base_suppresses_percentage():
    ev = Event(date="2026-07-23", category="earnings", title="Intel 财报",
               importance=3, tickers=["INTC"], watch="w",
               meta={"eps_estimate": 0.21, "revenue_estimate": 14.4e9,
                     "last_eps_actual": 0.29, "last_eps_estimate": 0.01,
                     "last_quarter": "2026-03-31"})
    html = build_html([ev], [], today=TODAY)
    assert "超预期" in html and "2108" not in html


def test_tentative_policy_and_ipo_have_visible_status_and_calendar_status():
    events = [
        Event(date="2026-12-01", category="policy", title="闭门访谈（拟议）",
              meta={"date_confirmed": False, "date_label": "请求日期，待确认"}),
        Event(date="2026-11-19", category="ipo", title="预计上市",
              meta={"date_confirmed": False, "date_label": "预计日程"}),
    ]
    html = build_html(events, [], today=TODAY)
    assert '<span class="tbd">请求日期，待确认</span>' in html
    assert '<span class="tbd">预计日程</span>' in html
    ics = build_ics_text(events, TODAY).replace("\r\n ", "")
    assert ics.count("STATUS:TENTATIVE") == 2
    assert "【请求日期，待确认】" in ics and "【预计日程】" in ics


def test_header_and_filter_js():
    ev = Event(date="2026-06-18", category="options", title="t", watch="w")
    html = build_html([ev], [], today=TODAY)
    assert "北京时间" in html and "(UTC)" not in html
    assert "function flt(" in html and 'data-cat="options"' in html
    assert 'href="events.ics"' in html


def test_moved_request_is_visible_before_this_week_without_changing_ics_identity():
    ev = Event(date="2026-12-01", category="policy", title="黄仁勋国会闭门访谈（拟议）",
               meta={"date_confirmed": False, "original_proposed_date": "2026-11-18",
                     "display_title": "黄仁勋（Jensen Huang）国会闭门访谈（拟议）"})
    today = dt.date(2026, 11, 16)
    html = build_html([], [ev], today=today)
    assert html.index("重要日程更新") < html.index("本周大事")
    assert "原拟 11/18；最新请求日期 12/01，尚未确认" in html
    assert 'href="#day-2026-12-01"' in html and 'id="day-2026-12-01"' in html
    assert "Jensen Huang" in html
    canonical = Event(date=ev.date, category=ev.category, title=ev.title,
                      meta={"date_confirmed": False, "original_proposed_date": "2026-11-18"})
    assert build_ics_text([ev], today) == build_ics_text([canonical], today)
    assert "重要日程更新" not in build_html([], [ev], today=dt.date(2026, 11, 19))


def test_ics_timed_event_converts_et_to_utc():
    ev = Event(date="2026-06-25", time_et="08:30", category="macro",
               title="PCE 物价指数 (5月)", importance=2, watch="w")
    ics = build_ics_text([ev], TODAY)
    assert "DTSTART:20260625T123000Z" in ics    # 08:30 EDT = 12:30 UTC
    assert "BEGIN:VEVENT" in ics and "END:VCALENDAR" in ics


def test_ics_all_day_event_spans_range():
    ev = Event(date="2026-06-15", end_date="2026-06-18",
               category="conference", title="Databricks Summit", watch="w")
    ics = build_ics_text([ev], TODAY)
    assert "DTSTART;VALUE=DATE:20260615" in ics
    assert "DTEND;VALUE=DATE:20260619" in ics   # exclusive end

def test_ics_stable_across_same_day_runs():
    ev = Event(date="2026-06-25", time_et="08:30", category="macro",
               title="PCE", watch="w")
    assert build_ics_text([ev], TODAY) == build_ics_text([ev], TODAY)


def test_health_panel_distinguishes_failed_sources_and_unverified_news():
    health = {"company_count": 182, "issuer_count": 20, "official_source_count": 14,
              "sources": [{"source": "earnings", "partition": "AMD", "status": "failed", "last_success": "2026-11-15"}],
              "stale_events": 1}
    candidates = [{"title": "Future announcement", "url": "https://example.com/news",
                   "published_date": "2026-11-16", "review_status": "needs_review"}]
    html = build_html([], [], today=TODAY, health=health, candidates=candidates)
    assert "182 家上市公司" in html and "来源分区暂不可用" in html
    assert "公告发布日期不代表活动日期" in html and "Future announcement" in html
    assert 'href="source_status.json"' in html and 'href="candidates.json"' in html


def test_stale_event_does_not_appear_freshly_verified():
    ev = Event(date="2026-11-18", category="earnings", title="AMD 财报", tickers=["AMD"],
               meta={"source_stale": True, "last_verified": "2026-11-15"})
    html = build_html([ev], [], today=TODAY)
    assert "来源暂不可用 · 沿用 2026-11-15 记录" in html


def test_health_panel_displays_coverage_drop_and_seed_horizon_warnings():
    html = build_html([], [], today=TODAY, health={"warnings": [
        {"kind": "coverage_drop", "category": "earnings", "before": 30, "after": 0},
        {"kind": "macro_seed_expiring", "last_date": "2026-12-23"}]})
    assert "财报日程由 30 条降至 0 条" in html
    assert "宏观人工日程仅覆盖至 2026-12-23" in html

