/* Customer workflow override: supports multiple executable strategy families. */
document.getElementById("run").onclick = async () => {
  const file = document.getElementById("csv").files[0];
  const status = document.getElementById("status");
  status.hidden = false;
  if (!file) {
    status.textContent = "Choose a historical price CSV first.";
    return;
  }
  try {
    status.textContent = "Running the historical backtest and 120 unseen synthetic markets…";
    const closes = closeColumn(await file.text());
    const response = await fetch("/api/robustness/sma", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        closes,
        strategy_type: document.getElementById("strategy").value,
        fast_window: Number(document.getElementById("fast").value),
        slow_window: Number(document.getElementById("slow").value),
        worlds_per_regime: 30,
      }),
    });
    const result = await response.json();
    if (!response.ok) throw Error(result.detail || "The test failed.");
    const metrics = result.historical_backtest;
    document.getElementById("metrics").innerHTML = [
      ["Return", `${fmt(metrics.total_return_pct)}%`],
      ["Max drawdown", `${fmt(metrics.max_drawdown_pct)}%`],
      ["Sharpe", fmt(metrics.sharpe)],
      ["Win rate", `${fmt(metrics.win_rate_pct)}%`],
      ["Trades", metrics.trades],
    ].map(item => `<div class="metric"><b>${item[1]}</b><span>${item[0]}</span></div>`).join("");
    document.getElementById("regimes").innerHTML = result.synthetic_forward_test.regimes.map(item =>
      `<div class="regime"><strong>${item.regime}</strong><span>Lost money in <b>${item.loss_rate_pct}%</b> of ${item.worlds} worlds<br>Median return ${item.median_return_pct}% · worst drawdown ${item.worst_drawdown_pct}%</span></div>`
    ).join("");
    if (baseline) {
      const oldWorst = Math.max(...baseline.synthetic_forward_test.regimes.map(item => item.loss_rate_pct));
      const newWorst = Math.max(...result.synthetic_forward_test.regimes.map(item => item.loss_rate_pct));
      document.getElementById("comparison-panel").hidden = false;
      document.getElementById("comparison").innerHTML = [
        ["Historical return", `${fmt(baseline.historical_backtest.total_return_pct)}% → ${fmt(metrics.total_return_pct)}%`],
        ["Worst loss rate", `${fmt(oldWorst)}% → ${fmt(newWorst)}%`],
        ["Max drawdown", `${fmt(baseline.historical_backtest.max_drawdown_pct)}% → ${fmt(metrics.max_drawdown_pct)}%`],
      ].map(item => `<div class="metric"><b>${item[1]}</b><span>${item[0]}</span></div>`).join("");
    }
    document.getElementById("failure").textContent = result.failure_summary;
    document.getElementById("suggestion").textContent = `Suggested experiment: increase the slow/lookback period to ${result.suggested_test.slow_window}. ${result.suggested_test.reason}`;
    document.getElementById("retry").onclick = () => {
      baseline = result;
      document.getElementById("slow").value = result.suggested_test.slow_window;
      document.getElementById("run").click();
    };
    document.getElementById("limits").textContent = result.limitations;
    document.getElementById("results").hidden = false;
    status.textContent = `Complete: ${result.strategy.name} backtest plus ${result.synthetic_forward_test.worlds} unseen markets.`;
  } catch (error) {
    status.textContent = error.message;
  }
};
