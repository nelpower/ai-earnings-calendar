import datetime as dt
from dataclasses import replace

from src.build_ics import build_ics_text
from src.event_state import legacy_uid, same_event, track_events
from src.events_model import Event


TODAY = dt.date(2026, 11, 16)


def test_natural_event_keeps_migrated_legacy_uid_across_multiple_date_updates():
    original = Event(date="2026-11-18", category="policy", title="Briefing", tickers=["NVDA"])
    first = track_events([original], [original], TODAY)[0]
    revised = replace(original, date="2026-12-01")
    second = track_events([revised], [first], TODAY + dt.timedelta(days=1))[0]
    third_source = replace(revised, date="2026-12-02")
    third = track_events([third_source], [second], TODAY + dt.timedelta(days=2))[0]
    steady = track_events([third_source], [third], TODAY + dt.timedelta(days=3))[0]
    assert {e.meta["ics_uid"] for e in (first, second, third, steady)} == {legacy_uid(original)}
    assert {e.meta["event_id"] for e in (first, second, third, steady)} == {first.meta["event_id"]}
    assert [e.meta["revision"] for e in (first, second, third, steady)] == [0, 1, 2, 2]
    assert steady.meta["modified_on"] == "2026-11-18"
    assert steady.meta["original_proposed_date"] == "2026-11-18"
    assert steady.meta["previous_date"] == "2026-12-01"
    ics = build_ics_text([steady], TODAY)
    assert "SEQUENCE:2" in ics and f"UID:{legacy_uid(original)}" in ics


def test_same_title_does_not_merge_distinct_explicit_source_ids():
    old = Event(date="2026-11-18", category="policy", title="Hearing",
                meta={"event_id": "committee-a"})
    fresh = replace(old, meta={"event_id": "committee-b"})
    previous = track_events([old], [], TODAY)[0]
    current = track_events([fresh], [previous], TODAY)[0]
    assert not same_event(old, fresh)
    assert current.meta["event_id"] == "committee-b"
    assert current.meta["ics_uid"] != previous.meta["ics_uid"]
    assert current.meta["revision"] == 0


def test_explicit_source_id_allows_large_postponement_and_title_update():
    old = Event(date="2026-11-18", category="policy", title="Hearing",
                meta={"event_id": "committee-appearance"})
    previous = track_events([old], [], TODAY)[0]
    current = track_events([replace(old, date="2027-03-01", title="Rescheduled hearing")],
                           [previous], TODAY)[0]
    assert current.meta["ics_uid"] == previous.meta["ics_uid"]
    assert current.meta["revision"] == 1


def test_adding_explicit_source_id_preserves_an_existing_subscription():
    source = Event(date="2026-11-18", category="policy", title="Hearing")
    previous = track_events([source], [], TODAY)[0]
    current = track_events([replace(source, meta={"event_id": "new-source-id"})],
                           [previous], TODAY)[0]
    assert current.meta["ics_uid"] == previous.meta["ics_uid"]
    assert current.meta["event_id"] == "new-source-id"
    assert current.meta["identity_generated"] is False


def test_untickered_ipo_issuers_receive_distinct_identities():
    alpha = Event(date="2026-11-18", category="ipo", title="Alpha IPO 预计定价",
                  meta={"event_type": "pricing"})
    beta = replace(alpha, title="Beta IPO 预计定价")
    tracked = track_events([alpha, beta], [], TODAY)
    assert tracked[0].meta["ics_uid"] != tracked[1].meta["ics_uid"]
    revised = track_events([replace(alpha, date="2026-11-19", title="Alpha IPO 定价")],
                           tracked, TODAY)[0]
    assert revised.meta["ics_uid"] == tracked[0].meta["ics_uid"]


def test_legacy_nasdaq_pricing_identity_survives_phase_and_title_metadata():
    original = Event(date="2026-11-18", category="ipo", title="Anthropic IPO (ANT)",
                     tickers=["ANT"], source_url="https://www.nasdaq.com/market-activity/ipos")
    revised = replace(original, date="2026-11-19", title="Anthropic IPO 定价 (ANT)",
                      tickers=["ant"], meta={"event_type": "pricing"})
    tracked = track_events([revised], [original], TODAY)[0]
    assert tracked.meta["ics_uid"] == legacy_uid(original)
    assert tracked.meta["revision"] == 1


def test_monthly_occurrences_do_not_inherit_each_others_uid():
    november = Event(date="2026-11-20", category="options", title="Monthly expiry")
    december = replace(november, date="2026-12-18")
    previous = track_events([november], [], TODAY)
    # New occurrence is listed first deliberately, to catch greedy reuse.
    current = track_events([december, november], previous, TODAY)
    assert current[1].meta["ics_uid"] == previous[0].meta["ics_uid"]
    assert current[0].meta["ics_uid"] != current[1].meta["ics_uid"]


def test_one_previous_occurrence_can_only_be_assigned_once():
    source = Event(date="2026-11-18", category="policy", title="Hearing")
    previous = track_events([source], [], TODAY)
    current = track_events([replace(source, date="2026-11-19"), source], previous, TODAY)
    assert current[1].meta["ics_uid"] == previous[0].meta["ics_uid"]
    assert current[0].meta["ics_uid"] != current[1].meta["ics_uid"]


def test_explicit_source_match_takes_precedence_over_a_natural_name_match():
    source = Event(date="2026-11-18", category="policy", title="Hearing",
                   meta={"event_id": "source-id"})
    previous = track_events([source], [], TODAY)
    ambiguous = replace(source, meta={})
    current = track_events([ambiguous, source], previous, TODAY)
    assert current[1].meta["ics_uid"] == previous[0].meta["ics_uid"]
    assert current[0].meta["ics_uid"] != current[1].meta["ics_uid"]


def test_changed_calendar_link_increments_subscription_revision():
    source = Event(date="2026-11-18", category="macro", title="CPI", source_url="https://example.org/old")
    previous = track_events([source], [], TODAY)
    current = track_events([replace(source, source_url="https://example.org/new")], previous, TODAY)[0]
    assert current.meta["revision"] == 1
def test_source_outage_updates_subscribed_event_once_without_changing_uid():
    from src.events_model import Event
    from src.event_state import track_events
    today = dt.date(2026, 11, 16)
    fresh = Event(date="2026-11-18", category="earnings", title="NVIDIA 财报", tickers=["NVDA"])
    initial = track_events([fresh], [], today)[0]
    stale = Event(date=fresh.date, category=fresh.category, title=fresh.title, tickers=fresh.tickers,
                  meta={"date_source_stale": True, "date_last_verified": "2026-11-16"})
    first = track_events([stale], [initial], today + dt.timedelta(days=1))[0]
    second = track_events([stale], [first], today + dt.timedelta(days=2))[0]
    assert first.meta["ics_uid"] == initial.meta["ics_uid"] == second.meta["ics_uid"]
    assert first.meta["revision"] == initial.meta["revision"] + 1
    assert second.meta["revision"] == first.meta["revision"]
    ics = build_ics_text([first], today).replace("\r\n ", "")
    assert "日程来源暂不可用，沿用 2026-11-16 核验记录" in ics

