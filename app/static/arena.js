let challenge = null;
let configuredPresetId = "aggressive_pov";
let comparisonPolicyId = "guarded_pov";
let lastRun = null;
let submissionId = null;
let currentRole = null;
let currentUser = null;
let formInitialized = false;
let practiceRemaining = null;
let challengeDesignOptions = null;
const challengePath = "/api/arena/execution/challenges/trade-the-shock";
const $ = (selector) => document.querySelector(selector);
const esc = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (character) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        character
      ],
  );
const fmt = (number, digits = 1) => Number(number ?? 0).toFixed(digits);

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  if (!response.ok) {
    const payload = await response
      .json()
      .catch(() => ({ detail: response.statusText }));
    const detail =
      typeof payload.detail === "string"
        ? payload.detail
        : JSON.stringify(payload.detail || payload);
    throw new Error(detail);
  }
  return response.json();
}
const get = (path) => api(path);
const post = (path, body = {}) =>
  api(path, { method: "POST", body: JSON.stringify(body) });

function setBusy(button, busy, label) {
  if (!button.dataset.label) button.dataset.label = button.textContent;
  button.disabled = busy;
  button.textContent = busy ? label : button.dataset.label;
}

function applyLifecycleControls() {
  if (!challenge) return;
  const phase = challenge.phase;
  const isStudent = currentRole === "student";
  const isInstructor = currentRole === "instructor";
  const practiceOpen = phase === "public_practice";
  $("#run-public").disabled =
    !isStudent || !practiceOpen || practiceRemaining === 0;
  $("#comparison-policy").disabled = !isStudent || !practiceOpen;
  $("#save-draft").disabled =
    !isStudent || !practiceOpen || Boolean(submissionId);
  $("#submit-final").disabled =
    !isStudent || !practiceOpen || Boolean(submissionId);
  $("#lock-challenge").disabled = !isInstructor || phase !== "public_practice";
  $("#evaluate-hidden").disabled =
    !isInstructor || phase !== "submission_locked";
  $("#release-results").disabled =
    !isInstructor || phase !== "hidden_evaluation";
  // Keep refresh available to signed users so another tab's instructor release can be discovered.
  $("#refresh-rankings").disabled = !currentRole;
  $("#request-feedback").disabled = !submissionId;
  $("#challenge-designer").hidden = !isInstructor;
  $("#challenge-designer-controls").disabled = !isInstructor;
  $("#draft-challenge-design").disabled =
    !isInstructor || challengeDesignOptions == null;
}

function policyPayload() {
  return {
    schema_version: "1.0",
    strategy_type: $("#strategy-type").value,
    target_participation: Number($("#target-participation").value),
    max_participation: Number($("#max-participation").value),
    max_spread_bps: Number($("#max-spread").value),
    urgency_curve: $("#urgency-curve").value,
    feed_latency_tolerance_ms: Number($("#feed-latency").value),
    cancel_after_ms: Number($("#cancel-after").value),
    completion_buffer_steps: Number($("#completion-buffer").value),
    pause_during_halt: $("#pause-halt").checked,
    pause_above_spread_limit: $("#pause-spread").checked,
    include_pending_in_budget: $("#pending-budget").checked,
    rationale: $("#rationale").value.trim(),
  };
}

function clearStaleResults(
  message = "Policy changed. Run public practice to refresh deterministic evidence.",
) {
  lastRun = null;
  $("#public-result").textContent = message;
  $("#market-chart").textContent =
    "Run the updated policy to render market evidence.";
  $("#strategy-chart").textContent =
    "Run the updated policy to render strategy evidence.";
  $("#evidence-table").textContent = "No current replay evidence.";
  $("#event-timeline").textContent = "";
  $("#replay-label").textContent = "No current run";
  renderComparison(null);
  updatePolicySummary();
}

function updatePolicySummary() {
  const policy = policyPayload();
  $("#policy-summary").innerHTML =
    `<b>${esc(policy.strategy_type.replaceAll("_", " ").toUpperCase())}</b><span>Targets ${fmt(policy.target_participation * 100)}% participation, caps at ${fmt(policy.max_participation * 100)}%, pauses above ${fmt(policy.max_spread_bps, 0)} bps, and cancels stale child orders after ${fmt(policy.cancel_after_ms, 0)} ms.</span>`;
}

function populatePolicy(policy) {
  const targetParticipation =
    policy.target_participation ?? policy.participation_rate ?? 0.08;
  const strategyType =
    policy.strategy_type ||
    (policy.strategy === "twap"
      ? "twap"
      : policy.urgency_curve === "adaptive"
        ? "adaptive_pov"
        : "pov");
  $("#strategy-type").value = strategyType;
  $("#target-participation").value = targetParticipation;
  $("#max-participation").value =
    policy.max_participation ?? targetParticipation;
  $("#max-spread").value = policy.max_spread_bps ?? 12;
  $("#feed-latency").value = policy.feed_latency_tolerance_ms ?? 10000;
  $("#cancel-after").value = policy.cancel_after_ms ?? 10000;
  $("#completion-buffer").value = policy.completion_buffer_steps ?? 0;
  $("#urgency-curve").value = policy.urgency_curve || "uniform";
  $("#pause-halt").checked = policy.pause_during_halt ?? true;
  $("#pause-spread").checked = policy.pause_above_spread_limit ?? false;
  $("#pending-budget").checked = policy.include_pending_in_budget ?? true;
  $("#rationale").value =
    policy.rationale ||
    `I selected the ${policy.name || "configured"} policy to test how its bounded participation, timing, spread, latency, cancellation, and completion controls behave in the visible synthetic exchange.`;
  updatePolicySummary();
}

function applyBenchmark(policy) {
  configuredPresetId = policy.policy_id;
  populatePolicy(policy);
  document.querySelectorAll(".policy-card").forEach((card) => {
    const selected = card.dataset.id === configuredPresetId;
    card.classList.toggle("selected", selected);
    card.setAttribute("aria-pressed", String(selected));
  });
  formInitialized = true;
  clearStaleResults();
}

function renderComparisonSelector() {
  const selector = $("#comparison-policy");
  const available = challenge.policies || [];
  if (!available.some((policy) => policy.policy_id === comparisonPolicyId)) {
    comparisonPolicyId =
      available.find((policy) => policy.policy_id === "guarded_pov")
        ?.policy_id ||
      available[0]?.policy_id ||
      null;
  }
  selector.innerHTML = available
    .map(
      (policy) =>
        `<option value="${esc(policy.policy_id)}">${esc(policy.name)}</option>`,
    )
    .join("");
  if (comparisonPolicyId) selector.value = comparisonPolicyId;
  const selected = available.find(
    (policy) => policy.policy_id === comparisonPolicyId,
  );
  $("#comparison-selection-summary").textContent = selected
    ? `${selected.name} selected`
    : "No benchmark available";
}

