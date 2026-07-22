# Research Brief — Break-Test Engine Upgrade

Target file: `app/break_test/RESEARCH_BRIEF.md`  
Date: 2026-07-21  
Scope: State-of-the-art references, formulas, and open-source adoptable patterns for the forward-test / synthetic-market platform at `app/break_test/`.

---

## 0. Current State (What We Already Have)

| File | What it does | Gap vs. SOTA |
|---|---|---|
| `app/break_test/regimes.py` | 4 hand-tuned regimes with simple GBM + mean-reversion shock | No stochastic vol, no jumps, no factor model, no Markov-regime transition matrix |
| `app/break_test/exchange_fwd.py` | Runs `run_simulation` for each world and calculates P&L from mid-prices | Does **not** submit the strategy's orders to the exchange — still computes positions on post-hoc mid-prices |
| `app/simulation.py` | Discrete-event CDA order book, price-time priority, latency profiles, strategy observations | Needs regression to real micro models (spread, impact, latency queue models) |
| `app/schemas/world.py` + `app/agents/behaviors.py` | Strong typed world spec; 7 agent populations including execution agent | Execution algo is primitive TWAP/POV only |
| `app/break_test/metrics.py` | Sharpe, Sortino, Calmar, VaR, CVaR, win-rate | Missing Deflated Sharpe, PSR, CPCV |

**Bottom line for this brief:** We need a realistic price generator, a genuine order-to-book execution harness, rigorous validation, free data sourcing, and awareness of the competitive landscape.

---

## 1. Synthetic Market Generation (Regime Switching, Jumps, Stochastic Vol, Factor Models)

### 1.1 Core Models to Adopt

#### 1.1.1 Regime-Switching Heston + Merton Jump Diffusion
The most practical upgrade path from the current hand-tuned regime dict is a **Markov Regime-Switching** generator where each regime carries its own:

```
Regime state s_t ∈ {1,...,M}
Transition matrix Q = [q_ij] where q_ij = P(s_{t+1}=j | s_t=i)
```

**Heston SDE under regime s:**
```
dS_t = μ(s_t) S_t dt + sqrt(v_t) S_t dW_t^S
dv_t = κ(s_t) [θ(s_t) - v_t] dt + ξ(s_t) sqrt(v_t) dW_t^v
d⟨W^S, W^v⟩_t = ρ(s_t) dt
```

Where:
- `κ` = mean-reversion speed of variance
- `θ` = long-run variance
- `ξ` = vol-of-vol
- `ρ` = correlation between asset and variance Brownian motions (typically negative)
- Under Feller (`2κθ > ξ²`), `v_t` stays positive

**Merton Jump Diffusion on top:**
```
dS_t = μ S_t dt + sqrt(v_t) S_t dW_t^S + (Y-1) S_t dN_t
```
with `N_t` a Poisson process of intensity `λ(s_t)` and lognormal jump sizes `Y ~ LogN(μ_J, σ_J²)`.

Generate via **Euler-Maruyama + Poisson sampling** with full-truncation:
```python
# Pseudocode pattern from codearmo.com + quantlib-style
v_t = max(v_t, 1e-12)          # enforce positivity
z1, z2 = correlated_normals(rho)
v_t += kappa*(theta - v_t)*dt + xi*np.sqrt(v_t)*z2*np.sqrt(dt)
S_t *= np.exp((mu - 0.5*v_t)*dt + np.sqrt(v_t)*z1*np.sqrt(dt))
jump = 1.0 if np.random.poisson(lam*dt) > 0 else 0.0
S_t *= np.exp(jump * (mu_jump - 0.5*sigma_jump**2) + sigma_jump * normal())
```

#### 1.1.2 Factor Model with Macro Common Factors
Add `common_factor_strength` that already exists in `MacroSpec`, but formalize it as a `K`-factor model:

```
R_i,t = α_i,regime + β_i1 F_1,t + ... + β_iK F_K,t + ε_i,t
```

