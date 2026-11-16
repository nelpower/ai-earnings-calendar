# AI Market-Event Calendar

A daily AI market-event timeline with sourced dates, visible collection health,
and a separate queue for official announcements that still need review.

Live: https://nelpower.github.io/ai-earnings-calendar/

## Coverage

- **183 listed companies** in `data/ai_companies.yaml` (114 existing + 69 added).
  New entries carry current ticker/CIK evidence, a primary business source, a
  verification date and relevance note. Coverage includes chips, equipment,
  materials, optical networking, memory, servers, power/cooling, cloud,
  enterprise software, edge AI, robotics/autonomy and data-center colocation.
- **20 issuer/brand identities** in `data/issuer_watchlist.yaml`, with aliases,
  official URLs and listing status. Private or subsidiary brands can be
  watched without pretending they have a tradable earnings ticker. A proposed
  ticker or an SEC identifier does not establish that trading has begun.
- **17 official discovery sources** in `data/discovery_sources.yaml`: House
  committee announcements, Senate Banking schedules/news, NVIDIA/AMD investor
  events and news, Anthropic/OpenAI news, official macro schedules, and SEC
  submissions for NVIDIA, AMD and Anthropic.
- Yahoo Finance supplies next earnings dates, consensus figures and the last
  quarter's actual/estimated EPS. Nasdaq supplies AI-relevant IPO **pricing**
  dates and assumed IPO+180-day lockups. Curated sources backstop major IPOs,
  macro releases and conferences. Options and index dates are computed.

The Yahoo dependency is pinned to **yfinance 1.8.0**. Its default can turn an
HTTP error into an empty calendar; the adapter temporarily enables exception
propagation and restores the library configuration afterward. Upgrade it only
with the failure-path tests.

## What becomes a calendar event

A news/RSS publication date is **never** an activity date. Ordinary official
announcements and SEC filing dates go to `candidates.json` with
`review_status: needs_review`. SEC registration/filing is not IPO completion.

Only explicit full dates from verified official calendar adapters or
structured Event data can enter automatically. The pipeline prefers an
official company earnings date over Yahoo while preserving available consensus
figures. Pricing and first trading day are separate IPO phases.

Manually sourced dates stay in `data/events.yaml` and
`data/macro_events.yaml`. Expected/requested dates require
`meta.date_confirmed: false`, plus optional `meta.date_label`.
Use `meta.verified_on` for a real primary-source review date; loading a YAML
file never refreshes it. The macro file retains its original June review date
and separately records the November recheck of the currently upcoming releases.

The homepage shows:
- A notice when a date originally proposed for this week has moved.
- Collection failures, cached records, date conflicts and overdue reviews.
- A collapsible official-announcement review queue with source links.

This is a discovery and verification workflow, not a completeness guarantee.
Some official sites return HTTP 403 to automated requests; those are visible
failures, never successful empty results.

## Recovery and updates

Each source reports `ok / empty / failed / invalid_schema`, partitioned by
ticker for earnings and source month for Nasdaq. Health reports include raw,
accepted and rejected counts, last success, and consecutive failures.

- **Explicit failures** retain only that partition's known future records.
  Cached events show their original last-verification date.
- **Successful empty responses** clear that partition; withdrawn records are
  not indefinitely copied back from yesterday's feed.
- A partial failure cannot erase all other tickers or months.
- Coverage drops exclude events that expired normally and appear as warnings.
- Different official/estimated dates are visible conflicts, not silent
  overwrites. Actual IPO pricing replaces a corresponding estimate.
- Stable event identities, preserved subscription UIDs and increasing ICS
  SEQUENCE values let date corrections update an existing subscribed event.
  Explicit cancellations use STATUS:CANCELLED. Archive records separately label
  unverified passed dates and cancellations; a passed scheduled date alone
  does not establish that the event actually occurred.

The review queue preserves first-seen dates and review decisions across runs.
A changed dedicated schedule becomes pending again. To review a candidate,
check its primary source, add/update a sourced event where warranted, and mark
its entry in `outputs/candidates.json` reviewed with a note. A reviewed lead
does not automatically become a dated event.

## Run and inspect

```sh
python -m pip install -r requirements.txt
python -m pytest -q
python -m src.pipeline --throttle 0.25
python -m src.pipeline --no-ipo --no-discovery
python -m src.quality
```

Generated artifacts:

| Path | Purpose |
| --- | --- |
| `outputs/events.json` | Committed event state and stable identities |
| `outputs/source_status.json` | Collection health, coverage, conflicts and review deadlines |
| `outputs/candidates.json` | Persistent official-announcement review queue |
| `outputs/archive.jsonl` | Append-only past-date/outcome archive |
| `site/index.html` | Standalone homepage |
| `site/events.json` | Current this-week/upcoming event data |
| `site/events.ics` | Subscribe from Apple/Google Calendar |
| `site/source_status.json`, `site/candidates.json` | Public diagnostic and review exports |

US events use Eastern calendar dates. The data day/header uses Beijing time.
No IPO first-trade hour is invented. Non-USD revenue uses its reporting
currency; foreign EPS is shown without an asserted currency when ambiguous.

## CI and publishing

`.github/workflows/tests.yml` runs offline tests and a live-source candidate
build on pull requests and `codex/**` pushes, then uploads the candidate site.

`.github/workflows/daily.yml` runs at 16:20 / 20:20 / 00:20 UTC (Beijing
00:20 / 04:20 / 08:20), on relevant main-branch source changes, and manually.
It runs tests, builds, writes source warnings and a GitHub run summary, commits
the output state and deploys GitHub Pages. Output-only commits do not
recursively trigger source-change deployments.

Macro schedule snapshots produce review signals; their HTML is not blindly
converted into dates. The remaining seeded macro horizon and unreviewed
announcements stay visible in the report. Assumed 180-day IPO lockups are
estimates and must be checked against prospectus terms.

Research/heads-up tool only; not investment advice.