function renderPolicyCards() {
  const box = $("#policy-cards");
  box.innerHTML = challenge.policies
    .map((policy) => {
      const selected = policy.policy_id === configuredPresetId;
      return `<button type="button" class="policy-card ${selected ? "selected" : ""}" data-id="${esc(policy.policy_id)}" aria-pressed="${selected}"><b>${esc(policy.name)}</b><span>${esc(policy.description)}</span><small>${policy.strategy.toUpperCase()} · ${fmt(policy.participation_rate * 100, 0)}% reference target · ${policy.latency_ms}ms entry latency</small></button>`;
    })
    .join("");
  box
    .querySelectorAll("button")
    .forEach((button) =>
      button.addEventListener("click", () =>
        applyBenchmark(
          challenge.policies.find(
            (policy) => policy.policy_id === button.dataset.id,
          ),
        ),
      ),
    );
  if (!formInitialized) {
    const initialPolicy =
      challenge.policies.find(
        (policy) => policy.policy_id === configuredPresetId,
      ) || challenge.policies[0];
    if (initialPolicy) {
      configuredPresetId = initialPolicy.policy_id;
      populatePolicy(initialPolicy);
      formInitialized = true;
    }
  }
  renderComparisonSelector();
}

function renderQuality() {
  const labels = {
    PASS: "pass",
    NOT_EVALUATED: "neutral",
    NOT_CLAIMED: "warn",
  };
  $("#quality-card").innerHTML = Object.entries(challenge.quality)
    .map(
      ([key, value]) =>
        `<div><span>${esc(key.replaceAll("_", " "))}</span><b class="${labels[value] || "neutral"}">${esc(value.replaceAll("_", " "))}</b></div>`,
    )
    .join("");
  $("#stress-contract").innerHTML = challenge.stress_contract
    .map((item) => `<li>${esc(item)}</li>`)
    .join("");
  const count = challenge.hidden_worlds.count || 0;
  $("#hidden-worlds").innerHTML = Array.from(
    { length: count },
    (_, index) =>
      `<div class="hidden-world"><b>Protected test ${index + 1}</b><span>Server-side until release</span></div>`,
  ).join("");
}

function metricsGrid(metrics) {
  return `<div class="metric-grid"><div><span>Completion</span><b>${fmt(metrics.completion_pct)}%</b></div><div><span>Shortfall</span><b>${fmt(metrics.implementation_shortfall_bps)} bps</b></div><div><span>Max participation</span><b>${fmt(metrics.max_participation_pct)}%</b></div><div><span>Remaining</span><b>${metrics.remaining_inventory}</b></div><div><span>Temporary impact</span><b>${fmt(metrics.temporary_impact_bps)} bps</b></div><div><span>Inventory accounting</span><b>${metrics.inventory_accounting_ties ? "TIED" : "FAIL"}</b></div></div>`;
}

function chartPath(values, width, top, height, minimum, maximum) {
  return values
    .map((value, index) => {
      const x = (index / Math.max(1, values.length - 1)) * width;
      const y =
        top +
        height -
        ((Number(value) - minimum) / Math.max(1, maximum - minimum)) * height;
      return `${index ? "L" : "M"} ${x} ${y}`;
    })
    .join(" ");
}

function chartLegend(items) {
  return `<div class="chart-legend">${items.map((item) => `<span><i class="${item.dashed ? "dashed" : ""}" style="--swatch:${item.color}"></i>${esc(item.label)}</span>`).join("")}</div>`;
}

