import datetime as dt
from dataclasses import replace

from src.events_model import Event
from src.quality import build_report, carry_source_history, recover_failed, source_key


TODAY = dt.date(2026, 11, 16)


def earnings(ticker, date="2026-11-18", **meta):
    return Event(date=date, category="earnings", title=f"{ticker} 财报", tickers=[ticker],
                 meta={"source": "earnings", "source_partition": ticker, **meta})


def pricing(date="2026-11-18", title="Anthropic IPO", phase="pricing", **meta):
    return Event(date=date, category="ipo", title=title, tickers=["ANT"],
                 source_url="https://www.nasdaq.com/market-activity/ipos",
                 meta={"source": "nasdaq_ipo", "source_partition": date[:7],
                       "event_type": phase, **meta})


def status(source, partition, result, checked_on="2026-11-16", **fields):
    return {"source": source, "partition": partition, "status": result,
            "checked_on": checked_on, **fields}


def test_only_failed_partitions_recover_and_successful_empty_partitions_clear():
    previous = [earnings("FAIL"), earnings("EMPTY"), earnings("OK")]
    reports = [status("earnings", "FAIL", "failed"), status("earnings", "EMPTY", "empty"),
               status("earnings", "OK", "ok")]
    result = recover_failed([], previous, reports, TODAY, "2026-11-15")
    assert [e.tickers for e in result] == [["FAIL"]]
    assert result[0].meta["source_stale"] is True
    assert result[0].meta["last_verified"] == "2026-11-15"
    assert recover_failed([], previous, [], TODAY) == []  # never infer total failure from []


def test_failed_month_does_not_keep_events_from_a_successfully_empty_other_month():
    previous = [pricing(), pricing("2026-12-01", title="December issuer")]
    reports = [status("nasdaq_ipo", "2026-11", "invalid_schema"),
               status("nasdaq_ipo", "2026-12", "empty")]
    result = recover_failed([], previous, reports, TODAY)
    assert [e.date for e in result] == ["2026-11-18"]


def test_repeated_outage_preserves_original_last_verification_and_stale_since():
    previous = [earnings("NVDA", last_verified="2026-11-14")]
    old_report = {"sources": [status("earnings", "NVDA", "ok", "2026-11-14",
                                     last_success="2026-11-14", consecutive_failures=0)]}
    first_sources = carry_source_history([status("earnings", "NVDA", "failed")], old_report)
    first = recover_failed([], previous, first_sources, TODAY, "2026-11-15")
    second_day = TODAY + dt.timedelta(days=1)
    second_sources = carry_source_history(
        [status("earnings", "NVDA", "failed", second_day.isoformat())], {"sources": first_sources})
    second = recover_failed([], first, second_sources, second_day, "2026-11-16")
    assert second[0].meta["last_verified"] == "2026-11-14"
    assert second[0].meta["stale_since"] == "2026-11-16"
    assert second_sources[0]["last_success"] == "2026-11-14"
    assert second_sources[0]["consecutive_failures"] == 2
    recovered = carry_source_history([status("earnings", "NVDA", "empty", "2026-11-17")],
                                    {"sources": second_sources})
    assert recovered[0]["consecutive_failures"] == 0
    assert recovered[0]["last_success"] == "2026-11-17"
    assert recover_failed([], second, recovered, second_day) == []


def test_expired_cached_event_cannot_be_resurrected():
    previous = [earnings("OLD", date="2026-11-15")]
    assert recover_failed([], previous, [status("earnings", "OLD", "failed")], TODAY) == []


def test_current_official_date_supersedes_failed_partition_cache_across_months():
    previous = [pricing()]
    current = [pricing("2026-12-01", title="Official revised IPO pricing")]
    reports = [status("nasdaq_ipo", "2026-11", "failed"), status("nasdaq_ipo", "2026-12", "ok")]
    result = recover_failed(current, previous, reports, TODAY)
    assert result == current


def test_pricing_and_listing_remain_distinct_when_recovering_a_failure():
    previous = [pricing()]
    current = [pricing("2026-11-19", phase="listing")]
    result = recover_failed(current, previous, [status("nasdaq_ipo", "2026-11", "failed")], TODAY)
    assert {e.meta["event_type"] for e in result} == {"pricing", "listing"}


def test_legacy_pricing_is_not_duplicated_when_current_title_and_phase_changed():
    previous = [replace(pricing(), meta={})]
    current = [pricing(title="Anthropic IPO 预计定价")]
    result = recover_failed(current, previous, [status("nasdaq_ipo", "2026-11", "failed")], TODAY)
    assert result == current


def test_legacy_earnings_partition_is_case_normalised():
    original = Event(date="2026-11-18", category="earnings", title="NVIDIA 财报",
                     tickers=["nvda"])
    assert source_key(original) == ("earnings", "NVDA")
    assert len(recover_failed([], [original], [status("earnings", "NVDA", "failed")], TODAY)) == 1


def test_coverage_drop_excludes_records_that_expired_normally():
    expired = [earnings(str(i), date="2026-11-15") for i in range(4)]
    report = build_report([], expired, [], [], [], TODAY, 4, 0, [])
    assert not any(w["kind"] == "coverage_drop" for w in report["warnings"])
    missing = [earnings(str(i)) for i in range(4)]
    report = build_report([], missing, [], [], [], TODAY, 4, 0, [])
    assert any(w["kind"] == "coverage_drop" for w in report["warnings"])