where factors `F_k,t` are drawn from a regime-conditional multivariate normal with regime-switching covariance. Set `β_i` based on `macro_beta` already in `AssetSpec` (use factor-1 = market, factor-2 = momentum, factor-3 = carry as a minimum).

**Implementation pattern (adopt from fsynth):**
```python
factors = rng.multivariate_normal(
    mean=regime_factor_mu,
    cov=regime_factor_cov,  # switches with Q
    size=steps
)
asset_return = (alpha[s] + beta @ factors[t] + idiosyncratic[s]) * dt
```

Current `app/simulation.py` already approximates this at lines 435-448 with a single `factor + specific` draw; we should lift it to `K>=2` factors.

### 1.2 Regime Transition Generator (Markov Chain)

Replace the per-regime loop in `run_forward_test` with a forward-simulated regime path:

```python
Q = np.array([[0.92, 0.05, 0.03],
              [0.04, 0.90, 0.06],
              [0.01, 0.04, 0.95]])  # example 3-state
state = 0
for t in range(steps):
    state = rng.choice(3, p=Q[state])
    mu[s], vol[s], kappa[s], theta[s], ... = regime_params[state]
```

### 1.3 Key References & Code

| Resource | Why |
|---|---|
| **Heston (1993)** — "A Closed-Form Solution for Options with Stochastic Volatility" | Foundation; use for calibrating `(κ,θ,ξ,ρ)` |
| **Merton (1976)** — "Option Pricing when Underlying Returns Are Discontinuous" | Jump diffusion; Lai & Shanafelt options-research.com/gordon |
| **Hamilton (1989)** — "A New Approach to the Economic Analysis of Nonstationary Time Series" | Markov regime switching foundation |
| **Goutte et al. (2017)** — "Regime-switching Stochastic Volatility Model: Estimation and Forecasting" | MLE estimation with jumps |
| **fsynth** — `github.com/welcra/fsynth` | MIT-licensed Python; Heston + Merton + regime macro states; Numba JIT. Pattern to fork: `MarketConfig` dict, CLI via `typer`, parquet I/O |
| **`financial-stochastic-processes`** — PyPI | Has Heston, Regime-Switching, and Merton classes ready to pip-install |

### 1.4 Concrete Upgrade for `app/break_test/regimes.py`

1. Add `_REGIME_Q` transition matrix.
2. Simulate `regime_path` forward.
3. For each step, call Heston solver + Merton Poisson jumps.
4. Output the same `{prices, returns}` dict so `compute_positions` doesn't break.

---

## 2. Market Microstructure Simulation (CDA, Price-Time Priority, TCP/IP Matching Engine)

### 2.1 What Your Engine Already Has (Strong Foundation)

`app/exchange/order_book.py` already implements:
- Continuous double auction (CDA)
- Price-time priority (FIFO per price level via `deque`)
- Matching on incoming order vs. `best_ask`/`best_bid`
- Partial fills, book depth tracking
- Circuit breakers / halts

This is genuinely competitive vs. many toy simulators.

### 2.2 Reference Architecture Layers

The **standard industry matching-engine stack**, from thin-client to matching-logic, is:

```
[TCP/IP gateway] → [matching engine core] → [market data publisher] → [clearing/settlement]
```

For a Python simulation, we do not need TCP sockets, but modeling message lifecycles with the existing `LatencyProfile` (`feed_ms/decision_ms/order_entry_ms/cancel_ms`) already mirrors this.

### 2.3 What to Add for Micro-Realism

| Gap | SOTA Pattern | Adopt From |
|---|---|---|
| **Latency queue model** | Orders arrive late; `submission_time` ≠ `arrival_time`. Already present in `_order_timing()`; extend to randomized feed delay distribution (e.g., log-normal around `latency.entry_ms`). | Current `simulation.py` `_stamp_order` |
| **Fragmentation / dark pool routing** | Add a `routing_profile` to `ExchangeSpec` (`lit_only`, `smart_router`, `dark_pool_fraction`). | CoinTossX `routing` strategy |
| **Tick-size regimes / subsize ladder** | U.S. equities moved to "tick-size pilot" bands; simulate quoting at half-penny, penny, nickel tiers. | `MarketSimulator` (Man Group, 2023) |
| **Pricebands / LULD (Limit Up-Limit Down)** | Replace simple `circuit_breaker_pct` with per-side, rolling-N LULD bands around reference price. | Reg NMS concept; Duke/QuantConnect lit reference |

