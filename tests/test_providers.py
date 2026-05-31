"""Tests for the deterministic + static providers (no network)."""
import datetime as dt

from src.providers import deterministic_events, static_events


def test_quad_witching_june_2026():
    evs = deterministic_events(dt.date(2026, 6, 1), dt.date(2026, 6, 30))
    opts = [e for e in evs if e.category == "options"]
    # 3rd Friday of June 2026 = June 19; June is a quad-witching month -> high
    quad = [e for e in opts if e.date == "2026-06-19"]
    assert quad and quad[0].importance == 3


def test_russell_reconstitution_last_friday_june():
    evs = deterministic_events(dt.date(2026, 6, 1), dt.date(2026, 6, 30))
    idx = [e for e in evs if e.category == "index"]
    assert any(e.date == "2026-06-26" for e in idx)  # last Friday of June 2026


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