function renderComparison(comparison, primaryRun = null) {
  const target = $("#policy-comparison");
  const status = $("#comparison-status");
  if (!comparison) {
    status.textContent = "Awaiting paired evidence";
    target.className = "empty-state";
    target.textContent =
      "This practice run has no paired public comparison yet.";
    return;
  }
  const configuredPreset = challenge?.policies?.find(
    (policy) => policy.policy_id === configuredPresetId,
  );
  const primaryComparisonRow = primaryRun
    ? {
        name: configuredPreset
          ? `Configured ${configuredPreset.name}`
          : "Your custom configured policy",
        policy_id: primaryRun.policy?.policy_id,
        metrics: primaryRun.metrics,
        public_score: primaryRun.public_score,
        replay: primaryRun.replay,
        world: primaryRun.world,
        evidence: primaryRun.evidence,
      }
    : null;
  const candidates =
    comparison.rows ||
    comparison.policies ||
    comparison.public_runs ||
    (primaryComparisonRow
      ? [primaryComparisonRow, comparison]
      : [
          comparison.baseline || comparison.original,
          comparison.candidate || comparison.corrected,
        ].filter(Boolean));
  const rows = Array.isArray(candidates) ? candidates.slice(0, 2) : [];
  const firstEnvironmentHash = rows[0]?.world?.environment_hash;
  const secondEnvironmentHash = rows[1]?.world?.environment_hash;
  const environmentHashMatch =
    Boolean(firstEnvironmentHash) &&
    firstEnvironmentHash === secondEnvironmentHash;
  const visibleWorldMatch =
    rows.length === 2 &&
    rows[0]?.world?.variant === rows[1]?.world?.variant &&
    rows[0]?.world?.seed === rows[1]?.world?.seed;
  const sameWorld =
    rows.length === 2 && visibleWorldMatch && environmentHashMatch;
  if (!sameWorld) {
    status.textContent =
      rows.length === 2 ? "World mismatch rejected" : "Incomplete pair";
    target.className = "empty-state";
    target.textContent =
      rows.length === 2
        ? "The comparison was not rendered because variant, seed, and environment hash did not all match."
        : "The server comparison did not include two policy records.";
    return;
  }
  const card = (row, index) => {
    const metrics =
      row.metrics || row.public_metrics || row.result?.metrics || {};
    const name =
      row.name ||
      row.policy_name ||
      row.policy_id ||
      row.submission_id ||
      `Policy ${index + 1}`;
    const score = row.public_score ?? row.score ?? row.result?.public_score;
    return `<article class="comparison-card"><h4>${esc(name)}</h4><div class="metric-grid"><div><span>Public score</span><b>${score == null ? "—" : fmt(score, 2)}</b></div><div><span>Completion</span><b>${metrics.completion_pct == null ? "—" : `${fmt(metrics.completion_pct)}%`}</b></div><div><span>Shortfall</span><b>${metrics.implementation_shortfall_bps == null ? "—" : `${fmt(metrics.implementation_shortfall_bps)} bps`}</b></div><div><span>Max participation</span><b>${metrics.max_participation_pct == null ? "—" : `${fmt(metrics.max_participation_pct)}%`}</b></div><div><span>Remaining</span><b>${metrics.remaining_inventory ?? "—"}</b></div><div><span>Result hash</span><b>${esc(String(row.result_hash || row.evidence?.result_hash || "—").slice(0, 10))}</b></div></div></article>`;
  };
  const decisionEvidence = (row) => {
    const replay = row.replay || row.result?.replay || {};
    const evidenceRows = replay.evidence_rows || [];
    const orders = replay.orders || [];
    const cancels = replay.cancels || [];
    const fills = replay.strategy_trades || replay.fills || [];
    const actionRows = evidenceRows.filter(
      (item) =>
        (item.order_action || []).length || Number(item.fill_quantity) > 0,
    );
    return {
      submitted: orders.length,
      cancelled: cancels.length,
      filled: fills.length,
      firstDecision: actionRows[0]?.step ?? orders[0]?.submitted_step ?? "—",
      lastDecision: actionRows.at(-1)?.step ?? fills.at(-1)?.step ?? "—",
      remaining: row.metrics?.remaining_inventory ?? "—",
      participation:
        row.metrics?.max_participation_pct == null
          ? "—"
          : `${fmt(row.metrics.max_participation_pct)}%`,
    };
  };
  const decisions = rows.map(decisionEvidence);
  const decisionRows = [
    ["Submitted orders", "submitted"],
    ["Cancelled orders", "cancelled"],
    ["Fill records", "filled"],
    ["First decision step", "firstDecision"],
    ["Last decision step", "lastDecision"],
    ["Final remaining inventory", "remaining"],
    ["Maximum participation", "participation"],
  ];
  status.textContent = `Same public world verified · seed ${rows[0].world.seed} · environment ${firstEnvironmentHash.slice(0, 10)}`;
  target.className = "comparison-grid";
  target.innerHTML = `${rows.map(card).join("")}<div class="comparison-decisions"><h4>Paired decision evidence</h4><p class="muted">Message-lifecycle and outcome differences from the two server replays.</p><div class="table-scroll"><table class="metric-table" aria-label="Paired public decision evidence"><thead><tr><th>Measured replay evidence</th><th>${esc(rows[0].name || "Configured policy")}</th><th>${esc(rows[1].name || "Comparison benchmark")}</th></tr></thead><tbody>${decisionRows.map(([label, key]) => `<tr><th>${esc(label)}</th><td>${esc(decisions[0][key])}</td><td>${esc(decisions[1][key])}</td></tr>`).join("")}</tbody></table></div></div><div class="callout comparison-contract"><b>Comparison contract</b><span>Both policies used public variant ${esc(rows[0].world.variant)} and seed ${esc(rows[0].world.seed)} with matching environment hash ${esc(firstEnvironmentHash)}. Only the policy changed.</span></div>`;
}