### 2.4 Open-Source Code Patterns

| Repo | Description | Adopt |
|---|---|---|
| `github.com/SimonOuellette35/Microstructure` | Python framework for generating microstructure data from LOB; queue-reactive order flow | Queue-reactive provider pattern |
| `github.com/donpablo57/CointeSSX` / `CoTossX` (Jericevich 2022) | Low-latency matching engine in Python/C++; open-hardware-inspired | Matching-logic benchmarks / throughput tests |
| `github.com/obhen/lob` | Ultra-lightweight Python engine simulating LOB at msgs/s; good benchmark for throughput of your current `OrderBook` | Performance regression pattern |
| `github.com/ManGroup/trading-calendar-strategies` (notebook) | Microstructure notebooks for ACD, VPIN | Liquidity-toxicity metrics |
| `github.com/QuantConnect/Lean` | Full production multi-asset engine | Reference when adding futures/options support |

### 2.5 Concrete Formulas

**Garman (1976) stylized-exponent inventory-skew rule (already in your MM):**
```
reservation_price = mid - γ * I * σ² * Δt
```
where `I` = inventory, `γ` = risk aversion, `σ` = vol, `Δt` = time-to-close.

You already approximate this with `inventory_skew` parameter in `MarketMaker.decide`. Replace with full Garman formula:
```python
inv_skew = risk_aversion * (inventory**2) * (idiosyncratic_vol**2) * time_left
```

---

## 3. Execution Algorithms (Almgren-Chriss, IS, TWAP, POV)

### 3.1 Theoretical Foundation: Almgren-Chriss (2000)

Treat trading as a control problem. Let `X_t` be remaining inventory at time `t`. Trading rate `v_t = -dX_t/dt`.

**Price impact (observed price):**
```
S_t = S̄_t + η v_t + θ (X_0 - X_t)
S_t = fundamental + temporary impact + permanent impact
```

**Total cost:**
```
C = ∫₀^T S_t v_t dt
  = ∫₀^T [S̄_t v_t + η v_t² + θ (X_0 - X_t) v_t] dt
```

**Risk-adjusted objective** (Almgren-ChrissJan 2000/J Risk):
```
E[C] + λ Var(C)
```

Closed-form optimal trajectory for symmetric permanent+temporary impact:
```
x*_t = X_0 * (cosh(κ(T-t)) / cosh(κT))       for buy
with κ = sqrt(2 λ η σ² / θ²)
```

Implementation pattern (open-source reference):
- `github.com/joshuapjacob/almgren-chriss-optimal-execution` — notebook; numerically solves the ODE `x'' = (κ²) x`. Port to `app/agents/behaviors.py` `ExecutionAgent.decide()`.
- `github.com/quant-research-group/quant-trading-algorithms` — includes Almgren-Chriss, VWAP, IS.

### 3.2 TWAP, IS, VWAP, POV Formulas

| Algorithm | Definition | Formula / Implementation |
|---|---|---|
| **TWAP** (Time-Weighted) | Split equally over `N` slices of duration `T/N`. | `slice_quantity = Q / N` → your existing `target_quantity // 80` pattern |
| **VWAP** (Vol-Weighted) | Weight each slice by historical `V_i/∑V_i`. | `slice_i = Q * V_i / ∑V`; requires volume profile; use `observed_volume * timeWarp` |
| **IS** (Implementation Shortfall) | `IS = (P_arrival - P_actual) / P_arrival × 10000` (bps) | Already computed in `simulation.py` line 808 `shortfall` |
| **POV** (Percentage of Volume) | Each slice = `α × V_t` (capped at `Q - filled`). | Already partially in `ExecutionAgent.pov` logic |

### 3.3 What Your `app/schemas/world.py` and `app/agents/behaviors.py` Need

