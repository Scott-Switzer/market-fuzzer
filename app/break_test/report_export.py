from __future__ import annotations

import html
import json
from datetime import datetime, timezone
from typing import Any


def _esc(value: Any) -> str:
    return html.escape(str(value))


def render_html_report(result: dict[str, Any]) -> str:
    strategy = result.get("strategy", {})
    historical = result.get("historical") or {}
    forward = result.get("forward_test") or {}
    regimes = forward.get("regimes") or []
    analysis = result.get("failure_analysis") or {}
    suggestion = result.get("correction_suggestion") or {}

    def metrics_table(metrics: dict[str, Any]) -> str:
        return "".join(f"<tr><td>{_esc(k)}</td><td>{_esc(v)}</td></tr>" for k, v in metrics.items())

    def rows(regimes_list: list[dict[str, object]]) -> str:
        return "".join(
            "<tr>"
            f"<td>{_esc(r.get('regime',''))}</td>"
            f"<td>{_esc(r.get('worlds',''))}</td>"
            f"<td>{_esc(r.get('loss_rate_pct',''))}%</td>"
            f"<td>{_esc(r.get('median_return_pct',''))}%</td>"
            f"<td>{_esc(r.get('worst_drawdown_pct',''))}%</td>"
            f"<td>{_esc(r.get('best_return_pct',''))}%</td>"
            "</tr>"
            for r in regimes_list
        )

    corrected = result.get("corrected")
    corrected_block = ""
    if corrected:
        corr_forward = corrected.get("forward_test") or {}
        corr_regimes = corr_forward.get("regimes") or []
        corrected_block = (
            "<div class=\"card\"><h2>Corrected</h2>"
            "<table><thead><tr><th>Regime</th><th>Worlds</th><th>Loss Rate</th><th>Median Return</th>"
            "<th>Worst Drawdown</th><th>Best Return</th></tr></thead><tbody>"
        ) + rows(corr_regimes) + "</tbody></table></div>"

    insight_items = analysis.get("segmentation_insights") or []
    insights_html = "".join(f"<li>{_esc(x)}</li>" for x in insight_items)
    insights_block = f"<h3>Insights</h3><ul>{insights_html}</ul>" if insights_html else ""

    alts = suggestion.get("alternatives") or []
    alternatives_html = "".join(
        "<li>"
        f"<strong>{_esc(a.get('label',''))}</strong> - {_esc(a.get('reason',''))}<br/>"
        f"<code>{_esc(json.dumps(a.get('parameter_changes', {})))}</code>"
        "</li>"
        for a in alts
    )
    alternatives_block = f"<h3>Alternatives</h3><ul>{alternatives_html}</ul>" if alternatives_html else ""

    lines = [
        "<!doctype html>",
        "<html lang=\"en\">",
        "<head>",
        "<meta charset=\"utf-8\" />",
        "<title>Break Test Report</title>",
        "<style>",
        "  body { font-family: ui-sans-serif, system-ui, -apple-system, sans-serif; background: #0f172a; color: #e2e8f0; padding: 24px; }",
        "  .card { background: #1f2e45; border: 1px solid #2b3c56; border-radius: 12px; padding: 14px; margin-bottom: 14px; }",
        "  table { width: 100%; border-collapse: collapse; }",
        "  th, td { text-align: left; padding: 8px; border-bottom: 1px solid #2b3c56; }",
        "  th { color: #f1f5f9; }",
        "  td { color: #e2e8f0; }",
        "  h1 { font-size: 20px; margin: 0 0 6px; }",
        "  h2 { font-size: 16px; margin: 0 0 8px; }",
        "  .meta { color: #94a3b8; font-size: 12px; }",
        "  code { background: #0b1220; padding: 4px 6px; border-radius: 6px; color: #38bdf8; }",
        "  ul { margin: 8px 0; padding-left: 20px; }",
        "  .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }",
        "</style>",
        "</head>",
        "<body>",
        "<div class=\"card\">",
        "  <h1>Break Test Report</h1>",
        f"  <div class=\"meta\">Generated {_esc(datetime.now(timezone.utc).isoformat())}Z | session: {_esc(result.get('session_id',''))}</div>",
        "</div>",
        "<div class=\"grid\">",
        "  <div class=\"card\">",
        "    <h2>Strategy</h2>",
        f"    <div class=\"meta\">Type: {_esc(strategy.get('type',''))}</div>",
        f"    <div class=\"meta\">Params: {_esc(json.dumps(strategy.get('parameters', {})))}</div>",
        f"    <div class=\"meta\">Forward mode: {_esc(result.get('forward_mode',''))}</div>",
        "  </div>",
        "  <div class=\"card\">",
        "    <h2>Outcome</h2>",
        f"    <div class=\"meta\">Session: {_esc(result.get('session_id',''))}</div>",
        f"    <div class=\"meta\">Total synthetic worlds: {_esc(forward.get('total_worlds',''))}</div>",
        f"    <div class=\"meta\">Overall loss rate: {_esc(forward.get('overall_loss_rate_pct',''))}%</div>",
        "  </div>",
        "</div>",
        "<div class=\"card\">",
        "  <h2>Historical Backtest</h2>",
        "  <table>",
        "    <thead><tr><th>Metric</th><th>Value</th></tr></thead>",
        "    <tbody>" + metrics_table(historical) + "</tbody>",
        "  </table>",
        "</div>",
        "<div class=\"card\">",
        "  <h2>Forward Test</h2>",
        "  <table>",
        "    <thead><tr><th>Regime</th><th>Worlds</th><th>Loss Rate</th><th>Median Return</th><th>Worst Drawdown</th><th>Best Return</th></tr></thead>",
        "    <tbody>" + rows(regimes) + "</tbody>",
        "  </table>",
        "</div>",
        corrected_block,
        "<div class=\"card\">",
        "  <h2>Failure Analysis</h2>",
        f"  <p>{_esc(result.get('failure_summary',''))}</p>",
        "  " + insights_block,
        "</div>",
        "<div class=\"card\">",
        "  <h2>Suggested Correction</h2>",
        f"  <p class=\"meta\">{_esc(suggestion.get('rationale',''))}</p>",
        "  " + alternatives_block,
        "</div>",
        "</body>",
        "</html>",
    ]
    return "\n".join(lines)


def render_pdf_report(result: dict[str, Any]) -> tuple[bytes, str, str]:
    """Placeholder PDF export."""
    html_report = render_html_report(result).encode("utf-8")
    return html_report, "application/octet-stream", f"report-{result.get('session_id', 'report')}.bin"
