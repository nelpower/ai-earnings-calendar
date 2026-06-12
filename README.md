# AI Market-Event Calendar

A daily, auto-updating web timeline of **market-moving events for the AI complex**
— not just earnings, but macro data, Fed/FOMC, conferences/keynotes, options
expiry, index rebalances, and AI-relevant IPOs — each with an importance level
and a "看点 (what to watch)" note.

Live: published to GitHub Pages. Research/heads-up tool only — **not investment advice**.

## Event categories & where the data comes from

| Category | Auto? | Source |
|---|---|---|
| 财报 Earnings | ✅ | yfinance `.calendar` (date + consensus EPS/revenue) + `earnings_history` (last-qtr actual vs est) + `info.financialCurrency` (revenue currency, e.g. TSM=TWD) |
| 宏观 Macro (CPI/非农/PCE) | ✅ curated | Official BLS/BEA release schedules (`data/macro_events.yaml`, seeded through Dec 2026) |
| 美联储 Fed / FOMC + Jackson Hole | ✅ curated | federalreserve.gov FOMC calendar (date = decision day) + kansascityfed.org |
| 期权到期 / 四巫日 | ✅ computed | 3rd Friday each month, **shifted to the prior trading day when it's an NYSE holiday** (e.g. Juneteenth Fri 2026-06-19 → Thu 06-18); quad-witching in Mar/Jun/Sep/Dec |
| 指数重构 Index | ✅ computed | Russell reconstitution, **semi-annual since 2026**: 4th Friday of June + 2nd Friday of December (FTSE Russell notice 2025-11-05) |
| 发布会 / 大会 Conferences | 🟡 curated | `data/events.yaml` (GTC/WWDC/Computex…, announced months ahead) |
| IPO | 🟡 scraped | Nasdaq IPO calendar, **keyword-filtered to AI-relevant names** |

5 categories are fully automatic; conferences are hand-curated (no clean API);
IPOs are scraped from Nasdaq and filtered (silver-miners/insurers dropped).

## Universe

`data/ai_companies.yaml` — 114 AI-business tickers (SEC-verified) used by the
earnings provider. `data/macro_events.yaml` + `data/events.yaml` carry the
curated macro/Fed/conference events. Edit any of them freely.

## Install & run

```bash
cd ai-earnings-calendar
python -m pip install -r requirements.txt
python -m src.pipeline                 # fetch everything + build site/
python -m src.pipeline --no-ipo        # skip the Nasdaq IPO scrape
python -m src.pipeline --throttle 0.3  # gentler on Yahoo
python -m pytest                       # tests (network mocked)
```

Open `site/index.html`. Outputs: `outputs/events.json` (committed) + `site/`
(`index.html` + `events.json` + `events.ics`).

The site is a single self-contained HTML page (no CDN): the legend chips filter
by category, and `events.ics` can be subscribed to from Apple/Google Calendar
(`https://nelpower.github.io/ai-earnings-calendar/events.ics`).

## How it works (daily run logic)

1. `static_events()` loads curated macro/Fed + conferences from YAML.
2. `deterministic_events()` computes options-expiry / quad-witching / Russell dates.
3. `ipo_events()` scrapes Nasdaq's IPO calendar and keeps only AI-relevant names.
4. `earnings_events()` pulls next earnings for all 114 tickers (yfinance), keeps
   the in-window ones, and adds last-quarter actual-vs-estimate.
5. All events merge into one timeline → `today … +45d` (day 0 included — a US
   after-hours report is still upcoming on its own date), split into **本周
   (≤7d)** and **未来 8–45 天**, color-coded by category with importance + 看点.
6. Write `outputs/events.json`, build the site (+ `events.ics`), deploy to Pages.
7. If the earnings fetch returns nothing (Yahoo blocked the runner), the last
   committed earnings are reused so that section never disappears. Macro/Fed/
   conference/options/index events are network-free and always present.

`.github/workflows/daily.yml` runs daily (11:00 UTC = 19:00 北京) and deploys.

## Limitations

- **yfinance / Yahoo** is unofficial; earnings dates can lag official IR by ±1
  day and have no BMO/AMC time. Estimated (range) dates show a "日期待确认"
  badge. The card's Yahoo link lets you verify.
- **EPS currency for foreign reporters is ambiguous** on Yahoo (TSM EPS is
  USD-per-ADR but ASML EPS is EUR), so non-USD reporters show a bare EPS
  number; revenue always shows its reporting currency.
- **Macro/conference dates are curated** — re-check the official schedules yearly
  (the files note the source URLs). FOMC/CPI/非农/PCE seeded through Dec 2026
  (verified 2026-06-12); 非农 dates beyond July follow the BLS scheduling rule.
- **IPO keyword filter** favours precision; it can miss an oddly-named AI IPO
  (curate it in `events.yaml`) or, rarely, admit a borderline one.
- Consensus figures are point-in-time and revise.

## Layout

```
ai-earnings-calendar/
  data/  ai_companies.yaml  macro_events.yaml  events.yaml
  src/   config.py  events_model.py  fetch_earnings.py  providers.py
         pipeline.py  build_site.py
  tests/  outputs/(events.json)  site/(generated)
  .github/workflows/daily.yml
```