function renderReplay(run) {
  const frames = run.replay?.timeline || [];
  const evidence = run.replay?.evidence_rows || [];
  const points = frames.map((frame) => ({
    step: frame.step,
    mid: frame.asset_states.NOVA.mid_ticks,
    bid: frame.asset_states.NOVA.best_bid_ticks,
    ask: frame.asset_states.NOVA.best_ask_ticks,
    bidDepth: frame.asset_states.NOVA.bid_depth,
    askDepth: frame.asset_states.NOVA.ask_depth,
    depth:
      frame.asset_states.NOVA.bid_depth + frame.asset_states.NOVA.ask_depth,
  }));
  if (!points.length) {
    $("#market-chart").textContent =
      "The run returned no market replay frames.";
    $("#strategy-chart").textContent =
      "The run returned no synchronized strategy frames.";
    return;
  }

  const width = 640;
  const priceTop = 10,
    priceHeight = 125,
    depthTop = 165,
    depthHeight = 55;
  const prices = points.flatMap((point) => [
    point.bid ?? point.mid,
    point.ask ?? point.mid,
  ]);
  const minimum = Math.min(...prices),
    maximum = Math.max(...prices);
  const depths = points.map((point) => point.depth);
  const maxDepth = Math.max(1, ...depths);
  const midPath = chartPath(
    points.map((point) => point.mid),
    width,
    priceTop,
    priceHeight,
    minimum,
    maximum,
  );
  const bidPath = chartPath(
    points.map((point) => point.bid ?? point.mid),
    width,
    priceTop,
    priceHeight,
    minimum,
    maximum,
  );
  const askPath = chartPath(
    points.map((point) => point.ask ?? point.mid),
    width,
    priceTop,
    priceHeight,
    minimum,
    maximum,
  );
  const depthPath = chartPath(
    depths,
    width,
    depthTop,
    depthHeight,
    0,
    maxDepth,
  );
  const depthArea = `${depthPath} L ${width} ${depthTop + depthHeight} L 0 ${depthTop + depthHeight} Z`;
  $("#market-chart").innerHTML =
    `<svg viewBox="0 0 ${width} 235" role="img" aria-label="NOVA bid ask mid-price and displayed depth over time"><line x1="0" y1="150" x2="${width}" y2="150" stroke="#dfe5ec"/><path d="${depthArea}" fill="#dff3ee" opacity=".8"/><path d="${depthPath}" fill="none" stroke="#148963" stroke-width="2"/><path d="${bidPath}" fill="none" stroke="#8aa5d8" stroke-width="1"/><path d="${askPath}" fill="none" stroke="#8aa5d8" stroke-width="1"/><path d="${midPath}" fill="none" stroke="#2667ff" stroke-width="3"/><text x="4" y="160" fill="#687482" font-size="10">DISPLAYED DEPTH</text></svg>${chartLegend(
      [
        { label: "Mid price", color: "#2667ff" },
        { label: "Bid / ask", color: "#8aa5d8" },
        { label: "Displayed depth", color: "#148963" },
      ],
    )}<p>Mid ${minimum}–${maximum} ticks · depth ${Math.min(...depths)}–${maxDepth} displayed shares · final depth ${points.at(-1).depth}.</p>`;

  const parent = Number(run.policy?.parent_quantity || 6000);
  const activityByStep = new Map(
    (run.replay.strategy_activity || []).map((row) => [row.step, row]),
  );
  const strategyPoints = evidence.length
    ? evidence.map((row) => {
        const activity = activityByStep.get(row.step) || {};
        const remaining = Number(
          row.remaining_inventory ??
            activity.remaining_parent_quantity ??
            parent,
        );
        return {
          step: Number(row.step),
          remaining,
          filled: Number(
            activity.filled_inventory ?? Math.max(0, parent - remaining),
          ),
          participation: Number(
            row.participation_pct ?? 100 * Number(activity.participation || 0),
          ),
          participationLimit: Number(
            row.participation_limit_pct ??
              run.metrics?.participation_limit_pct ??
              0,
          ),
        };
      })
    : points.map((point) => ({
        step: point.step,
        remaining: parent,
        filled: 0,
        participation: 0,
        participationLimit: Number(run.metrics?.participation_limit_pct || 0),
      }));
  const quantityTop = 10,
    quantityHeight = 125,
    participationTop = 165,
    participationHeight = 55;
  const maxStep = Math.max(1, ...strategyPoints.map((point) => point.step));
  const maxParticipation = Math.max(
    1,
    ...strategyPoints.flatMap((point) => [
      point.participation,
      point.participationLimit,
    ]),
  );
  const remainingPath = chartPath(
    strategyPoints.map((point) => point.remaining),
    width,
    quantityTop,
    quantityHeight,
    0,
    parent,
  );
  const filledPath = chartPath(
    strategyPoints.map((point) => point.filled),
    width,
    quantityTop,
    quantityHeight,
    0,
    parent,
  );
  const participationPath = chartPath(
    strategyPoints.map((point) => point.participation),
    width,
    participationTop,
    participationHeight,
    0,
    maxParticipation,
  );
  const limitPath = chartPath(
    strategyPoints.map((point) => point.participationLimit),
    width,
    participationTop,
    participationHeight,
    0,
    maxParticipation,
  );
  const markerPosition = (step, remaining) => ({
    x: (Number(step) / maxStep) * width,
    y:
      quantityTop +
      quantityHeight -
      (Number(remaining) / parent) * quantityHeight,
  });
  const rowByStep = new Map(strategyPoints.map((point) => [point.step, point]));
  const fillMarkers = evidence
    .filter((row) => Number(row.fill_quantity) > 0)
    .map((row) => {
      const point = markerPosition(row.step, row.remaining_inventory);
      return `<circle class="fill-marker" data-evidence-marker="fill" cx="${point.x}" cy="${point.y}" r="4" fill="#15a66d"><title>Fill at step ${row.step}: ${row.fill_quantity} shares</title></circle>`;
    })
    .join("");
  const orderMarkers = (run.replay.orders || [])
    .map((order) => {
      const row = rowByStep.get(Number(order.submitted_step));
      if (!row) return "";
      const point = markerPosition(order.submitted_step, row.remaining);
      return `<path class="order-marker" data-evidence-marker="order" d="M ${point.x - 4} ${point.y + 6} L ${point.x} ${point.y - 3} L ${point.x + 4} ${point.y + 6} Z" fill="#2667ff"><title>Submit ${esc(order.order_id)} at step ${order.submitted_step}</title></path>`;
    })
    .join("");
  const cancelMarkers = (run.replay.cancels || [])
    .map((cancel) => {
      const row = rowByStep.get(Number(cancel.effective_step));
      if (!row) return "";
      const point = markerPosition(cancel.effective_step, row.remaining);
      return `<path class="cancel-marker" data-evidence-marker="cancel" d="M ${point.x - 4} ${point.y - 4} L ${point.x + 4} ${point.y + 4} M ${point.x + 4} ${point.y - 4} L ${point.x - 4} ${point.y + 4}" stroke="#d04b44" stroke-width="2"><title>Cancel ${esc(cancel.order_id)} at step ${cancel.effective_step}</title></path>`;
    })
    .join("");
  $("#strategy-chart").innerHTML =
    `<svg viewBox="0 0 ${width} 235" role="img" aria-label="Remaining and filled parent quantity, participation and limit, with submit fill and cancel markers"><line x1="0" y1="150" x2="${width}" y2="150" stroke="#dfe5ec"/><path d="${remainingPath}" fill="none" stroke="#6b3df5" stroke-width="3"/><path d="${filledPath}" fill="none" stroke="#15a66d" stroke-width="3"/><path d="${participationPath}" fill="none" stroke="#e78b18" stroke-width="2"/><path d="${limitPath}" fill="none" stroke="#d04b44" stroke-width="2" stroke-dasharray="7 5"/>${orderMarkers}${fillMarkers}${cancelMarkers}<text x="4" y="160" fill="#687482" font-size="10">PARTICIPATION %</text></svg>${chartLegend(
      [
        { label: "Remaining", color: "#6b3df5" },
        { label: "Filled", color: "#15a66d" },
        { label: "Participation", color: "#e78b18" },
        { label: "Participation limit", color: "#d04b44", dashed: true },
        { label: "Submit marker", color: "#2667ff" },
        { label: "Cancel marker", color: "#d04b44" },
      ],
    )}<p>${(run.replay.strategy_trades || []).length} strategy trades · max measured participation ${fmt(run.metrics.max_participation_pct)}% · configured limit ${fmt(run.metrics.participation_limit_pct)}%.</p>`;

  const events = (run.replay.events || []).filter(
    (event) => event.asset === "NOVA" || event.scope === "market",
  );
  $("#event-timeline").innerHTML = events.length
    ? events
        .slice(0, 12)
        .map(
          (event) =>
            `<span>Step ${event.step}: ${esc(String(event.type).replaceAll("_", " "))}</span>`,
        )
        .join("")
    : "<span>No scheduled public event in this practice world.</span>";
  $("#evidence-table").innerHTML =
    `<table class="metric-table"><thead><tr><th>Step</th><th>Event</th><th>Bid</th><th>Ask</th><th>Spread</th><th>Depth</th><th>Volume</th><th>Action</th><th>Order qty</th><th>Fill qty</th><th>Remaining</th><th>Participation</th><th>Limit</th><th>Shortfall</th></tr></thead><tbody>${evidence.map((row) => `<tr><td>${row.step}</td><td>${esc(row.market_event || "")}</td><td>${row.best_bid_ticks ?? ""}</td><td>${row.best_ask_ticks ?? ""}</td><td>${row.spread_ticks ?? ""}</td><td>${row.displayed_depth ?? ""}</td><td>${row.observed_volume ?? ""}</td><td>${esc((row.order_action || []).join(" · "))}</td><td>${row.order_quantity ?? 0}</td><td>${row.fill_quantity ?? 0}</td><td>${row.remaining_inventory ?? ""}</td><td>${fmt(row.participation_pct)}%</td><td>${fmt(row.participation_limit_pct)}%</td><td>${fmt(row.shortfall_contribution_bps, 3)} bps</td></tr>`).join("")}</tbody></table>`;
  $("#replay-label").textContent =
    `Seed ${run.world.seed} · ${run.evidence.result_hash.slice(0, 10)}`;
}

