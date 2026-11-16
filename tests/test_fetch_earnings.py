"""Tests for the yfinance parsing layer (network mocked)."""
import datetime as dt
from types import SimpleNamespace

import pytest
import yfinance

from src import fetch_earnings as fe
from src.source_health import reports, reset_health


@pytest.fixture(autouse=True)
def _fresh_source_health():
    reset_health(dt.date(2026, 11, 16))


class _FakeTicker:
    """Stand-in for yfinance.Ticker returning a canned .calendar."""
    _DATA = {
        "NVDA": {"Earnings Date": [dt.date(2026, 6, 3)],
                 "Earnings Average": 2.5, "Revenue Average": 4.0e10,
                 "Revenue Low": 3.8e10, "Revenue High": 4.2e10},
        "RANGE": {"Earnings Date": [dt.date(2026, 6, 10), dt.date(2026, 6, 14)],
                  "Earnings Average": 0.5, "Revenue Average": 1.0e9},
        "NODATE": {"Earnings Average": 1.0},   # no Earnings Date
        "EMPTY": {},
    }

    def __init__(self, ticker):
        self.ticker = ticker

    @property
    def calendar(self):
        return self._DATA.get(self.ticker, {})


def _patch(monkeypatch):
    monkeypatch.setattr(yfinance, "Ticker", _FakeTicker)


def test_fetch_one_single_date_confirmed(monkeypatch):
    _patch(monkeypatch)
    e = fe.fetch_one({"ticker": "NVDA", "name": "NVIDIA", "subsector": "AI semiconductors"})
    assert e is not None
    assert e.earnings_date == "2026-06-03"
    assert e.date_confirmed is True
    assert e.eps_estimate == 2.5
    assert e.revenue_estimate == 4.0e10


def test_fetch_one_date_range_is_unconfirmed(monkeypatch):
    _patch(monkeypatch)
    e = fe.fetch_one({"ticker": "RANGE", "name": "R", "subsector": "x"})
    assert e.earnings_date == "2026-06-10"   # earliest of the range
    assert e.date_confirmed is False


def test_fetch_one_missing_date_returns_none(monkeypatch):
    _patch(monkeypatch)
    assert fe.fetch_one({"ticker": "NODATE", "name": "n", "subsector": "x"}) is None
    assert fe.fetch_one({"ticker": "EMPTY", "name": "n", "subsector": "x"}) is None
    assert all(r["status"] == "empty" for r in reports())
    assert all(r["last_success"] == "2026-11-16" for r in reports())


def test_fetch_one_records_exception_as_failed_not_empty(monkeypatch):
    class FailingTicker:
        def __init__(self, ticker):
            raise RuntimeError("upstream unavailable")

    monkeypatch.setattr(yfinance, "Ticker", FailingTicker)
    assert fe.fetch_one({"ticker": "NVDA"}) is None
    report = reports()[0]
    assert report["partition"] == "NVDA" and report["status"] == "failed"
    assert report["last_success"] == "" and "upstream unavailable" in report["detail"]


@pytest.fixture
def swallowing_calendar_library(monkeypatch):
    """Reproduce Quote._fetch HTTPError -> None -> calendar={} in yfinance 1.8."""
    import requests
    config = SimpleNamespace(debug=SimpleNamespace(hide_exceptions=True))
    response = SimpleNamespace(text="simulated HTTP 503", status_code=503)
    failure = requests.HTTPError("simulated HTTP 503", response=response)

    class Quote:
        def _fetch(self):
            try:
                raise failure
            except requests.HTTPError:
                if not config.debug.hide_exceptions:
                    raise
                return None

        @property
        def calendar(self):
            result = self._fetch()
            return {} if result is None else result

    class Ticker:
        def __init__(self, ticker):
            self.calendar = Quote().calendar

    monkeypatch.setattr(yfinance, "config", config, raising=False)
    monkeypatch.setattr(yfinance, "Ticker", Ticker)
    return config, Quote


def test_swallowed_http_failure_is_failed_and_original_policy_is_restored(swallowing_calendar_library):
    config, Quote = swallowing_calendar_library
    assert Quote().calendar == {}  # The real failure mode before our context.
    assert fe.fetch_one({"ticker": "NVDA"}) is None
    assert reports()[0]["status"] == "failed"
    assert reports()[0]["last_success"] == ""
    assert config.debug.hide_exceptions is True


def test_existing_strict_policy_is_not_reenabled_after_failure(swallowing_calendar_library):
    config, _ = swallowing_calendar_library
    config.debug.hide_exceptions = False
    assert fe.fetch_one({"ticker": "NVDA"}) is None
    assert reports()[0]["status"] == "failed"
    assert config.debug.hide_exceptions is False


def test_real_yfinance_quote_http_failure_is_not_a_successful_empty_calendar(monkeypatch):
    # Exercise the installed library's complete _fetch -> _fetch_calendar chain;
    # only the lowest transport is replaced. No Yahoo network call is made.
    from yfinance.scrapers.quote import HTTPError, Quote, YfConfig

    class FailingData:
        def get_raw_json(self, *a, **k):
            raise HTTPError("simulated HTTP 503", response=SimpleNamespace(text="503"))

    class Ticker:
        def __init__(self, ticker):
            self._quote = Quote(FailingData(), ticker)

        @property
        def calendar(self):
            return self._quote.calendar

    debug = YfConfig.debug
    original = debug.hide_exceptions
    try:
        debug.hide_exceptions = True
        assert Ticker("NVDA").calendar == {}
        monkeypatch.setattr(yfinance, "Ticker", Ticker)
        assert fe.fetch_one({"ticker": "NVDA"}) is None
        assert reports()[0]["status"] == "failed"
        assert YfConfig.debug.hide_exceptions is True
    finally:
        debug.hide_exceptions = original


