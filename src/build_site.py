"""Render the static AI earnings-calendar site (self-contained HTML, no JS/CDN)."""
from __future__ import annotations

import datetime as dt
import html
import json
from collections import defaultdict
from pathlib import Path

from src.config import SITE_DIR
from src.fetch_earnings import Earnings

DISCLAIMER = (
    "数据来自 Yahoo Finance（经 yfinance 抓取）。财报日期可能为预估，未经公司确认前会变动；"
    "EPS / 营收为分析师共识预期，非实际结果。本页仅供研究参考，不构成任何投资建议。"
)

_CSS = """
:root{--bg:#0f1115;--card:#1a1d24;--ink:#e8eaed;--muted:#9aa0aa;--line:#2a2e37;
--accent:#4c8bf5;--ok:#2ea043;--warn:#d2992b;}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"PingFang SC",
"Hiragino Sans GB","Microsoft YaHei",sans-serif;line-height:1.5;}
.wrap{max-width:900px;margin:0 auto;padding:0 16px 64px;}
header{position:sticky;top:0;background:rgba(15,17,21,.92);backdrop-filter:blur(8px);
border-bottom:1px solid var(--line);padding:14px 16px;z-index:9;}
header .wrap{padding:0;display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;}
h1{font-size:18px;margin:0;}
.upd{color:var(--muted);font-size:12px;}
h2{font-size:16px;margin:26px 0 6px;}
.dayhdr{font-size:13px;color:var(--accent);margin:18px 0 6px;font-weight:600;
border-bottom:1px solid var(--line);padding-bottom:4px;}
.stats{display:flex;gap:10px;flex-wrap:wrap;margin:14px 0;}
.stat{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 14px;flex:1;min-width:110px;}
.stat .n{font-size:22px;font-weight:700;} .stat .l{color:var(--muted);font-size:12px;}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:13px 15px;margin:9px 0;}
.row{display:flex;align-items:center;gap:8px;flex-wrap:wrap;}
.co{font-weight:700;font-size:15px;}
.tk{font:600 11px/1 ui-monospace,monospace;background:#11141a;border:1px solid var(--line);
color:var(--accent);padding:3px 6px;border-radius:6px;}
.sub{font-size:11px;color:var(--muted);background:#11141a;border:1px solid var(--line);padding:2px 7px;border-radius:6px;}
.when{margin-left:auto;font-size:12px;color:var(--ink);text-align:right;}
.rel{color:var(--warn);font-weight:600;}
.badge{font-size:10.5px;padding:2px 7px;border-radius:999px;border:1px solid var(--line);}
.conf{color:#5fd97a;} .est{color:#e9bd64;}
.ests{display:flex;gap:18px;margin-top:9px;flex-wrap:wrap;}
.est-item .k{font-size:11px;color:var(--muted);} .est-item .v{font-size:15px;font-weight:600;}
table{width:100%;border-collapse:collapse;font-size:13px;margin-top:6px;display:block;overflow-x:auto;}
th,td{text-align:left;padding:6px 10px;border-bottom:1px solid var(--line);white-space:nowrap;}
th{color:var(--muted);font-weight:600;}
.disc{background:#1a1410;border:1px solid #3a2a18;color:#e9c98f;border-radius:10px;padding:11px 14px;font-size:12.5px;margin:14px 0;}
footer{margin-top:32px;color:var(--muted);font-size:12px;border-top:1px solid var(--line);padding-top:14px;}
.empty{color:var(--muted);padding:8px 0;}
a{color:var(--accent);text-decoration:none;} a:hover{text-decoration:underline;}
"""

_WD = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def _esc(s) -> str:
    return html.escape(str(s if s is not None else ""))


def _fmt_eps(v) -> str:
    if v is None:
        return "—"
    return (f"-${abs(v):.2f}" if v < 0 else f"${v:.2f}")


def _fmt_rev(v) -> str:
    if v is None:
        return "—"
    if abs(v) >= 1e9:
        return f"${v/1e9:.2f}B"
    if abs(v) >= 1e6:
        return f"${v/1e6:.0f}M"
    return f"${v:,.0f}"


def _rel(d: dt.date, today: dt.date) -> str:
    n = (d - today).days
    if n == 0:
        return "今天"
    if n == 1:
        return "明天"
    if n == 2:
        return "后天"
    return f"{n} 天后"


def _yf_link(tk: str) -> str:
    return f"https://finance.yahoo.com/quote/{tk}"