1. **Almgren-Chriss schedule**: Extend `ExperimentSpec` strategy literal to include `optimal_execution`.
2. **Urgency curve**: Replace hard-coded `1.5 - progress` with a `κ` parameterizer and compute `x*_t` from Heston-estimated `σ`.
3. **Arrival-price benchmark**: Use `arrival_price` from `simulation.py` (already computed) for IS / P&L.

---

## 4. Transaction Cost Models (TCM)

### 4.1 Decomposition of IS

```
Implementation Shortfall (IS) = ΔP_arrival + Spread_cost + Temporary_impact + Permanent_impact - Timing_luck
```

In your `simulation.py` summary dict, you already compute:
- `vwap_slippage_bps`
- `temporary_impact_bps`
- `spread_paid_bps`
- `adverse_selection_bps`
- `persistent_impact_bps`

That's 5 of 6 standard TCM components. Add `opportunity_cost_terminal` = `(PnL_if_held - PnL_if_executed)`.

### 4.2 Square-Root Market Impact Model (Almgren 2000 scale)

```
I = η * (X/V)^γ * σ^β * (1/τ)^ζ
```

Typical calibrations for equities: `η ~ 50-200 bps`, `γ ~ 0.5`, `β ~ 0.6`, `ζ ~ 0.1-0.2`.

**Pattern to add to `OrderBook.submit()` impact estimate:**
```python
participation = order.quantity / max(1, observed_volume)
impact_bps = eta * (participation**0.5) * (volatility**0.6)
```

Reference: **Hasbrouck (2009) "Empirical Market Microstructure"**, Grinold & Kahn, or **Kissell (2013) "Optimal Trading Strategies"**.

### 4.3 Collecting Real Slippage Stats Per Agent

Add a `TransactionCostModel` dataclass in `app/exchange/` that stores per-fill:
```python
@dataclass
class TCM:
    arrival_px: int
    fill_px: int
    quantity: int
    side: Side
    spread_at_arrival_bps: float
    time_to_fill_ms: int
```
Then aggregate in `simulation.py` post-run.

---

## 5. Walk-Forward Validation (Embargo, Deflated Sharpe, PSR, CPCV)

### 5.1 Why This Is Missing

Currently `run_forward_test` iterates regimes independently and reports median return. There is **no statistical correction for selection bias**, no out-of-sample purging, and no confidence-interval reporting.

### 5.2 The Four-Square Validation Framework

From **Bailey et al. (2015)** series of papers + **Marcos López de Prado (2018)**

#### 5.2.1 Probabilistic Sharpe Ratio (PSR)
Tests if the true Sharpe exceeds a target `SR*`:

```
PSR = P(SR_true > SR*) = Φ( (SR̂ - SR*) / σ(SR̂) )
```

where `σ(SR̂) = sqrt((1 + 0.5 SR̂² - SR̂·skew + (kurtosis-3)/4) / (N-1))` for `N` bars.

**Formula:**
```python
psr = norm.cdf((sharpe_observed - sharpe_target) / sharpe_std)
```

#### 5.2.2 Deflated Sharpe Ratio (DSR)
Corrects for the fact you selected the best strategy from `K` candidates:

```
DSR = P(SR_max > SR* | max over K trials)
     = (1 - (1-Φ(z*))^K)   [under i.i.d. Gaussian null; see Bailey et al. 2016]
```

where `z*` is achieved z-score. For non-Gaussian returns, use a **permutation block-bootstrap** instead.

#### 5.2.3 Minimum Backtest Length
If the strategy is truly good, how long must it be to confirm Sharpe `SR`?

```
T_min = 2 * Φ^{-1}( (1 + PSR^{1/(T-1)}) / 2 )^2  [Bailey 2015 Eq. 7]
```

Adopt the formula from `eslazarev/purged-cross-validation` which implements this.

#### 5.2.4 Combinatorial Purged Cross-Validation (CPCV)
Instead of K-fold on a temporal series:

1. Split into `K` **embargoed** caret baskets: each test set is separated from its train by `h` bars (PurgedCV).
2. Enumerate all `C(n,k)` combinations of `k` baskets as train.
3. For each combination, compute performance distribution.

