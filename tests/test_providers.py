"""Tests for the deterministic + static providers (no network)."""
import datetime as dt

from src.providers import deterministic_events, static_events


def test_quad_witching_june_2026_shifts_for_juneteenth():
    evs = deterministic_events(dt.date(2026, 6, 1), dt.date(2026, 6, 30))
    opts = [e for e in evs if e.category == "options"]
    # 3rd Friday of June 2026 = June 19 = Juneteenth (NYSE closed)
    # -> expiry moves to Thursday June 18
    assert not any(e.date == "2026-06-19" for e in opts)
    quad = [e for e in opts if e.date == "2026-06-18"]
    assert quad and quad[0].importance == 3
    assert "休市" in quad[0].watch


def test_quad_witching_september_2026_normal_friday():
    evs = deterministic_events(dt.date(2026, 9, 1), dt.date(2026, 9, 30))
    opts = [e for e in evs if e.category == "options"]
    assert any(e.date == "2026-09-18" for e in opts)   # no holiday, 3rd Friday


def test_opex_good_friday_2025_shifts_to_thursday():
    # April 2025: 3rd Friday Apr 18 was Good Friday -> expiry on Apr 17
    evs = deterministic_events(dt.date(2025, 4, 1), dt.date(2025, 4, 30))
    opts = [e for e in evs if e.category == "options"]
    assert any(e.date == "2025-04-17" for e in opts)
    assert not any(e.date == "2025-04-18" for e in opts)


def test_russell_reconstitution_june_and_december_2026():
    evs = deterministic_events(dt.date(2026, 1, 1), dt.date(2026, 12, 31))
    idx = [e for e in evs if e.category == "index" and "Russell" in e.title]
    # June: 4th Friday = June 26; December (semi-annual since 2026):
    # 2nd Friday = Dec 11 (FTSE Russell notice 2025-11-05)
    assert any(e.date == "2026-06-26" for e in idx)
    assert any(e.date == "2026-12-11" for e in idx)


def test_russell_no_december_recon_before_2026():
    evs = deterministic_events(dt.date(2025, 12, 1), dt.date(2025, 12, 31))
    assert not any(e.category == "index" and "Russell" in e.title for e in evs)


def test_monthly_opex_is_low_importance():
    # July 2026: 3rd Friday = July 17, NOT a quad-witching month -> importance 1
    evs = deterministic_events(dt.date(2026, 7, 1), dt.date(2026, 7, 31))
    jul = [e for e in evs if e.category == "options" and e.date == "2026-07-17"]
    assert jul and jul[0].importance == 1


def test_ai_ipo_keyword_filter():
    from src.providers import _is_ai_ipo
    assert _is_ai_ipo("Quantinuum Inc. QNT")          # quantum
    assert _is_ai_ipo("Foobar AI Inc. FBAI")          # \bai\b
    assert _is_ai_ipo("Acme Semiconductor Corp")      # semiconductor
    assert not _is_ai_ipo("Sunshine Silver Mining Co")
    assert not _is_ai_ipo("Safepoint Holdings, Inc.")  # insurer


def test_static_events_load_macro_and_conferences():
    evs = static_events()
    assert any(e.category == "fed" for e in evs)             # FOMC
    assert any("CPI" in e.title for e in evs)                # macro
    assert any(e.category == "conference" for e in evs)      # WWDC/GTC/Computex
    # every static event has a date and a watch note
    assert all(e.date and e.watch for e in evs)


def test_fomc_dates_are_decision_days():
    """FOMC entries carry the decision/statement day (2nd meeting day),
    verified 2026-06-12 against federalreserve.gov."""
    evs = static_events()
    fomc = {e.date for e in evs if e.category == "fed" and "FOMC" in e.title}
    assert {"2026-06-17", "2026-07-29", "2026-09-16",
            "2026-10-28", "2026-12-09"} <= fomc


def test_earnings_events_includes_day_zero(monkeypatch):
    """An earnings report dated 'today' is still upcoming (US AMC happens
    overnight Beijing time) and must not vanish from the calendar."""
    from src import providers
    from src.fetch_earnings import Earnings

    today = dt.date(2026, 6, 12)
    items = [Earnings(ticker="NVDA", name="NVIDIA", subsector="AI semiconductors",
                      earnings_date="2026-06-12"),
             Earnings(ticker="OLD", name="Old", subsector="x",
                      earnings_date="2026-06-11")]
    monkeypatch.setattr(providers, "fetch_all", lambda companies, throttle=0.0: items)
    monkeypatch.setattr(providers, "enrich_last_quarter_all",
                        lambda x, throttle=0.0: x)
    evs = providers.earnings_events(today, 45)
    dates = {e.date for e in evs}
    assert "2026-06-12" in dates       # day 0 kept
    assert "2026-06-11" not in dates   # yesterday dropped