async function runPublic() {
  const button = $("#run-public");
  setBusy(button, true, "Running exchange…");
  try {
    lastRun = await post(`${challengePath}/practice`, {
      policy: policyPayload(),
      comparison_policy_id: comparisonPolicyId,
      seed: 42,
    });
    $("#public-result").innerHTML =
      `<div class="result passbox"><b>Public practice complete.</b> Score ${fmt(lastRun.public_score, 2)}.${metricsGrid(lastRun.metrics)}</div>`;
    practiceRemaining = lastRun.practice_runs_remaining;
    $("#practice-limit").textContent = `${practiceRemaining} runs remaining`;
    renderReplay(lastRun);
    renderComparison(lastRun.comparison, lastRun);
    $("#explanation").innerHTML =
      `<b>Deterministic public evidence:</b> completion ${fmt(lastRun.metrics.completion_pct)}%, shortfall ${fmt(lastRun.metrics.implementation_shortfall_bps)} bps, and maximum measured participation ${fmt(lastRun.metrics.max_participation_pct)}%. <p>Protected-world claims remain withheld.</p>`;
  } catch (error) {
    $("#public-result").innerHTML =
      `<div class="result failbox">${esc(error.message)}</div>`;
  } finally {
    setBusy(button, false, "");
    applyLifecycleControls();
  }
}

async function submitFinal() {
  const button = $("#submit-final");
  setBusy(button, true, "Submitting…");
  try {
    const result = await post(`${challengePath}/submissions`, {
      policy: policyPayload(),
    });
    submissionId = result.submission_id;
    $("#submission-status").textContent =
      `Final submission ${submissionId.slice(-8)} saved · public score ${fmt(result.public_score, 2)}`;
    button.disabled = true;
    applyLifecycleControls();
  } catch (error) {
    $("#submission-status").textContent = error.message;
  } finally {
    setBusy(button, false, "");
    applyLifecycleControls();
  }
}

async function saveDraft() {
  const button = $("#save-draft");
  setBusy(button, true, "Saving…");
  try {
    const result = await post(`${challengePath}/drafts`, {
      policy: policyPayload(),
    });
    $("#submission-status").textContent =
      `Draft ${result.submission_id.slice(-8)} saved. You may continue editing.`;
  } catch (error) {
    $("#submission-status").textContent = error.message;
  } finally {
    setBusy(button, false, "");
    applyLifecycleControls();
  }
}

function benchmarkTable(rows) {
  return `<table class="metric-table"><thead><tr><th>Robust rank</th><th>Policy</th><th>Public rank</th><th>Public score</th><th>Hidden mean shortfall</th><th>Worst shortfall</th><th>Robustness</th></tr></thead><tbody>${rows.map((row) => `<tr><td><b>${row.robustness_rank ?? "—"}</b></td><td>${esc(row.name)}</td><td>${row.public_rank}</td><td>${fmt(row.public_score, 2)}</td><td>${row.hidden_mean_shortfall_bps == null ? "withheld" : `${fmt(row.hidden_mean_shortfall_bps)} bps`}</td><td>${row.hidden_worst_shortfall_bps == null ? "withheld" : `${fmt(row.hidden_worst_shortfall_bps)} bps`}</td><td><b>${row.robustness_score == null ? "withheld" : fmt(row.robustness_score, 2)}</b></td></tr>`).join("")}</tbody></table>`;
}

function resetWorldHeatmap(
  message = "The policy-by-world heatmap appears only after release from an authorized instructor response.",
) {
  const target = $("#world-heatmap");
  target.className = "heatmap-shell empty-state";
  target.textContent = message;
}