This gives an empirical distribution of OOS Sharpe rather than a single point estimate. The Sharpe ratio is stable iff its CPCV distribution is narrow and centered above zero.

#### 5.2.5 Embargo Gap
Size must be at least the **maximum label horizon** (lookahead in target construction):

```python
def embargo_indices(train, test, embargo_h):
    return train[-1] < test[0] - embargo_h
```

### 5.3 Open-Source Code Patterns

| Repo | Description |
|---|---|
| `github.com/eslazarev/purged-cross-validation` | scikit-learn-compatible; implements `PurgedKFold`, `EmbargoKFold`, `CombinatorialPurgedCV`, `deflated_sharpe()`, `minimum_backtest_length()`. Active, 19 stars. |
| `github.com/huggingface/synthcity` | Conditional GAN for synthetic data, but the **evaluation submodule** useful for model-stability scoring |
| López de Prado (2018) **"Advances in Financial Machine Learning"** Has full chapter code. |

### 5.4 Incorporating into `app/break_test/metrics.py`

Add functions:
```python
def probabilistic_sharpe(sharpe_hat, sharpe_star, n, skew, kurt):
    z = (sharpe_hat - sharpe_star) / (sharpe_std(n, sharpe_hat, skew, kurt))
    return norm.cdf(z)
def deflated_sharpe(max_sharpe_k, n_trials, n, skew=0.0, kurt=3.0):
    z = max_sharpe_k / sharpe_std(n, 0.0, skew, kurt)
    dsr = 1.0 - (1.0 - norm.cdf(z))**n_trials
    return dsr
```
Extend `run_forward_test` return dict to include `psr_95`, `dsr`, `min_backtest_len_days`.

---

## 6. Free Data Sources (Multi-Asset, Corporate Actions, LOB)

### 6.1 Price / Corporate-Action Data That Doesn't Require Payment

| Source | Asset Classes | Corporate Actions | Notes |
|---|---|---|---|
| **Yahoo Finance (yfinance)** | Equities, ETFs | Splits + dividends (basic) | Free; `yfinance` Python lib |
| **Alpha Vantage** | Equities, FS, Crypto | Splits + dividends + earnings | 5 req/min free; free API key |
| **Financial Modeling Prep (FMP)** | Equities, FX, crypto | Splits, dividends, splits calendar | Generous free tier |
| **Stooq** | Equities, ETFs | EOD OHLCV | ~Be careful: no CA data, but historically clean free CSVs |
| **EOD Historical Data** | Equities, FX, bonds | Splits + dividends + splits | 20 req/day free |
| **NASDAQ Data Link (Quandl) free tables** | Futures, equities | Corporate actions via `WIKI` (historical), `SHARADAR` SF1 for fundamentals | `nasdaq-data-link` Python lib |

**For local corporate-actions archive** (build your own from FMP free endpoint):
```python
import requests, pandas as pd
r = requests.get("https://financialmodelingprep.com/api/v3/stock_split_calendar?from=2020-01-01&to=2025-01-01&apikey=FREE_KEY")
df = pd.DataFrame(r.json())
df.to_parquet("data/splits.parquet")
```

### 6.2 Limit Order Book Data (Harder — Free Options Are Rare)

| Source | Data | Access | Caveats |
|---|---|---|---|
| **LOBSTER** (lobsterdata.com) | NASDAQ 5-level snapshots + single-order message feed | Free after registration | 10 stocks, limited dates (~2014 era) |
| **WRDS LOBSTER / TAQ** | NYSE TAQ + LOBSTER snapshots | Free with university account | Requires .edu/academic login |
| **NASDAQ ITCH allbook via `itch-py`** | Full message-level reconstruction possible | Free | Must build LOB reconstruction yourself |
| **DXFeed / Databento sample** | Limited free LOB samples | Free tier | Paid for full history |
| **S3A: Synthetic LOB by MIT (neurips 2022)** | `s3a` synthetic LOB datasets | Free download | Synthetic, designed for benchmarking |