def _card(e: Earnings, today: dt.date) -> str:
    d = dt.date.fromisoformat(e.earnings_date)
    badge = ('<span class="badge conf">已确认</span>' if e.date_confirmed
             else '<span class="badge est">预估日期</span>')
    return f"""<div class="card">
  <div class="row">
    <span class="co">{_esc(e.name)}</span>
    <a class="tk" href="{_yf_link(e.ticker)}" target="_blank" rel="noopener">{_esc(e.ticker)}</a>
    <span class="sub">{_esc(e.subsector)}</span>
    <span class="when"><span class="rel">{_rel(d, today)}</span><br>{d:%Y-%m-%d} {_WD[d.weekday()]}</span>
  </div>
  <div class="row" style="margin-top:6px">{badge}</div>
  <div class="ests">
    <div class="est-item"><div class="k">EPS 共识预期</div><div class="v">{_fmt_eps(e.eps_estimate)}</div></div>
    <div class="est-item"><div class="k">营收 共识预期</div><div class="v">{_fmt_rev(e.revenue_estimate)}</div></div>
  </div>
</div>"""


def build_html(this_week: list[Earnings], upcoming: list[Earnings],
               run_date: dt.date | None = None) -> str:
    today = run_date or dt.datetime.now(dt.timezone.utc).date()
    parts: list[str] = []
    A = parts.append
    A('<!doctype html><html lang="zh"><head><meta charset="utf-8">')
    A('<meta name="viewport" content="width=device-width,initial-scale=1">')
    A("<title>AI 公司财报日历</title>")
    A(f"<style>{_CSS}</style></head><body>")
    A('<header><div class="wrap"><h1>🗓️ AI 公司财报日历</h1>'
      f'<span class="upd">更新于 {today} (UTC) · 本周 {len(this_week)} 家</span></div></header>')
    A('<div class="wrap">')

    A('<div class="stats">'
      f'<div class="stat"><div class="n">{len(this_week)}</div><div class="l">未来 7 天</div></div>'
      f'<div class="stat"><div class="n">{len(upcoming)}</div><div class="l">未来 8–30 天</div></div>'
      '</div>')

    # This week, grouped by date
    A("<h2>本周财报（未来 7 天）</h2>")
    if this_week:
        by_day: dict[str, list[Earnings]] = defaultdict(list)
        for e in this_week:
            by_day[e.earnings_date].append(e)
        for day in sorted(by_day):
            d = dt.date.fromisoformat(day)
            A(f'<div class="dayhdr">{d:%Y-%m-%d} {_WD[d.weekday()]} · {_rel(d, today)}</div>')
            for e in sorted(by_day[day], key=lambda x: x.name):
                A(_card(e, today))
    else:
        A('<div class="empty">未来 7 天内，名单内的 AI 公司暂无已排定的财报。</div>')

    # Coming up (8-30 days) compact table
    A("<h2>未来 8–30 天</h2>")
    if upcoming:
        A("<table><tr><th>日期</th><th>公司</th><th>代码</th><th>板块</th>"
          "<th>EPS 预期</th><th>营收 预期</th></tr>")
        for e in sorted(upcoming, key=lambda x: (x.earnings_date, x.name)):
            d = dt.date.fromisoformat(e.earnings_date)
            A(f"<tr><td>{d:%m-%d} {_WD[d.weekday()]}</td><td>{_esc(e.name)}</td>"
              f'<td><a class="tk" href="{_yf_link(e.ticker)}" target="_blank" rel="noopener">{_esc(e.ticker)}</a></td>'
              f"<td>{_esc(e.subsector)}</td><td>{_fmt_eps(e.eps_estimate)}</td>"
              f"<td>{_fmt_rev(e.revenue_estimate)}</td></tr>")
        A("</table>")
    else:
        A('<div class="empty">未来 8–30 天暂无数据。</div>')

    A(f'<footer><div class="disc">⚠️ {_esc(DISCLAIMER)}</div>'
      ' · <a href="earnings.json" download>下载数据 (JSON)</a></footer>')
    A("</div></body></html>")
    return "\n".join(parts)


def build_site(this_week: list[Earnings], upcoming: list[Earnings],
               site_dir: Path = SITE_DIR, run_date: dt.date | None = None) -> Path:
    site_dir = Path(site_dir)
    site_dir.mkdir(parents=True, exist_ok=True)
    (site_dir / "index.html").write_text(
        build_html(this_week, upcoming, run_date), encoding="utf-8")
    payload = {"updated": (run_date or dt.datetime.now(dt.timezone.utc).date()).isoformat(),
               "this_week": [e.to_dict() for e in this_week],
               "upcoming": [e.to_dict() for e in upcoming]}
    (site_dir / "earnings.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return site_dir / "index.html"
