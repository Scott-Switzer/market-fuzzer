let spec = null;
let campaign = null;

const $ = (selector) => document.querySelector(selector);
const escapeHtml = (value) => String(value ?? "—").replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
const pretty = (value) => JSON.stringify(value, null, 2);
const label = (value) => String(value ?? "unknown").replaceAll('_', ' ');

async function request(path, options = {}) {
  const response = await fetch(path, options);
  if (!response.ok) throw new Error(await response.text());
  return response.json();
}

async function post(path, payload) {
  return request(path, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)});
}

function setStatus(text, kind = 'neutral') {
  const node = $('#engine-status');
  node.textContent = text;
  node.className = `status ${kind}`;
}

function openView(name) {
  document.querySelectorAll('.tab').forEach(node => node.classList.toggle('active', node.dataset.view === name));
  document.querySelectorAll('.view').forEach(node => node.classList.toggle('active', node.id === `view-${name}`));
}

document.querySelectorAll('.tab').forEach(node => node.addEventListener('click', () => openView(node.dataset.view)));

function statusBadge(status) {
  const normalized = String(status ?? 'not evaluated').toLowerCase();
  const kind = normalized === 'fit' || normalized.includes('pass') || normalized.includes('approved') ? 'pass' : normalized.includes('fail') || normalized.includes('block') ? 'fail' : normalized === 'limited' || normalized.includes('partial') || normalized.includes('warn') ? 'partial' : 'neutral';
  return `<span class="status ${kind}">${escapeHtml(label(status))}</span>`;
}

function entries(value) {
  if (Array.isArray(value)) return value;
  if (value && typeof value === 'object') return Object.entries(value).map(([name, item]) => typeof item === 'object' ? {name, ...item} : {name, value: item});
  return [];
}

function renderCalibration(pack) {
  const calibration = pack.calibration || {};
  pack = pack.pack || pack;
  const sources = pack.sources || pack.data_sources || [];
  const targets = pack.targets || pack.metrics || pack.calibration_targets || pack.objectives || [];
  const coverage = pack.coverage || pack.scope || {instrument: pack.instrument, venue: pack.venue, session: pack.session, accepted_parameter_sets: calibration.accepted_parameter_sets?.length};
  $('#calibration-summary').className = '';
  $('#calibration-summary').innerHTML = `<div class="fact-grid">
    <div><small>PACK</small><strong>${escapeHtml(pack.name || pack.pack_id || pack.calibration_id || 'Demo calibration')}</strong></div>
    <div><small>VERSION</small><strong>${escapeHtml(pack.version || pack.schema_version || 'prototype')}</strong></div>
    <div><small>SOURCES</small><strong>${sources.length || entries(pack).length}</strong></div>
    <div><small>TARGETS</small><strong>${entries(targets).length}</strong></div>
  </div>${sources.length ? `<ul class="clean-list">${sources.map(source => `<li>${escapeHtml(typeof source === 'string' ? source : source.name || source.title || source.id)}</li>`).join('')}</ul>` : ''}`;
  $('#calibration-coverage').className = '';
  $('#calibration-coverage').innerHTML = `<dl class="definition-list">${entries(coverage).map(item => `<div><dt>${escapeHtml(label(item.name))}</dt><dd>${escapeHtml(item.value ?? item.status ?? item.description ?? pretty(item))}</dd></div>`).join('') || '<div><dt>Boundary</dt><dd>Research-grade prototype; no institutional calibration claim.</dd></div>'}</dl>`;
  const rows = entries(targets);
  $('#calibration-targets').innerHTML = rows.length ? `<table><thead><tr><th>Target</th><th>Definition / value</th><th>Status</th><th>Source</th></tr></thead><tbody>${rows.map(item => `<tr><td><strong>${escapeHtml(item.name || item.metric || item.id)}</strong></td><td>${escapeHtml(item.definition || item.target || item.value || item.description)}</td><td>${statusBadge(item.status || 'target')}</td><td>${escapeHtml(item.source || item.reference)}</td></tr>`).join('')}</tbody></table>` : `<pre>${escapeHtml(pretty(pack))}</pre>`;
}