function renderWorldHeatmap(matrix) {
  if (currentRole !== "instructor") {
    resetWorldHeatmap(
      "Released aggregate rankings are visible. World-level evidence remains instructor-only.",
    );
    return;
  }
  if (!matrix.released) {
    resetWorldHeatmap(
      "World-level evidence is authorized for this instructor, but the heatmap remains sealed until release.",
    );
    return;
  }
  const sourceRows = (matrix.rows || []).filter(
    (row) => Array.isArray(row.world_results) && row.world_results.length,
  );
  if (!sourceRows.length) {
    resetWorldHeatmap(
      "Released aggregate rankings are visible. World-level evidence remains instructor-only.",
    );
    return;
  }
  const worldLabels = [];
  for (const row of sourceRows) {
    for (const world of row.world_results) {
      const label = world.variant || world.world_id;
      if (label && !worldLabels.includes(label)) worldLabels.push(label);
    }
  }
  const aggregates = sourceRows.map((row) => {
    const values = {};
    for (const label of worldLabels) {
      const samples = row.world_results.filter(
        (world) => (world.variant || world.world_id) === label,
      );
      const shortfalls = samples
        .map((world) => Number(world.metrics?.implementation_shortfall_bps))
        .filter(Number.isFinite);
      const completions = samples
        .map((world) => Number(world.metrics?.completion_pct))
        .filter(Number.isFinite);
      values[label] = shortfalls.length
        ? {
            shortfall:
              shortfalls.reduce((sum, value) => sum + value, 0) /
              shortfalls.length,
            completion: completions.length
              ? completions.reduce((sum, value) => sum + value, 0) /
                completions.length
              : null,
          }
        : null;
    }
    const measured = Object.entries(values).filter(
      ([, value]) => value != null,
    );
    const worst =
      measured.sort(
        (left, right) => right[1].shortfall - left[1].shortfall,
      )[0] || null;
    return {
      name: row.name || row.policy_id,
      robustness: row.robustness_score,
      values,
      worst,
    };
  });
  const finiteValues = aggregates
    .flatMap((row) =>
      Object.values(row.values).map((value) => value?.shortfall),
    )
    .filter(Number.isFinite);
  const minimum = finiteValues.length ? Math.min(...finiteValues) : 0;
  const maximum = finiteValues.length ? Math.max(...finiteValues) : 1;
  const cell = (value, isWorst) => {
    if (!value || !Number.isFinite(value.shortfall))
      return '<td aria-label="No world metric">—</td>';
    const normalized =
      (value.shortfall - minimum) / Math.max(1e-9, maximum - minimum);
    const hue = Math.round(120 * (1 - normalized));
    const completion = Number.isFinite(value.completion)
      ? `${fmt(value.completion)}% complete`
      : "completion unavailable";
    return `<td style="--heat-hue:${hue}" aria-label="${fmt(value.shortfall)} basis points implementation shortfall, ${completion}${isWorst ? ", worst world" : ""}"><strong>${fmt(value.shortfall)} bps</strong><span>${completion}</span>${isWorst ? '<span class="worst-badge">Worst world</span>' : ""}</td>`;
  };
  const matrixHash = matrix.provenance?.matrix_hash || matrix.matrix_hash;
  const target = $("#world-heatmap");
  target.className = "heatmap-shell";
  target.innerHTML = `<h4>Released policy × protected-world outcomes</h4><p class="muted">Cells show mean implementation shortfall and completion across deterministic seeds. Green is lower shortfall; the worst shortfall world is labeled.${matrixHash ? ` Matrix ${esc(matrixHash.slice(0, 12))}.` : ""}</p><table class="heatmap"><thead><tr><th>Policy</th><th>Robustness score</th>${worldLabels.map((label) => `<th>${esc(String(label).replaceAll("_", " "))}</th>`).join("")}<th>Worst-world status</th></tr></thead><tbody>${aggregates.map((row) => `<tr><td class="policy-label">${esc(row.name)}</td><td class="robustness-cell"><strong>${row.robustness == null ? "—" : fmt(row.robustness, 2)}</strong></td>${worldLabels.map((label) => cell(row.values[label], row.worst?.[0] === label)).join("")}<td class="worst-cell">${row.worst ? `<strong>${esc(String(row.worst[0]).replaceAll("_", " "))}</strong><span>${fmt(row.worst[1].shortfall)} bps · ${row.worst[1].completion == null ? "completion unavailable" : `${fmt(row.worst[1].completion)}% complete`}</span>` : "—"}</td></tr>`).join("")}</tbody></table>`;
}

function resetMeasuredQuality(
  message = "Measured challenge-behavior checks appear here for an authorized instructor after evaluation.",
) {
  const target = $("#measured-quality");
  target.className = "quality-evidence empty-state";
  target.textContent = message;
}

function renderMeasuredQuality(matrix) {
  if (currentRole !== "instructor") {
    resetMeasuredQuality(
      "Measured protected-world quality evidence remains instructor-only.",
    );
    return;
  }
  const behavior = matrix.provenance?.quality?.challenge_behavior;
  if (!behavior || !Array.isArray(behavior.checks) || !behavior.checks.length) {
    resetMeasuredQuality(
      "This evaluation did not return measured challenge-behavior checks.",
    );
    return;
  }
  const observation = (check) =>
    Object.entries(check)
      .filter(([key]) => !["id", "passed"].includes(key))
      .map(
        ([key, value]) =>
          `<span><b>${esc(key.replaceAll("_", " "))}:</b> ${esc(typeof value === "object" ? JSON.stringify(value) : value)}</span>`,
      )
      .join("");
  const target = $("#measured-quality");
  target.className = "quality-evidence";
  target.innerHTML = `<h4>Measured challenge-behavior evidence · ${esc(behavior.status || "UNKNOWN")}</h4><p class="muted">${esc(behavior.definition || "Each check is derived from persisted deterministic run evidence.")}</p><div class="quality-checks">${behavior.checks.map((check) => `<div class="quality-check"><code>${esc(check.id)}</code><b class="${check.passed ? "check-pass" : "check-fail"}">${check.passed ? "PASS" : "FAIL"}</b><div class="quality-observations">${observation(check)}</div></div>`).join("")}</div>`;
}

async function lifecycleAction(action, label) {
  const button = $(`#${action}`);
  setBusy(button, true, label);
  try {
    const result = await post(
      `${challengePath}/${action === "evaluate-hidden" ? "evaluate" : action === "lock-challenge" ? "lock" : "release"}`,
      action === "evaluate-hidden"
        ? {}
        : { reason: `Demo instructor ${action.replaceAll("-", " ")}` },
    );
    $("#instructor-status").textContent =
      `${action.replaceAll("-", " ")} complete${result.matrix_hash ? ` · matrix ${result.matrix_hash.slice(0, 12)}` : ""}`;
    await loadChallenge();
    if (action !== "lock-challenge") await refreshRankings();
  } catch (error) {
    $("#instructor-status").textContent = error.message;
  } finally {
    setBusy(button, false, "");
    applyLifecycleControls();
  }
}

async function refreshRankings() {
  const button = $("#refresh-rankings");
  setBusy(button, true, "Loading…");
  resetWorldHeatmap("Loading an authorized leaderboard response…");
  resetMeasuredQuality("Loading measured challenge-behavior evidence…");
  try {
    const matrix = await get(`${challengePath}/leaderboard/hidden`);
    $("#benchmark-result").innerHTML =
      `${benchmarkTable(matrix.rows)}<div class="callout"><b>Public rank → robustness rank</b><span>The reversal is derived from measured multi-world results. Release exposes this summary while raw world hashes and evidence remain instructor-only.</span></div>`;
    renderWorldHeatmap(matrix);
    renderMeasuredQuality(matrix);
  } catch (error) {
    $("#benchmark-result").innerHTML =
      `<div class="result failbox">${esc(error.message)}</div>`;
    resetWorldHeatmap();
    resetMeasuredQuality(
      "Measured quality evidence is unavailable for this session and lifecycle phase.",
    );
  } finally {
    setBusy(button, false, "");
    applyLifecycleControls();
  }
}

