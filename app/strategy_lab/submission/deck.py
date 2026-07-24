"""Pitch deck builder: renders docs/pitch-deck/index.html from REAL evidence.

Every number shown is read from artifacts/submission/<sha>/pitch/deck_data.json.
If no run exists, it fails loudly rather than fabricating data.
"""

from __future__ import annotations

import glob
import json
from pathlib import Path

DECK_PATH = Path("app/static/pitch-deck/index.html")


def _latest_deck_data() -> dict:
    matches = sorted(glob.glob("artifacts/submission/*/pitch/deck_data.json"))
    if not matches:
        raise RuntimeError("no submission evidence found; run `make submission-demo` first")
    return json.loads(Path(matches[-1]).read_text())


def build_deck() -> str:
    data = _latest_deck_data()
    h = data["historical"]
    s = data["synthetic"]
    minz = data.get("minimized") or {}
    adj = data.get("adjacent_pass") or {}
    lims = "\n".join(f"<li>{x}</li>" for x in data["limitations"])

    def pct(x):
        return f"{100 * x:,.2f}%"

    def f2(x):
        return f"{x:,.2f}"

    failed = ", ".join(s["failed_mechanisms"]) or "none"

    html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Fenrix Strategy Validation Lab — Submission Deck</title>
<style>
 body{{font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;margin:0;background:#0b1020;color:#e7ecf3}}
 .slide{{max-width:900px;margin:24px auto;background:#121a2e;border-radius:14px;padding:32px;box-shadow:0 8px 30px rgba(0,0,0,.35)}}
 h1{{font-size:30px;margin:0 0 6px}} h2{{font-size:20px;color:#7fd1ff;margin-top:0}}
 .kpi{{display:flex;flex-wrap:wrap;gap:14px;margin:14px 0}}
 .card{{flex:1 1 160px;background:#0f1730;border:1px solid #243352;border-radius:10px;padding:14px}}
 .card b{{display:block;font-size:24px;color:#9affc4}} .card span{{font-size:12px;color:#9fb3d1}}
 .warn{{border-left:4px solid #ffb020;background:#1c2436;padding:12px 16px;border-radius:8px;color:#ffd9a0}}
 ul{{line-height:1.5}} code{{background:#0b1020;padding:1px 5px;border-radius:4px;color:#9affc4}}
 table{{width:100%;border-collapse:collapse;margin-top:10px}} th,td{{text-align:left;padding:8px;border-bottom:1px solid #243352}}
</style></head><body>

<div class="slide"><h1>Fenrix Strategy Validation Lab</h1>
<h2>Turn a strategy into a reviewed contract &rarr; test on history &rarr; search sealed synthetic failures</h2>
<p><b>Audience:</b> quant-finance students, instructors, retail quant researchers.<br>
<b>Strategy hash (immutable):</b> <code>{data["strategy_hash"][:16]}</code> &nbsp; <b>git:</b> <code>{data["git_sha"]}</code><br>
<b>Data mode of record:</b> {data["data_mode"]} &nbsp; <b>Universe size:</b> {data["universe_size"]}</p></div>

<div class="slide"><h2>1 &middot; What it does (the one story)</h2>
<ol>
<li>Plain-English strategy &rarr; structured clause ledger (every clause reviewed).</li>
<li>Mandatory approve step &rarr; immutable strategy version + canonical SHA-256 hash.</li>
<li>Same locked hash runs a <b>real multi-asset</b> historical backtest (performance, trades, costs, exposures, benchmark).</li>
<li>Same locked hash enters a <b>sealed synthetic</b> stress search for reproducible failures.</li>
<li>Confirmed failure &rarr; minimized replay + adjacent passing case &rarr; evidence-linked recommendation.</li>
</ol></div>

<div class="slide"><h2>2 &middot; Real historical backtest (Tier per data mode)</h2>
<div class="kpi">
<div class="card"><b>{pct(h["cumulative_return"])}</b><span>cumulative return</span></div>
<div class="card"><b>{f2(h["sharpe"])}</b><span>Sharpe</span></div>
<div class="card"><b>{pct(h["max_drawdown"])}</b><span>max drawdown</span></div>
<div class="card"><b>{pct(h["volatility"])}</b><span>ann. volatility</span></div>
<div class="card"><b>{pct(h["cost_pct_of_capital"])}</b><span>cost % of capital</span></div>
<div class="card"><b>{h["trades"]}</b><span>trades</span></div>
</div>
<p>Benchmark CAGR <code>{pct(h["benchmark_cagr"])}</code> &middot; Information ratio <code>{f2(h["information_ratio"])}</code> &middot; Gross exposure avg <code>{pct(h["gross_exposure_avg"])}</code> &middot; Net exposure avg <code>{pct(h["net_exposure_avg"])}</code></p>
<div class="warn">Costs are explicit bounded heuristics (commission/spread/slippage/borrow in bps). <b>Not</b> validated broker calibrations.</div></div>

<div class="slide"><h2>3 &middot; Sealed synthetic stress (reproducible failures)</h2>
<div class="kpi">
<div class="card"><b>{s["evaluated"]}</b><span>worlds searched</span></div>
<div class="card"><b>{s["failure_count"]}</b><span>confirmed failures</span></div>
<div class="card"><b>{pct(s["failure_rate"])}</b><span>failure rate</span></div>
</div>
<p>Mechanisms searched: <code>{", ".join(s["mechanisms_searched"])}</code></p>
<p>Confirmed-failure mechanisms: <b>{failed}</b></p>
<div class="warn">These are generated worlds, not historical events. They probe <i>fragility</i>, not real future risk. No claim of universal discovery or realism.</div></div>

<div class="slide"><h2>4 &middot; Confirmed failure &rarr; minimize &rarr; adjacent pass</h2>
<p><b>Minimized failure:</b> {minz.get("mechanism", "n/a")} at intensity <code>{minz.get("minimized_intensity", "n/a")}</code> (still fails: {minz.get("still_fails", "n/a")}).</p>
<p><b>Adjacent passing case:</b> {adj.get("mechanism", "n/a")} seed delta <code>{adj.get("delta_from_failure_seed", "n/a")}</code> passes: {adj.get("passes", "n/a")}.</p>
<p>Recommendation: tighten the failing mechanism's guard (e.g. cap per-name weight, add a vol-scaling stop, or widen the cost buffer) and re-run the sealed search before any capital decision.</p></div>

<div class="slide"><h2>5 &middot; Honest limitations (read before sharing)</h2>
<ul>{lims}</ul></div>

<div class="slide"><h2>6 &middot; How to reproduce</h2>
<pre style="background:#0b1020;padding:14px;border-radius:8px;overflow:auto">
make submission-demo      # full pipeline + evidence
make verify-submission    # tests + invariant checks
make pitch-deck           # rebuild this deck from evidence</pre>
<p>All numbers above were rendered from <code>deck_data.json</code> produced by <code>make submission-demo</code>. No hand-entered figures.</p></div>

</body></html>"""
    DECK_PATH.parent.mkdir(parents=True, exist_ok=True)
    DECK_PATH.write_text(html)
    return str(DECK_PATH)