**Pattern:** Fetch raw LOB, build your own `OrderBook` replayer method on `app/exchange/order_book.py`:

```python
def replay_lob_snapshot(self, bids: dict[int, int], asks: dict[int, int]) -> None:
    # completely replace book; good for historical replay
    self.bid_levels = defaultdict(deque, {p: deque() for p in sorted(bids, reverse=True)})
    # ...
```

### 6.3 Multi-Asset Price Data Pattern

For a multi-asset simulation, fetch once, cache to parquet, then read via your existing `app/simulation.py` fundamentals generator. FMP free tier covers equities, FX, crypto.

---

## 7. Existing Products / Tools (What We Compete With Or Integrate)

### 7.1 QuantConnect + LEAN

- **Engine:** Open-source C#/.NET `LEAN` engine; multi-asset (equities, options, futures, FX, crypto).
- **Strengths:** Cloud backtesting, real brokerage connectivity (IBKR, Tradier), extensive data library, 275k users.
- **Weaknesses for your use case:** C# not Python native; no built-in Heston/synthetic regime generation; LOB/simulation components are add-ons, not first-class.
- **What to steal:** Pydantic-typed `Universe` and data model design; the `QCAlgorithm` observer pattern maps cleanly to your `ExecutionDecider` callback in `simulation.py`.
- **Website:** https://www.quantconnect.com/

### 7.2 AlgoSeek

- **Data provider** (not an engine), ultra-low-latency US equities, options, futures.
- **Integration:** Now distributed via QuantConnect's data vendor pipeline.
- **For you:** If you ever need real LOB/Tick-replay training data, AlgoSeek is what institutions buy. Free tier unlikely; consider as a "pro" data layer.

### 7.3 Duke Trading Simulation Platform (AmplifyME)

- **Product:** Duke partners with AmplifyME for student-facing live-market simulations.
- **Open-source status:** **NOT open source**. Closed platform used in curricula.
- **Duke LEARN lab** sometimes open-sources from courses; check `https://econ.duke.edu/dfe/finance-simulations-amplifyme` for current partnerships.
- **For you:** This is a direct **user/competitor** if you target academic markets. Your differentiator = local/offline + synthetic multi-regime micro + open-source.

### 7.4 MarS (Microsoft Research)

- **Paper (2024):** "MarS: a Financial Market Simulation Engine Powered by Generative Models" (Li et al., OpenReview)
- **Approach:** LLM-powered agent order generation rather than hand-tuned heuristics.
- **Code:** Not currently open source; research prototype.
- **For you:** Watch. LLM agents will increasingly drive high-fidelity microstructure simulators. Your codebase's `AGENT_CLASS` registry (`app/agents/behaviors.py`) is the natural insertion point for an `LLMAgent` subclass.

### 7.5 Other Comparable Tools

| Tool | Open Source | Cost | Notes |
|---|---|---|---|
| `obi-sim` (JPM) | Internal | — | Not publicly available |
| `Markov::Sim` | Open (C++) | — | Matches on tick, event-driven; useful benchmark |
| Backtrader | Yes | Free | Python; lacks LOB/micro |
| `VectorBT` | Yes | Free-ish | Vectorized backtesting in Python; good for cross-sectional |
| `bt` (pmorissette) | Yes | Free | Simpler, event-driven |
| `TradingSystemFramework` | Yes | Free | Crypto-focused |

### 7.6 Competitive Positioning Summary

Your current platform is **more advanced than Backtrader/bt** because it has:
- Real limit order book (not vectorized close-to-close)
- Multi-asset, regime-switching macro factor model
- Latency profiles
- Execution agent with TWAP/POV
- Strict accounting invariants

You are **behind** a production-grade system like LEAN in:
- Data ingestion (real-time LOB, corporate actions)
- Execution-quality metrics suite
- CME-style derivative modeling

You are **uniquely positioned** for:
- Synthetic regime-switching backtesting at scale (no real-data dependency)
- Research-driven strategy validation with CPCV/PSR/DSR

---

## 8. Recommended Project Implementation Roadmap