async function startSession(role) {
  const message = $("#session-message");
  try {
    const body = { role };
    if (role === "instructor")
      body.instructor_code = $("#instructor-code").value;
    const session = await post("/api/arena/demo-session", body);
    currentRole = role;
    currentUser = session.user_id;
    submissionId = null;
    practiceRemaining = null;
    resetWorldHeatmap();
    resetMeasuredQuality();
    $("#session-status").textContent =
      `${role[0].toUpperCase()}${role.slice(1)} demo`;
    message.textContent = `${role} demo session active. This signed cookie is prototype authentication, not institutional SSO.`;
    $("#challenge-design-status").textContent =
      role === "instructor"
        ? "Ready for instructor-owned qualitative constraints."
        : "Instructor session required.";
    await loadChallenge();
    if (role === "student") {
      clearChallengeDesignOptions();
      await restoreSubmission();
    } else {
      await loadChallengeDesignOptions();
      if (["hidden_evaluation", "released"].includes(challenge.phase)) {
        await refreshRankings();
      }
    }
    applyLifecycleControls();
  } catch (error) {
    message.textContent = `Demo authentication unavailable: ${error.message}`;
  }
}

async function restoreSubmission() {
  const saved = await get(`${challengePath}/submissions/me`);
  practiceRemaining = saved.practice_runs_remaining;
  $("#practice-limit").textContent = `${practiceRemaining} runs remaining`;
  const restored = saved.final || saved.latest_draft;
  if (restored?.policy) {
    populatePolicy(restored.policy);
    configuredPresetId = null;
    formInitialized = true;
    document.querySelectorAll(".policy-card").forEach((card) => {
      card.classList.remove("selected");
      card.setAttribute("aria-pressed", "false");
    });
  }
  if (saved.final) {
    submissionId = saved.final.submission_id;
    $("#submission-status").textContent =
      `Final submission ${submissionId.slice(-8)} restored · public score ${fmt(saved.final.public_score, 2)}`;
  } else if (saved.latest_draft) {
    submissionId = null;
    $("#submission-status").textContent =
      `Draft ${saved.latest_draft.submission_id.slice(-8)} restored with its saved policy. You may continue editing.`;
  } else {
    submissionId = null;
    $("#submission-status").textContent = "No final submission yet.";
  }
  applyLifecycleControls();
}

async function restoreSession() {
  try {
    const session = await get("/api/arena/session");
    if (!session.authenticated) throw new Error("no signed session");
    currentRole = session.role;
    currentUser = session.user_id;
    $("#session-status").textContent =
      `${session.role[0].toUpperCase()}${session.role.slice(1)} demo`;
    $("#session-message").textContent =
      `${session.role} demo session restored from the signed server session.`;
    $("#challenge-design-status").textContent =
      session.role === "instructor"
        ? "Ready for instructor-owned qualitative constraints."
        : "Instructor session required.";
    if (session.role === "student") {
      clearChallengeDesignOptions();
      await restoreSubmission();
    } else {
      await loadChallengeDesignOptions();
      if (["hidden_evaluation", "released"].includes(challenge.phase)) {
        await refreshRankings();
      }
    }
  } catch (_error) {
    currentRole = null;
    currentUser = null;
    submissionId = null;
    clearChallengeDesignOptions();
    $("#session-status").textContent = "No signed session";
    $("#session-message").textContent =
      "Choose a demo role to create a signed prototype session.";
    $("#challenge-design-status").textContent = "Instructor session required.";
  }
  applyLifecycleControls();
}

async function requestFeedback() {
  const button = $("#request-feedback");
  if (!submissionId) {
    $("#explanation").textContent =
      "Submit a final policy before requesting feedback.";
    return;
  }
  setBusy(button, true, "Validating evidence…");
  try {
    const result = await post(
      `/api/arena/execution/submissions/${submissionId}/feedback`,
      {},
    );
    if (result.status === "withheld") {
      $("#explanation").innerHTML =
        `<b>Feedback withheld</b><p>${esc(result.message)}</p><small>No model was called.</small>`;
      return;
    }
    const feedback = result.feedback || result;
    const statements = [
      ...(feedback.statements || []),
      ...(feedback.public_strengths || []),
      ...(feedback.hidden_failures || []),
    ];
    const noKey =
      result.reason === "missing_api_key" ||
      (result.mode === "deterministic_fallback" && result.model == null);
    const source =
      result.mode === "deterministic_fallback"
        ? `Deterministic fallback${noKey ? " · no OpenAI API key" : ` · ${result.reason || "model output unavailable"}`}`
        : `GPT-5.6 structured output · ${result.model || "configured model"}`;
    const statementMarkup = statements.length
      ? statements
          .map(
            (statement) =>
              `<article class="evidence-statement"><p>${esc(statement.statement || "")}</p><div class="evidence-ids"><b>Evidence IDs</b>${(statement.evidence_ids || []).map((evidenceId) => `<code>${esc(evidenceId)}</code>`).join("")}</div>${statement.metric_names?.length ? `<small>Metrics: ${statement.metric_names.map(esc).join(" · ")}</small>` : ""}</article>`,
          )
          .join("")
      : '<p class="muted">No grounded quantitative statements were returned.</p>';
    $("#explanation").innerHTML =
      `<div id="feedback-source" class="feedback-source"><b>Source: ${esc(source)}</b><span>${esc(result.generated_by || "unknown generator")} · ${esc(result.evidence_scope || "validated evidence package")}${result.recovered_from_sqlite ? " · recovered from SQLite" : ""}</span></div><h4>${esc(result.mode === "deterministic_fallback" ? "Deterministic explanation" : "GPT-5.6 evidence analysis")}</h4><p>${esc(feedback.summary || feedback.message || "")}</p><div class="feedback-statements">${statementMarkup}</div><p>${esc(feedback.why_public_rank_changed || "")}</p><small>Scores and ranks remain deterministic; every quantitative statement above is bound to the displayed evidence IDs.</small>`;
  } catch (error) {
    $("#explanation").innerHTML =
      `<div class="result failbox">${esc(error.message)}</div>`;
  } finally {
    setBusy(button, false, "");
  }
}

function clearChallengeDesignOptions() {
  challengeDesignOptions = null;
  $("#design-intervention-options").textContent =
    "Available only after authenticated instructor authorization.";
  $("#design-parameter-options").textContent =
    "Available only after authenticated instructor authorization.";
}

function renderDesignOptionGroup(targetSelector, name, options, defaultCount) {
  const target = $(targetSelector);
  target.innerHTML = options
    .map((option, index) => {
      const record =
        typeof option === "string"
          ? { id: option, label: option.replaceAll("_", " ") }
          : option;
      return `<label><input type="checkbox" name="${esc(name)}" value="${esc(record.id)}"${index < defaultCount ? " checked" : ""}> ${esc(record.label)}</label>`;
    })
    .join("");
}