function renderInterventions(summary) {
  const scenarios = summary.scenarios || summary.runs || summary.worlds || summary;
  const rows = entries(scenarios);
  $('#intervention-summary').className = 'scenario-grid';
  $('#intervention-summary').innerHTML = rows.length ? rows.map((item, index) => {
    const metrics = item.summary || item.metrics || item;
    return `<article class="scenario-card"><div><span class="scenario-number">0${index + 1}</span><h4>${escapeHtml(label(item.scenario || item.name || item.id))}</h4></div><p>${escapeHtml(item.mutation || item.description || item.what_changed || 'Controlled counterfactual world')}</p><dl><div><dt>Fill rate</dt><dd>${metrics.fill_rate != null ? `${(Number(metrics.fill_rate) * (Number(metrics.fill_rate) <= 1 ? 100 : 1)).toFixed(1)}%` : '—'}</dd></div><div><dt>Shortfall</dt><dd>${metrics.implementation_shortfall_bps ?? metrics.shortfall_bps ?? '—'} bps</dd></div></dl></article>`;
  }).join('') : '<p class="empty">Campaign returned no scenario summaries.</p>';
  const claim = summary.claim_gate;
  if (claim) {
    $('#claim-gate').className = '';
    $('#claim-gate').innerHTML = `<h3>Claim gate: cost increases with participation</h3><div class="fact-grid"><div><small>SPEARMAN ρ</small><strong>${Number(claim.spearman_rho).toFixed(2)}</strong></div><div><small>POSITIVE PAIRS</small><strong>${(Number(claim.positive_paired_change_fraction) * 100).toFixed(1)}%</strong></div><div><small>SLOPE 95% LOWER</small><strong>${Number(claim.bootstrap_slope_interval?.[0]).toFixed(2)}</strong></div><div><small>CALIBRATION AGREEMENT</small><strong>${(Number(claim.calibration_set_agreement) * 100).toFixed(1)}%</strong></div></div><p>${statusBadge(claim.permitted ? 'FIT' : 'BLOCKED')} ${escapeHtml(claim.blocking_reasons?.join('; ') || 'All declared thresholds passed.')}</p>`;
  }
}

function reportRows(report) {
  const metrics = report.vectors || report.metrics || report.checks || report.diagnostics || report.results || report;
  return entries(metrics);
}

function renderReport(selector, report) {
  const rows = reportRows(report || {});
  $(selector).className = '';
  $(selector).innerHTML = rows.length ? `<table><thead><tr><th>Check</th><th>Observed</th><th>Verdict</th></tr></thead><tbody>${rows.map(item => `<tr><td><strong>${escapeHtml(label(item.name || item.metric || item.id))}</strong><small>${escapeHtml(item.evidence || item.description || '')}</small></td><td>${escapeHtml(item.value ?? item.observed ?? item.result ?? '—')}</td><td>${statusBadge(item.status || item.verdict || 'not evaluated')}</td></tr>`).join('')}</tbody></table>` : '<p class="empty">No component diagnostics returned.</p>';
}

function overall(report) {
  return report?.overall_status || report?.overall_verdict || report?.status || report?.verdict || 'not evaluated';
}

function renderChart(summary) {
  const runs = summary?.runs || summary?.scenarios || campaign?.intervention_summary?.runs || [];
  const rows = entries(runs);
  if (!rows.length) { $('#result-chart').className = 'chart empty'; $('#result-chart').textContent = 'No measured scenario runs.'; return; }
  const values = rows.map(item => Number((item.summary || item.metrics || item).implementation_shortfall_bps ?? (item.summary || item.metrics || item).shortfall_bps ?? 0));
  const max = Math.max(...values.map(Math.abs), 1);
  $('#result-chart').className = 'chart';
  $('#result-chart').innerHTML = rows.map((item, i) => {
    const metrics = item.summary || item.metrics || item;
    const fill = Number(metrics.fill_rate ?? 0) * (Number(metrics.fill_rate ?? 0) <= 1 ? 100 : 1);
    return `<div class="bar-group"><div class="bar-value">${values[i].toFixed(1)} bps</div><div class="bar-track"><i style="height:${Math.max(5, Math.abs(values[i]) / max * 150)}px"></i></div><strong>${escapeHtml(label(item.scenario || item.name || item.id))}</strong><small>${fill.toFixed(1)}% filled</small></div>`;
  }).join('');
}

function renderRelease(manifest, releaseReport) {
  const decision = overall(releaseReport);
  const decisionNode = $('#release-decision');
  decisionNode.textContent = label(decision);
  decisionNode.className = `status ${String(decision).toLowerCase() === 'fit' || String(decision).toLowerCase().includes('pass') || String(decision).toLowerCase().includes('approve') ? 'pass' : String(decision).toLowerCase().includes('fail') || String(decision).toLowerCase().includes('block') ? 'fail' : 'partial'}`;
  $('#manifest').textContent = pretty(manifest || {});
  $('#release-gate').className = '';
  const checks = reportRows(releaseReport || {});
  $('#release-gate').innerHTML = `<div class="release-verdict">${statusBadge(decision)}<p>${escapeHtml(releaseReport?.interpretation || releaseReport?.summary || 'Release decision is derived from explicit validation checks.')}</p></div>${checks.length ? `<ul class="gate-list">${checks.map(item => `<li>${statusBadge(item.status || item.verdict)} <span>${escapeHtml(label(item.name || item.metric || item.id))}</span></li>`).join('')}</ul>` : ''}`;
  const artifacts = manifest?.artifacts || manifest?.artifact_hashes || manifest?.files || [];
  $('#release-contents').className = '';
  $('#release-contents').innerHTML = entries(artifacts).length ? `<div class="artifact-grid">${entries(artifacts).map(item => `<article><strong>${escapeHtml(item.name || item.path || item.id)}</strong><small>${escapeHtml(item.hash || item.sha256 || item.value || 'included')}</small></article>`).join('')}</div>` : '<p>Manifest generated. Artifact downloads are available when produced by the campaign backend.</p>';
}

