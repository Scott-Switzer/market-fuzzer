# Break Test — Productization Sprint Plan

## Objective
Turn the current synthetic-exchange demo into a genuine quant-product prototype that can be shown to firms, used for strategy research, and demonstrated in a hackathon with no hand-waiving.

## Success Criteria
- Multi-asset universe: minimum 8 names, configurable correlation structure
- Realistic order submission: user strategies actually submit limit/market orders to the matching engine
- Deterministic seed-based simulation with conservation invariants
- Realistic data path: yfinance multi-asset bars + corporate actions + potential LOB data
- GARCH/jump-diffusion regime generator fitted to input data, not hand-tuned constants
- Realistic transaction costs: Almgren-Chriss impact, borrow fees, tiered maker/taker schedule
- Production-grade validation: walk-forward, CPCV, deflated Sharpe, PSR, embargo, regime-aware scoring
- Honest reporting: plain-English findings + exportable HTML/PDF report + session history
- UI that a quant can operate: universe selector, correlation matrix, execution algo selector, session history

## Workstreams

### 1. Literature & tooling research
- **Lead**: delegated subagent `deleg_85c689f6`
- **Output**: `app/break_test/RESEARCH_BRIEF.md`
- **Key citations**: Bailey & López de Prado, Almgren & Chriss, Hasbrouck, Foucault et al., Cont & Bouchaud
- **Coverage**: synthetic market generation, microstructure, execution, TCosts, validation, free data sources, commercial/open-source benchmarks

### 2. Data source audit
- **Lead**: delegated subagent `deleg_00a14679`
- **Output**: `app/break_test/DATA_SOURCE_RESEARCH.md`
- **Coverage**: yfinance, Stooq, Twelve Data, Polygon free, FRED, Nasdaq Data Link, LOBSTER, SEC EDGAR corporate actions, VIX/rates
- **Deliverable**: exact fetch patterns, licensing, schema docs, auth requirements

### 3. Multi-asset exchange kernel
- **Lead**: delegated subagent `deleg_16de8060`
- **Output**: redesign plan + implementation in `app/exchange/*`, `app/simulation.py`, `app/schemas/world.py`, `app/break_test/exchange_fwd.py`
- **Plan doc**: `app/break_test/EXCHANGE_REDESIGN_PLAN.md`
- **Key changes**: N-asset order routing, price-time priority, cross-asset margin, settlement date tracking, strategy order submission hooks, conservation invariants

### 4. Synthetic generator upgrade
- **Lead**: delegated subagent `deleg_0c0908ca`
- **Output**: research notes + implementation in `app/break_test/regimes.py`
- **Plan doc**: `app/break_test/GENERATOR_RESEARCH.md`
- **Methodology**: GARCH(1,1) fit, threshold regime switching, Poisson jump diffusion, one-factor cross-sectional correlation, fitted to input data

### 5. Transaction cost model
- **Lead**: delegated subagent `deleg_3cb92f46`
- **Output**: research notes + implementation in `app/break_test/metrics.py`, `app/simulation.py`, `app/schemas/world.py`
- **Plan doc**: `app/break_test/COST_MODEL_NOTES.md`
- **Methodology**: Almgren-Chriss temporary + permanent impact, borrow fees, tiered maker/taker fees, replacement of hardcoded 2bps

### 6. Walk-forward + probabilistic validation
- **Lead**: delegated subagent `deleg_98d5ddcf`
- **Output**: research notes + implementation in `app/break_test/oos_validation.py`
- **Plan doc**: `app/break_test/VALIDATION_RESEARCH.md`
- **Key metrics**: walk-forward with embargo, deflated Sharpe, PSR, CSR, CPCV, regime-aware fold weighting

### 7. UI/UX redesign
- **Lead**: delegated subagent `deleg_f1cf1c84`
- **Output**: plan + implementation in `app/static/break-test.html`
- **Plan doc**: `app/break_test/UI_REDESIGN_PLAN.md`
- **Key features**: multi-asset selector, correlation heatmap, execution algo selector, TCost inputs, regime timeline, session history, corrected-vs-baseline comparison, exportable summary

## Integration Checklist
- [ ] All tests pass after each workstream lands
- [ ] No hardcoded 3-asset assumptions remain in break-test paths
- [ ] No forward-test path extracts mid_prices before running strategy; strategy submits orders to book
- [ ] No hardcoded 2bps costs remain in user-facing paths
- [ ] No hand-tuned regime constants remain in GBM generator
- [ ] Plain-English compiler uses real NLP or structured strategy DSL
- [ ] yfinance uses Adj Close or explicit adjustment with warnings
- [ ] Report exports include honest limitations and references

## Known Risks
- Subagents may conflict if they edit the same files; integration owner must coordinate merges
- Full LOB data sources may be paywalled; fallback to generated order-flow with realistic features
- GARCH fit may fail on very short series; fallback to empirical bootstrap
