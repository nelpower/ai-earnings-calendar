"""Tests for the yfinance parsing layer (network mocked)."""
import datetime as dt

import yfinance

from src import fetch_earnings as fe


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


def test_fetch_all_skips_failures(monkeypatch):
    _patch(monkeypatch)
    companies = [
        {"ticker": "NVDA", "name": "NVIDIA", "subsector": "s"},
        {"ticker": "EMPTY", "name": "E", "subsector": "s"},
        {"ticker": "RANGE", "name": "R", "subsector": "s"},
    ]
    out = fe.fetch_all(companies)
    assert sorted(e.ticker for e in out) == ["NVDA", "RANGE"]


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
