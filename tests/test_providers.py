"""Tests for the deterministic + static providers (no network)."""
import datetime as dt

import pytest

from src.providers import deterministic_events, static_events
from src.source_health import reports, reset_health


@pytest.fixture(autouse=True)
def _fresh_source_health():
    reset_health(dt.date(2026, 11, 16))


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


def test_ai_ipo_recognizes_model_developers_without_industry_keywords():
    from src.providers import _is_ai_ipo
    for name in ("Anthropic, PBC ANT", "ANTHROPIC PBC", "OpenAI Group PBC",
                 "xAI Corp."):
        assert _is_ai_ipo(name), name
    for name in ("NotAnthropic Holdings", "OpenAir Aviation", "Xaim Insurance",
                 "ANT Holdings"):
        assert not _is_ai_ipo(name), name


def test_ipo_events_preserves_anthropic_and_labels_pricing_dates(monkeypatch):
    import requests
    from src.providers import ipo_events

    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"data": {
                "upcoming": {"upcomingTable": {"rows": [
                    {"proposedTickerSymbol": "ANT", "companyName": "Anthropic, PBC",
                     "expectedPriceDate": "11/18/2026"},
                    {"proposedTickerSymbol": "SLVR", "companyName": "Silver Mines Ltd.",
                     "expectedPriceDate": "11/18/2026"},
                ]}},
                "priced": {"rows": [
                    {"proposedTickerSymbol": "OAI", "companyName": "OpenAI Group PBC",
                     "pricedDate": "11/17/2026", "expectedPriceDate": "11/16/2026"},
                ]},
            }}

    monkeypatch.setattr(requests, "get", lambda *a, **k: FakeResp())
    evs = ipo_events(dt.date(2026, 11, 16), 7)
    by_symbol = {e.tickers[0]: e for e in evs}
    assert set(by_symbol) == {"ANT", "OAI"}
    expected = by_symbol["ANT"]
    assert expected.date == "2026-11-18" and "预计定价" in expected.title
    assert expected.meta["event_type"] == "pricing"
    assert expected.meta["date_confirmed"] is False
    assert expected.meta["source"] == "nasdaq_ipo"
    assert expected.meta["source_partition"] == "2026-11"
    assert expected.meta["last_verified"] == "2026-11-16"
    priced = by_symbol["OAI"]
    assert priced.date == "2026-11-17" and "预计" not in priced.title
    assert priced.meta["event_type"] == "pricing"
    assert priced.meta["date_confirmed"] is True
    report = reports()[0]
    assert report["status"] == "ok" and report["fetched"] == 3
    assert report["accepted"] == 2 and report["rejected"] == 1


def test_ipo_events_prefers_priced_record_over_same_day_estimate(monkeypatch):
    import requests
    from src.providers import ipo_events

    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            row = {"proposedTickerSymbol": "ANT", "companyName": "Anthropic, PBC"}
            return {"data": {
                "upcoming": {"upcomingTable": {"rows": [
                    {**row, "expectedPriceDate": "11/18/2026"},
                ]}},
                "priced": {"rows": [{**row, "pricedDate": "11/18/2026"}]},
            }}

    monkeypatch.setattr(requests, "get", lambda *a, **k: FakeResp())
    evs = ipo_events(dt.date(2026, 11, 16), 7)
    assert len(evs) == 1 and evs[0].meta["date_confirmed"] is True


def test_ipo_filter_consumes_watchlist_names_and_aliases_with_token_boundaries(monkeypatch):
    from src import providers
    monkeypatch.setattr(providers.config, "load_yaml", lambda path: {"issuers": [
        {"name": "Example Frontier Labs", "aliases": ["ExampleFrontier", "EFL PBC"]},
    ]})
    assert providers._is_ai_ipo("Example Frontier Labs, Inc. EFL")
    assert providers._is_ai_ipo("EXAMPLEFRONTIER PBC")
    assert providers._is_ai_ipo("EFL-PBC")
    assert not providers._is_ai_ipo("NotExampleFrontier Holdings")
    assert providers._is_ai_ipo("Anthropic PBC")  # fallback names remain available