### 8.1 Phase 1 — Price Generator Upgrade (Do First)
1. Add Markov regime-transition matrix to `app/break_test/regimes.py`.
2. Implement Heston solver with full-truncation Euler-Maruyama (`kappa, theta, xi, rho` per regime).
3. Add Merton Poisson jump layer.
4. Add `K=2` or `K=3` macro factors, already half-done in `app/simulation.py` line 435-448.

**Adopt library:** `pip install financial-stochastic-processes` or fork `fsynth` Numba kernels.

### 8.2 Phase 2 — Genuine Orderbook Execution in `exchange_fwd.py`
1. Remove post-hoc position computation.
2. Call `run_simulation(world, execution_decider=strategy_observe_wrapper)`.
3. `strategy_observe_wrapper` seens only the `strategy_observations` stream (frozen data, no cheating).
4. Strategy returns orders through `orders_from_adapter_action` (already implemented in `simulation.py`).
5. Compute `implementation_shortfall_bps` from arrival price vs. strategy fills.

### 8.3 Phase 3 — Execution Algorithm Suite
1. Add `optimal_execution` to `ExperimentSpec.strategy`.
2. In `ExecutionAgent.decide()`, compute `κ` from Heston sigma estimate of SYNTH asset.
3. Solve Almgren-Chriss ODE numerically (or analytic for symmetric case).
4. Submit `v_t` slices as limit orders intentionally placed at/to cross the spread.

### 8.4 Phase 4 — Statistical Validation Layer
1. `pip install eslazarev/purged-cross-validation` and adopt `deflated_sharpe`, `minimum_backtest_length`.
2. Extend `run_forward_test` to return `psr_95`, `dsr`, `cpcv_dist_sharpe`.
3. Add embargo gap when constructing train/test split for strategies with lookback window.

### 8.5 Phase 5 — Data Sources
1. Local cache directory: `data/prices/`, `data/fundamentals/`, `data/splits_dividends/`
2. ETL from FMP and/or Yahoo Finance.
3. Optionally register for LOBSTER access for one ticker to benchmark your synthetic book shape vs. real data.

### 8.6 Phase 6 — Product & Competitive Parity
1. Publish `RESEARCH_BRIEF.md` on repo wiki => thought leadership.
2. Add MIT license + PyPI packaging.
3. Track MarS, QuantConnect LEAN, and LOBSTER data releases for integration points.

---

## 9. Quick Reference — Papers / Books

| # | Citation | Why It Matters |
|---|---|---|
| 1 | Heston (1993). "A Closed-Form Solution for Options with Stochastic Volatility." *Rev Financ Stud.* | Stochastic vol formula; calibration target |
| 2 | Merton (1976). "Option Pricing when Underlying Returns Are Discontinuous." *JFE.* | Jump diffusion baseline |
| 3 | Hamilton (1989). "A New Approach to the Economic Analysis of Nonstationary Time Series." *Econometrica.* | Markov regime switching |
| 4 | Almgren & Chriss (2000). "Optimal Execution of Portfolio Transactions." *J Risk.* | Almgren-Chriss foundation |
| 5 | Almgren (2005). "Optimal execution with nonlinear impact functions." *Appl Math Finance.* | Permanent + temporary impact models |
| 6 | Hasbrouck (2009). "Empirical Market Microstructure." *Book.* | Spread, adverse selection, TCM intuition |
| 7 | Cont & Kukanov (2013). "Optimal Order Placement in Limit Order Markets." | Limit order placement under risk |
| 8 | Bailey et al. (2015). "The Probability of Backtest Overfitting." | PBO, DSR origins |
| 9 | Bailey et al. (2016). "The Deflated Sharpe Ratio." | DSR formula |
| 10 | López de Prado (2018). "Advances in Financial Machine Learning." | CPCV, PBO, Chapter 7 — whole validation framework |
| 11 | Goutte et al. (2017). "Regime-switching SV with jumps." *Quant Financ.* | Estimation |
| 12 | Princeton paper (Kalimipalli & Susmel, 2004). "Regime-Switching Stochastic Volatility." | Regime-SV calibration approach |
| 13 | fsynth (welcra/fsynth). Synthetic financial data Heston+Jump repo. 2024. | Python code pattern |
| 14 | eslazarev/purged-cross-validation. 2025. | CPCV + DSR Python code |
| 15 | Jericevich et al. (2022). "CoinTossX." *HPC.* | Matching-engine architecture pattern |