async function loadChallengeDesignOptions() {
  const status = $("#challenge-design-status");
  status.textContent = "Loading authorized instructor allow-lists…";
  try {
    const options = await get("/api/arena/execution/challenge-design-options");
    challengeDesignOptions = options;
    renderDesignOptionGroup(
      "#design-intervention-options",
      "design-intervention",
      options.allowed_world_interventions || [],
      2,
    );
    renderDesignOptionGroup(
      "#design-parameter-options",
      "design-parameter",
      options.allowed_policy_parameters || [],
      5,
    );
    status.textContent =
      "Authorized instructor allow-lists loaded. No numeric worlds were disclosed or created.";
  } catch (error) {
    clearChallengeDesignOptions();
    status.textContent = `Instructor design options unavailable: ${error.message}`;
  }
  applyLifecycleControls();
}

function selectedDesignValues(name) {
  return Array.from(
    document.querySelectorAll(`input[name="${name}"]:checked`),
  ).map((input) => input.value);
}

function challengeDesignPayload() {
  return {
    course_level: $("#design-course-level").value,
    learning_objective: $("#design-learning-objective").value.trim(),
    exchange_capabilities: challengeDesignOptions?.exchange_capabilities || [],
    allowed_world_interventions: selectedDesignValues("design-intervention"),
    allowed_policy_parameters: selectedDesignValues("design-parameter"),
    difficulty: $("#design-difficulty").value,
  };
}

function renderChallengeDesign(result) {
  const design = result.design || {};
  const list = (items) =>
    `<ul>${(items || []).map((item) => `<li>${esc(item)}</li>`).join("")}</ul>`;
  const intents = (design.hidden_test_intents || [])
    .map(
      (intent) =>
        `<li><b>${esc(String(intent.intervention_id).replaceAll("_", " "))}</b> · ${esc(intent.severity_band)}<br>${esc(intent.educational_purpose)}<br><span class="muted">${esc(intent.rationale)}</span></li>`,
    )
    .join("");
  const target = $("#challenge-design-result");
  target.className = "design-draft";
  target.innerHTML = `<div class="callout"><b>Authority: ${esc(result.approval_status || "draft")} only · numeric worlds created = ${esc(String(Boolean(result.numeric_worlds_created)))}</b><span>${esc(result.world_construction_authority || "deterministic_application_code")} remains authoritative. Mode: ${esc(result.mode || "unknown")}${result.message ? ` · ${esc(result.message)}` : ""}</span></div><h4>${esc(design.title || "Untitled qualitative draft")}</h4><p>${esc(design.student_brief || "")}</p><div class="design-draft-grid"><section><b>Learning objectives</b>${list(design.learning_objectives)}</section><section><b>Public-world narrative</b><p>${esc(design.public_world_narrative || "")}</p></section><section><b>Approved hidden-test intents</b><ul>${intents}</ul></section><section><b>Expected misconceptions</b>${list(design.expected_misconceptions)}</section><section><b>Instructor rubric</b>${list(design.instructor_rubric)}</section><section><b>Limitations</b>${list(design.limitations)}</section></div>`;
}

async function draftChallengeDesign() {
  const button = $("#draft-challenge-design");
  const status = $("#challenge-design-status");
  const payload = challengeDesignPayload();
  if (
    !payload.allowed_world_interventions.length ||
    !payload.allowed_policy_parameters.length
  ) {
    status.textContent =
      "Select at least one approved intervention and one approved policy parameter.";
    return;
  }
  setBusy(button, true, "Drafting structured design…");
  status.textContent =
    "Validating instructor constraints and structured output…";
  try {
    const result = await post(
      "/api/arena/execution/challenge-designs",
      payload,
    );
    renderChallengeDesign(result);
    status.textContent = `Qualitative ${result.approval_status || "draft"} ${result.design_id || ""} saved · no numeric worlds created.`;
  } catch (error) {
    $("#challenge-design-result").innerHTML =
      `<div class="result failbox">${esc(error.message)}</div>`;
    status.textContent = `Challenge design unavailable: ${error.message}`;
  } finally {
    setBusy(button, false, "");
    applyLifecycleControls();
  }
}

async function loadChallenge() {
  challenge = await get(challengePath);
  $("#challenge-title").textContent = challenge.title;
  $("#challenge-objective").textContent = challenge.objective;
  $("#phase").textContent = challenge.phase.replaceAll("_", " ");
  if (practiceRemaining == null)
    practiceRemaining = challenge.practice_policy.maximum_runs;
  $("#practice-limit").textContent = `${practiceRemaining} runs available`;
  renderQuality();
  renderPolicyCards();
  updatePolicySummary();
  applyLifecycleControls();
}

async function load() {
  try {
    await loadChallenge();
    await restoreSession();
  } catch (error) {
    $("#challenge-title").textContent = "Unable to load execution challenge";
    $("#challenge-objective").textContent = error.message;
  }
}

$("#policy-form").addEventListener("input", () => {
  configuredPresetId = null;
  document.querySelectorAll(".policy-card").forEach((card) => {
    card.classList.remove("selected");
    card.setAttribute("aria-pressed", "false");
  });
  clearStaleResults();
});
$("#comparison-policy").addEventListener("change", (event) => {
  comparisonPolicyId = event.target.value;
  const selected = challenge?.policies?.find(
    (policy) => policy.policy_id === comparisonPolicyId,
  );
  $("#comparison-selection-summary").textContent = selected
    ? `${selected.name} selected`
    : "No benchmark available";
  clearStaleResults(
    "Comparison benchmark changed. Run public practice to refresh paired evidence.",
  );
});
$("#run-public").addEventListener("click", runPublic);
$("#save-draft").addEventListener("click", saveDraft);
$("#submit-final").addEventListener("click", submitFinal);
$("#lock-challenge").addEventListener("click", () =>
  lifecycleAction("lock-challenge", "Locking…"),
);
$("#evaluate-hidden").addEventListener("click", () =>
  lifecycleAction("evaluate-hidden", "Evaluating…"),
);
$("#release-results").addEventListener("click", () =>
  lifecycleAction("release-results", "Releasing…"),
);
$("#refresh-rankings").addEventListener("click", refreshRankings);
$("#student-session").addEventListener("click", () => startSession("student"));
$("#instructor-session").addEventListener("click", () =>
  startSession("instructor"),
);
$("#request-feedback").addEventListener("click", requestFeedback);
$("#draft-challenge-design").addEventListener("click", draftChallengeDesign);
$("#jump-to-policy").addEventListener("click", () =>
  $("#policy-section").scrollIntoView({ behavior: "smooth" }),
);
load();