@pytest.mark.parametrize("payload", [
    {"data": None}, {"data": {}}, {"data": {"priced": None}},
    {"data": {"priced": {"rows": None}}},
    {"data": {"upcoming": {"rows": []}}},
    {"data": {"priced": {"rows": ["bad row"]}}},
    {"data": {"priced": {"rows": [{"companyName": 42}]}}},
    {"data": {"priced": {"rows": []}, "upcoming": None}},
])
def test_ipo_malformed_success_response_is_not_a_valid_empty_month(monkeypatch, payload):
    import requests
    from src.providers import ipo_events

    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return payload

    monkeypatch.setattr(requests, "get", lambda *a, **k: FakeResp())
    assert ipo_events(dt.date(2026, 11, 16), 7) == []
    assert reports()[0]["status"] == "invalid_schema"
    assert reports()[0]["last_success"] == ""


def test_ipo_explicit_empty_rows_are_a_successful_empty_month(monkeypatch):
    import requests
    from src.providers import ipo_events

    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"data": {"priced": {"rows": []},
                             "upcoming": {"upcomingTable": {"rows": []}}}}

    monkeypatch.setattr(requests, "get", lambda *a, **k: FakeResp())
    assert ipo_events(dt.date(2026, 11, 16), 7) == []
    assert reports()[0]["status"] == "empty"
    assert reports()[0]["last_success"] == "2026-11-16"


def test_valid_non_ai_rows_report_filter_rejections_without_source_failure(monkeypatch):
    import requests
    from src.providers import ipo_events

    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"data": {"upcoming": {"upcomingTable": {"rows": [
                {"companyName": "Silver Mines Ltd.", "proposedTickerSymbol": "SLVR",
                 "expectedPriceDate": "11/18/2026"},
            ]}}}}

    monkeypatch.setattr(requests, "get", lambda *a, **k: FakeResp())
    assert ipo_events(dt.date(2026, 11, 16), 7) == []
    report = reports()[0]
    assert report["status"] == "ok" and report["fetched"] == 1
    assert report["accepted"] == 0 and report["rejected"] == 1


def test_ipo_reports_failed_and_successful_months_independently(monkeypatch):
    import requests
    from src.providers import ipo_events

    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"data": {"upcoming": {"upcomingTable": {"rows": [
                {"companyName": "Anthropic, PBC", "proposedTickerSymbol": "ANT",
                 "expectedPriceDate": "12/02/2026"},
            ]}}}}

    def get(url, **kwargs):
        if "2026-11" in url:
            raise requests.Timeout("timed out")
        return FakeResp()

    monkeypatch.setattr(requests, "get", get)
    events = ipo_events(dt.date(2026, 11, 16), 30)
    assert len(events) == 1 and events[0].meta["source_partition"] == "2026-12"
    by_month = {report["partition"]: report for report in reports()}
    assert by_month["2026-11"]["status"] == "failed"
    assert by_month["2026-12"]["status"] == "ok"
    assert by_month["2026-12"]["accepted"] == 1


def test_ipo_invalid_json_is_reported_as_invalid_schema(monkeypatch):
    import requests
    from src.providers import ipo_events

    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            raise ValueError("not JSON")

    monkeypatch.setattr(requests, "get", lambda *a, **k: FakeResp())
    assert ipo_events(dt.date(2026, 11, 16), 7) == []
    assert reports()[0]["status"] == "invalid_schema"


def test_static_events_load_macro_and_conferences():
    evs = static_events()
    assert any(e.category == "fed" for e in evs)             # FOMC
    assert any("CPI" in e.title for e in evs)                # macro
    assert any(e.category == "conference" for e in evs)      # WWDC/GTC/Computex
    # every static event has a date and a watch note
    assert all(e.date and e.watch for e in evs)


def test_static_events_inherit_original_verified_date_without_inventing_new_checks(monkeypatch, tmp_path):
    from src import providers
    (tmp_path / "macro_events.yaml").write_text(
        'verified_on: 2026-06-12\nevents:\n'
        '  - {date: 2026-11-18, category: macro, title: Old schedule}\n'
        '  - date: 2026-11-19\n    category: macro\n    title: Individually checked\n'
        '    meta: {verified_on: "2026-11-15"}\n', encoding="utf-8")
    (tmp_path / "events.yaml").write_text(
        'events:\n  - {date: 2026-11-20, category: conference, title: Unchecked}\n',
        encoding="utf-8")
    monkeypatch.setattr(providers.config, "DATA_DIR", tmp_path)
    events = {event.title: event for event in providers.static_events()}
    assert events["Old schedule"].meta["verified_on"] == "2026-06-12"
    assert events["Individually checked"].meta["verified_on"] == "2026-11-15"
    assert "verified_on" not in events["Unchecked"].meta


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
    assert evs[0].meta["source"] == "earnings"
    by_ticker = {report["partition"]: report for report in reports()}
    assert by_ticker["NVDA"]["accepted"] == 1
    assert by_ticker["OLD"]["accepted"] == 0
    assert by_ticker["OLD"]["status"] == "ok"  # valid date, outside this window