---

## 10. Open-Source Code Patterns We Can Directly Adopt

| Pattern | File to Modify | Library / Repo |
|---|---|---|
| **Regime-transition generator** | `app/break_test/regimes.py` | `financial-stochastic-processes` `RegimeSwitching` or hand-roll from Heston |
| **Heston + Merton solver** | new `app/break_test/synth_prices.py` | `fsynth` `heston` and `jump_diffusion` modules |
| **Factor model loop** | `app/simulation.py` lines 435-448 | Multi-variate normal draw per regime |
| **Almgren-Chriss trajectory** | `app/agents/behaviors.py` `ExecutionAgent` | `joshuapjacob/almgren-chriss-optimal-execution` |
| **TCM (spread + impact)** | new `app/exchange/transaction_cost.py` | Pérez-Plaza et al. TCA patterns |
| **CPCV splits** | new `app/break_test/validation.py` | `eslazarev/purged-cross-validation` |
| **PSR / DSR functions** | `app/break_test/metrics.py` | Bailey formulas + `purgedcv.deflated_sharpe` |
| **Corporate actions ETL** | new `app/data/sources.py` | FMP / Alpha Vantage REST endpoints |
| **LOB replayer** | `app/exchange/order_book.py` new method | `lob` replayer from `SimonOuellette35/Microstructure` |

---

## 11. Summary of Key Formulas

### 11.1 Heston
```
/S_t = μ S_t dt + sqrt(v_t) S_t dW_t
dv_t = κ(θ - v_t)dt + ξ sqrt(v_t) dW_t^v
d⟨W^S, W^v⟩ = ρ dt
Euler: v = max(v + κ(θ-v)dt + ξ*sqrt(v)*Z*sqrt(dt), 1e-12)
```

### 11.2 Merton Jump
```
/log(S_t+1/S_t) = (μ - 0.5σ²)dt + σ sqrt(dt) Z + (Y-1) dN_t
/dN_t ~ Poisson(λ dt)
```

### 11.3 Impact
```
I_temp = η (Q/V)^γ
I_perm = θ (Q/V)^a
IS = (P_arrival - P_vwap)/P_arrival * 10,000 bps
```

### 11.4 PSR
```
z = (SR̂ - SR*) / sigma_SR
PSR = Φ(z)
σ_{SR̂} = sqrt((1 + 0.5 SR̂² - SR̂·γ₁ + (γ₂-3)/4) / (N-1))
```

### 11.5 DSR (scaled)
```
DSR = 1 - (1 - Φ( max_SR / σ_{SR̂} ))^{K}
```

### 11.6 CPCV min backtest length
```
T_min = 2 [Φ⁻¹( (1 + PSR^{1/(T-1)})/2 )]² / SR̂²  [days]
```

---

## 12. Checklist for This Task

- [x] Inspected current `app/break_test/{regimes.py,exchange_fwd.py}`, `app/exchange/{order_book.py,orders.py,market.py}`, `app/schemas/world.py`, `app/agents/behaviors.py`, `app/simulation.py`
- [x] Identified 4 hand-tuned regimes + plain GBM as the baseline to replace
- [x] Confirmed `exchange_fwd.py` still computes positions on post-hoc mid-prices (cheating status)
- [x] Confirmed OrderBook already has CDA + price-time priority as a strong base
- [x] Searched and confirmed Heston, Merton, regime-switching SV, Almgren-Chriss, TCM, PSR/DSR/CPCV papers/libraries
- [x] Noted web_search became unavailable after 4 calls — fell back to browser tool for GitHub pages and quantitative blogs
- [x] Written to `app/break_test/RESEARCH_BRIEF.md`

---

*End of Research Brief.*