function renderCampaign(data) {
  campaign = data;
  const experimentId = data.experiment_id || data.manifest?.experiment_id || 'quick-campaign';
  $('#experiment-label').textContent = experimentId;
  if (data.calibration_pack) renderCalibration(data.calibration_pack);
  renderInterventions(data.intervention_summary || {});
  renderReport('#simulator-validation', data.simulator_validation_report || {});
  renderReport('#release-validation', data.synthetic_release_validation_report || {});
  renderChart(data.intervention_summary || {});
  const simulatorStatus = overall(data.simulator_validation_report);
  const releaseStatus = overall(data.synthetic_release_validation_report);
  const scenarios = entries(data.intervention_summary?.runs || data.intervention_summary?.scenarios || []);
  const worst = scenarios.reduce((current, item) => {
    const value = Number((item.summary || item.metrics || item).implementation_shortfall_bps ?? -Infinity);
    return value > current.value ? {value, name: item.scenario || item.name || item.id} : current;
  }, {value: -Infinity, name: '—'});
  $('#validation-headline').innerHTML = `<div class="metric"><small>CAMPAIGN</small><strong>${escapeHtml(experimentId)}</strong><span>quick mode</span></div><div class="metric"><small>SIMULATOR</small><strong>${escapeHtml(label(simulatorStatus))}</strong><span>component checks</span></div><div class="metric"><small>SYNTHETIC RELEASE</small><strong>${escapeHtml(label(releaseStatus))}</strong><span>governance gate</span></div><div class="metric"><small>WORST WORLD</small><strong>${escapeHtml(label(worst.name))}</strong><span>${Number.isFinite(worst.value) ? `${worst.value.toFixed(1)} bps` : 'not measured'}</span></div>`;
  renderRelease(data.manifest || {}, data.synthetic_release_validation_report || {});
}

$('#load-calibration').addEventListener('click', async () => {
  setStatus('Loading calibration…');
  try {
    const data = await request('/api/calibration/demo');
    renderCalibration(data.calibration_pack || data);
    setStatus('Calibration loaded', 'pass');
  } catch (error) {
    setStatus('Calibration unavailable', 'partial');
    $('#calibration-summary').innerHTML = `<p class="error">${escapeHtml(error.message)}</p>`;
  }
});

$('#compile').addEventListener('click', async () => {
  setStatus('Compiling…');
  $('#compile-messages').innerHTML = '';
  try {
    const compiled = await post('/api/compile', {prompt: $('#prompt').value, seed: Number($('#seed').value)});
    spec = compiled.spec;
    $('#spec').textContent = pretty(compiled);
    $('#compile-messages').innerHTML = '<span class="status pass">Valid</span><span>Seeded specification compiled offline.</span>';
    setStatus('World compiled', 'pass');
  } catch (error) {
    $('#compile-messages').innerHTML = `<span class="status fail">Invalid</span><span>${escapeHtml(error.message)}</span>`;
    setStatus('Compile failed', 'fail');
  }
});

$('#run-campaign').addEventListener('click', async () => {
  if (!spec) await $('#compile').click();
  if (!spec) return;
  setStatus('Running campaign…');
  $('#run-campaign').disabled = true;
  $('#run-campaign').textContent = 'Running…';
  try {
    let data;
    try {
      data = await post('/api/validation-campaign', {spec, mode: 'quick'});
    } catch (campaignError) {
      const fallback = await post('/api/battery', {spec});
      data = {
        experiment_id: `legacy-${Date.now()}`,
        intervention_summary: fallback,
        simulator_validation_report: {overall_status: 'partial', checks: [{name:'deterministic scenario battery', status:'pass', value:`${fallback.runs?.length || 0} worlds`}, {name:'full calibration campaign', status:'not evaluated', value:'backend upgrade pending'}]},
        synthetic_release_validation_report: {overall_status:'not evaluated', checks:[], summary:'Governed release validation requires the validation-campaign endpoint.'},
        manifest: {engine_backend:'internal', compiler_mode:'offline', seeds:fallback.runs?.map(run => run.spec.seed), limitations:['Legacy battery fallback; no governed artifact release.']}
      };
    }
    renderCampaign(data);
    setStatus('Campaign complete', 'pass');
    openView('validation');
  } catch (error) {
    setStatus('Campaign failed', 'fail');
    $('#intervention-summary').innerHTML = `<p class="error">${escapeHtml(error.message)}</p>`;
  } finally {
    $('#run-campaign').disabled = false;
    $('#run-campaign').textContent = 'Run quick campaign';
  }
});