# --------------------------------------------------------------------------- #
# Industry monthly data (deterministic)
# --------------------------------------------------------------------------- #
def test_industry_tsmc_and_korea_in_window():
    from src.providers import industry_events
    evs = industry_events(dt.date(2026, 8, 19), dt.date(2026, 10, 3))
    tsmc = [e for e in evs if "台积电" in e.title]
    kor = [e for e in evs if "韩国" in e.title]
    # TSMC: Sep 10 (reports 8月) in window; Aug 10 already past window start
    assert [e.date for e in tsmc] == ["2026-09-10"]
    assert "8月" in tsmc[0].title and tsmc[0].importance == 3
    assert tsmc[0].category == "industry" and tsmc[0].tickers == ["TSM"]
    # Korea: Sep 1 (reports 8月) and Oct 1 (reports 9月)
    assert [e.date for e in kor] == ["2026-09-01", "2026-10-01"]
    assert "8月" in kor[0].title and "9月" in kor[1].title


def test_industry_january_reports_december():
    from src.providers import industry_events
    evs = industry_events(dt.date(2027, 1, 1), dt.date(2027, 1, 15))
    tsmc = [e for e in evs if "台积电" in e.title]
    assert tsmc and tsmc[0].date == "2027-01-10" and "12月" in tsmc[0].title


# --------------------------------------------------------------------------- #
# Lockup expiries (Nasdaq priced history, mocked)
# --------------------------------------------------------------------------- #
def test_lockup_events_from_priced_history(monkeypatch):
    import requests
    from src.providers import lockup_events

    today = dt.date(2026, 8, 19)
    ai_priced = (today - dt.timedelta(days=170)).strftime("%m/%d/%Y")   # lockup in 10d
    old_priced = (today - dt.timedelta(days=300)).strftime("%m/%d/%Y")  # out of range

    class FakeResp:
        status_code = 200
        def raise_for_status(self):
            pass
        def json(self):
            return {"data": {"priced": {"rows": [
                {"proposedTickerSymbol": "AICH", "companyName": "NeuralChip AI Inc.",
                 "pricedDate": ai_priced},
                {"proposedTickerSymbol": "SLVR", "companyName": "Silver Mines Ltd.",
                 "pricedDate": ai_priced},                     # non-AI -> filtered
                {"proposedTickerSymbol": "OLDA", "companyName": "Old AI Corp.",
                 "pricedDate": old_priced},                    # priced too早 -> out
            ]}}}

    monkeypatch.setattr(requests, "get", lambda *a, **k: FakeResp())
    evs = lockup_events(today, 45)
    assert len(evs) == 1
    e = evs[0]
    assert e.category == "ipo" and "解禁" in e.title and "AICH" in e.title
    assert e.date == (today + dt.timedelta(days=10)).isoformat()
    assert e.meta["assumed_lockup_days"] == 180
    assert e.importance == 2
    assert e.meta["source"] == "nasdaq_lockup"
    assert e.meta["source_partition"] == ai_priced[-4:] + "-" + ai_priced[:2]
    assert any(r["accepted"] == 1 for r in reports())


def test_lockup_requires_a_priced_table_and_does_not_reuse_upcoming(monkeypatch):
    import requests
    from src.providers import lockup_events

    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"data": {"upcoming": {"upcomingTable": {"rows": []}}}}

    monkeypatch.setattr(requests, "get", lambda *a, **k: FakeResp())
    assert lockup_events(dt.date(2026, 11, 16), 7) == []
    assert reports()[0]["source"] == "nasdaq_lockup"
    assert reports()[0]["status"] == "invalid_schema"


def test_lockup_network_failure_reports_the_original_ipo_month(monkeypatch):
    import requests
    from src.providers import LOCKUP_DAYS, lockup_events

    def get(*a, **k):
        raise requests.ConnectionError("unavailable")

    monkeypatch.setattr(requests, "get", get)
    today = dt.date(2026, 11, 16)
    assert lockup_events(today, 0) == []
    report = reports()[0]
    assert report["source"] == "nasdaq_lockup" and report["status"] == "failed"
    assert report["partition"] == (today - dt.timedelta(days=LOCKUP_DAYS)).strftime("%Y-%m")

