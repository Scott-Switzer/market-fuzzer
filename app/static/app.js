let spec = null;
const json = (el, value) => el.textContent = JSON.stringify(value, null, 2);
const request = async (path, payload) => {
  const response = await fetch(path, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)});
  if (!response.ok) throw new Error(await response.text());
  return response.json();
};
function cards(runs) {
  return runs.map(run => `<article><strong>${run.spec.scenario.replaceAll('_', ' ')}</strong><span>${(run.summary.fill_rate * 100).toFixed(1)}% filled</span><b>${run.summary.implementation_shortfall_bps} bps</b><small>final spread ${run.summary.final_spread_bps ?? 'halted'} bps</small></article>`).join('');
}
function showWorld(run) {
  const final = run.timeline.at(-1);
  const companies = Object.entries(run.world.companies).map(([ticker, company]) => `<li><b>${ticker}</b> · ${company.sector} · fundamental $${company.fundamental.toFixed(2)}</li>`).join('');
  const bids = final.book.bids.map(level => `${level.quantity} @ $${level.price}`).join('<br>') || '—';
  const asks = final.book.asks.map(level => `${level.quantity} @ $${level.price}`).join('<br>') || '—';
  document.querySelector('#world').innerHTML = `<div><h3>Macro regime</h3><p>${run.world.macro.name}: ${run.world.macro.final_state}</p><h3>Companies</h3><ul>${companies}</ul><h3>Agents</h3><p>${run.world.agent_ecology.join(' · ')}</p></div><div><h3>Live NOVA book</h3><p class="book"><span class="ask">ASKS<br>${asks}</span><span>MID<br><b>$${final.mid}</b><br>spread ${final.spread_bps ?? '—'} bps</span><span class="bid">BIDS<br>${bids}</span></p><h3>Event log</h3><p>${run.world.events.map(e => `t${e.step}: ${e.message}`).join('<br>') || 'No stress event'}</p></div>`;
}
function draw(runs) {
  const values = runs.map(r => r.summary.implementation_shortfall_bps);
  const maximum = Math.max(...values, 1);
  document.querySelector('#chart').innerHTML = values.map((v, i) => `<div class="bar"><i style="height:${Math.max(6, v / maximum * 150)}px"></i><span>${runs[i].spec.scenario.replaceAll('_', ' ')}</span></div>`).join('');
}
document.querySelector('#compile').onclick = async () => {
  spec = await request('/api/compile', {prompt: document.querySelector('#prompt').value, seed: 42});
  json(document.querySelector('#spec'), spec);
  document.querySelector('#summary').innerHTML = '<p>World compiled. Run the four-world battery to identify stress sensitivity.</p>';
};
document.querySelector('#battery').onclick = async () => {
  if (!spec) await document.querySelector('#compile').onclick();
  const result = await request('/api/battery', {spec});
  document.querySelector('#summary').innerHTML = cards(result.runs);
  draw(result.runs);
  json(document.querySelector('#finding'), result.failure_surface);
  showWorld(result.runs.find(run => run.spec.scenario === spec.scenario) || result.runs[0]);
};
