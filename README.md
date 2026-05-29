# AI Earnings Calendar

A daily, auto-updating web page listing **AI-related companies that report
earnings in the next 7 days** — with the consensus **EPS / revenue** estimates
for each. Data comes from Yahoo Finance via `yfinance` (no API key).

> Research/heads-up tool only. Estimates are analyst consensus, dates can be
> unconfirmed and change, and this is **not investment advice**.

## What it shows

- **本周财报（未来 7 天）** — cards grouped by date: company, ticker (→ Yahoo),
  subsector, confirmed/estimated date, consensus EPS, consensus revenue.
- **未来 8–30 天** — a compact preview table.
- Auto-refreshed daily and published to GitHub Pages.

## Universe

81 AI-business companies (in `data/ai_companies.yaml`), across: AI
semiconductors, semi equipment, EDA, networking/optical, AI servers/power,
hyperscalers, AI software/security/data, AI cloud/GPU, power & nuclear for AI
data centers, and consumer/platform AI. **Every ticker was verified against
SEC `company_tickers.json`.** Add/remove freely — each entry is
`{ticker, name, subsector}`.

## Install & run

```bash
cd ai-earnings-calendar
python -m pip install -r requirements.txt
python -m src.pipeline                 # fetch + build site/
python -m src.pipeline --throttle 0.3  # gentler on Yahoo
python -m pytest                       # tests (network mocked)
```

Open `site/index.html`. Outputs: `outputs/earnings.json` (full dataset, committed)
and `site/` (the published page + `earnings.json` download).

## Deploy (GitHub Pages, daily)

```bash
git init && git add -A && git commit -m "init"
gh repo create ai-earnings-calendar --public --source=. --push
# repo → Settings → Pages → Source = GitHub Actions → Actions tab → Run workflow
```

`.github/workflows/daily.yml` runs daily at **11:00 UTC (07:00 ET / 19:00 北京)**,
fetches, rebuilds the site, commits the refreshed data, and deploys to Pages.

## How it works (run logic)

1. Load the 81-company universe.
2. For each ticker, read `yf.Ticker(tk).calendar` → next earnings date +
   consensus EPS/revenue (per-ticker errors are skipped, never fatal).
3. Keep only **future** dates: `+1 … +7 days` = "this week", `+8 … +30` =
   "upcoming". Same-day/past dates are excluded (a just-reported company keeps a
   stale same-day date in yfinance briefly — this avoids showing it as upcoming).
4. Write `outputs/earnings.json`, build the static site, deploy.
5. If a fetch returns **0 companies** (e.g. Yahoo rate-limited the runner), it
   falls back to the last committed `earnings.json` so the page never goes blank.

## Limitations

- **yfinance is unofficial.** Yahoo can rate-limit or block cloud IPs; dates and
  estimates occasionally lag or differ from other providers. The Yahoo link on
  each card lets you verify. If reliability becomes a problem, switch the
  `fetch_earnings` backend to a keyed API (Finnhub/FMP) — the rest is unchanged.
- **No intraday BMO/AMC time** — yfinance gives a date, not the session timing.
- **Same-day reporters** are intentionally excluded (see run logic #3).
- Consensus figures are point-in-time and revise.

## Layout

```
ai-earnings-calendar/
  README.md  requirements.txt  pyproject.toml
  .github/workflows/daily.yml
  data/ai_companies.yaml          (the AI universe, SEC-verified tickers)
  src/  config.py  fetch_earnings.py  build_site.py  pipeline.py
  tests/  outputs/(earnings.json)  site/(generated)
```