def test_legitimate_empty_calendar_remains_empty_and_restores_policy(monkeypatch):
    config = SimpleNamespace(debug=SimpleNamespace(hide_exceptions=True))

    class EmptyTicker:
        def __init__(self, ticker):
            assert config.debug.hide_exceptions is False
            self.calendar = {}

    monkeypatch.setattr(yfinance, "config", config, raising=False)
    monkeypatch.setattr(yfinance, "Ticker", EmptyTicker)
    assert fe.fetch_one({"ticker": "EMPTY"}) is None
    assert reports()[0]["status"] == "empty"
    assert config.debug.hide_exceptions is True


def test_legacy_raise_errors_control_is_scoped_and_restored(monkeypatch):
    config = SimpleNamespace(debug=SimpleNamespace(raise_errors=False))

    class LegacyTicker:
        def __init__(self, ticker):
            if config.debug.raise_errors:
                raise RuntimeError("legacy HTTP failure")
            self.calendar = {}

    monkeypatch.setattr(yfinance, "config", config, raising=False)
    monkeypatch.setattr(yfinance, "Ticker", LegacyTicker)
    assert fe.fetch_one({"ticker": "NVDA"}) is None
    assert reports()[0]["status"] == "failed"
    assert config.debug.raise_errors is False


def test_unknown_exception_control_never_certifies_an_empty_calendar(monkeypatch):
    monkeypatch.setattr(yfinance, "config", SimpleNamespace(debug=SimpleNamespace()), raising=False)
    monkeypatch.setattr(yfinance, "Ticker", _FakeTicker)
    assert fe.fetch_one({"ticker": "EMPTY"}) is None
    assert reports()[0]["status"] == "failed"
    assert "exception policy" in reports()[0]["detail"]


@pytest.mark.parametrize("calendar", [[], "invalid", 0,
                                         {"Earnings Date": "2026-11-18"},
                                         {"Earnings Date": [dt.date(2026, 11, 18), None]}])
def test_fetch_one_rejects_wrong_calendar_types(monkeypatch, calendar):
    class BadTicker:
        def __init__(self, ticker):
            self.calendar = calendar

    monkeypatch.setattr(yfinance, "Ticker", BadTicker)
    assert fe.fetch_one({"ticker": "NVDA"}) is None
    assert reports()[0]["status"] == "invalid_schema"


def test_fetch_one_accepts_a_datetime_but_emits_an_iso_date(monkeypatch):
    class DateTimeTicker:
        def __init__(self, ticker):
            self.calendar = {"Earnings Date": [dt.datetime(2026, 11, 18, 16, 30)]}

    monkeypatch.setattr(yfinance, "Ticker", DateTimeTicker)
    item = fe.fetch_one({"ticker": "NVDA"})
    assert item.earnings_date == "2026-11-18"
    assert reports()[0]["status"] == "ok"


def test_fetch_all_skips_failures(monkeypatch):
    _patch(monkeypatch)
    companies = [
        {"ticker": "NVDA", "name": "NVIDIA", "subsector": "s"},
        {"ticker": "EMPTY", "name": "E", "subsector": "s"},
        {"ticker": "RANGE", "name": "R", "subsector": "s"},
    ]
    out = fe.fetch_all(companies)
    assert sorted(e.ticker for e in out) == ["NVDA", "RANGE"]


def test_fetch_all_isolates_success_empty_and_failed_tickers(monkeypatch):
    class MixedTicker(_FakeTicker):
        @property
        def calendar(self):
            if self.ticker == "FAIL":
                raise RuntimeError("rate limited")
            return super().calendar

    monkeypatch.setattr(yfinance, "Ticker", MixedTicker)
    out = fe.fetch_all([{"ticker": t} for t in ("NVDA", "EMPTY", "FAIL")])
    assert [item.ticker for item in out] == ["NVDA"]
    assert {r["partition"]: r["status"] for r in reports()} == {
        "NVDA": "ok", "EMPTY": "empty", "FAIL": "failed"}


def test_enrichment_failure_keeps_successfully_fetched_date(monkeypatch):
    def fail(ticker):
        raise RuntimeError("enrichment failed")

    monkeypatch.setattr(yfinance, "Ticker", fail)
    item = fe.Earnings(ticker="NVDA", name="NVIDIA", subsector="AI semiconductors",
                       earnings_date="2026-11-18")
    assert fe.enrich_last_quarter(item) is item
    assert item.earnings_date == "2026-11-18"


def test_enrich_last_quarter(monkeypatch):
    import pandas as pd
    df = pd.DataFrame(
        {"epsActual": [1.60, 1.87], "epsEstimate": [1.53, 1.77]},
        index=pd.to_datetime(["2026-01-31", "2026-04-30"]),
    )

    class _FT:
        def __init__(self, tk):
            pass

        @property
        def earnings_history(self):
            return df

    monkeypatch.setattr(yfinance, "Ticker", _FT)
    e = fe.Earnings(ticker="NVDA", name="NVIDIA", subsector="s",
                    earnings_date="2026-06-03")
    fe.enrich_last_quarter(e)
    assert e.last_eps_actual == 1.87        # most recent quarter
    assert e.last_eps_estimate == 1.77
    assert e.last_quarter == "2026-04-30"

