# Fenrix MVP Gap Matrix

Baseline: GitHub `main` `822ec731ba0b6fb8083cabde7c2af751206350c3` on `Scott-Switzer/market-fuzzer`.

This matrix is the starting executable audit for the Fenrix MVP program. It is deliberately conservative: a capability is WORKING only when a real route, real implementation, and real test or runtime behavior support it from this checkout. Claims in READMEs or landing-page copy do not make a capability WORKING.

Legend
- WORKING: core user-visible capability is implementable from this codebase and is wired end-to-end, or exists as a complete existing product path.
- PARTIAL: exists but missing a required UX, persistence, comparison, replay, export, or deterministic-evidence piece.
- BROKEN: wired but currently failing for a likely user journey.
- MISSING: no current implementation.
- OUT OF MVP SCOPE: exists but intentionally not part of this MVP program, or not justified yet.

Notes
- The product has two distinct evaluation modes: strategy validation (canonical strategy spec / pipeline) and synthetic exchange agent benchmark (M10.5–M10.7). They may share UI/report infrastructure. They must not be falsely represented as the same execution engine without a tested adapter.
- MVP-0 objective: merge or confirm the M10.8 documentation branch, then start implementation.
- MVP-1 objective: one coherent Fenrix entry point with project workflow and offline demo onboarding.
- MVP-2 objective: real executable strategy validation from the UI.
- MVP-3 objective: synthetic stress failure search + replay + revision + retest.
- MVP-4 objective: browser-based synthetic exchange agent benchmark + dataset release inspection.
- MVP-5 objective: comparison, evidence, sensitivity diagnostic, export.
- MVP-6 objective: browser E2E, financial correctness regressions, security/privacy audit, release candidate.

## 0. Product shell and entry points

| Requirement | Status | Implementing file / function | Existing route | Existing test | Observed behavior | Missing behavior | Planned change | PR |
|---|---|---|---|---|---|---|---|---|
| Single Fenrix product homepage | PARTIAL | `app/static/start.html` is the current public first page; `/` can serve a shell | `/` and `/start` | No dedicated E2E for shell identity yet | `start.html` currently says "Strategy Break Test" and leads with CSV upload; `strategy-lab.html` already brands "Fenrix Strategy Workspace" with tabs | No single unified Fenrix shell; multiple unrelated static HTML pages; root entry does not clearly offer the product | MVP-1: establish one coherent Fenrix entry point and application shell wrapping existing workflows | MVP-1 |
| Preserve legacy routes | WORKING | `app/api/app.py` serves static pages + API | `/arena`, `/break-test`, `/market-fuzzer`, `/strategy-stress-lab`, `/synthetic-market-world`, `/strategy-lab` | Not all routes have E2E | Routes exist and render existing pages | Some pages are incomplete demos; not all have full end-to-end workflows | Preserve routes; improve coherence | MVP-1 |
| Desktop-first usable shell | PARTIAL | `app/static/strategy-lab.html` has a usable multi-step layout | `/strategy-lab` | No browser E2E yet in MVP state | Tabbed thesis/validation/failure/audit flow already exists in Strategy Lab | No unified project context; no saved research runs; no replay/evidence export from one coherent workflow | MVP-1 + MVP-5 | MVP-1, MVP-5 |
| Mobile/tablet tolerability | PARTIAL | CSS in static pages uses responsive grids | static pages | Manual | Responsive breakpoints exist for some pages | Not verified across all MVP screens | MVP-1 + MVP-6 | MVP-1, MVP-6 |

## 1. Project workflow

| Requirement | Status | Implementing file / function | Existing route | Existing test | Observed behavior | Missing behavior | Planned change | PR |
|---|---|---|---|---|---|---|---|---|
| Create or open a research project | MISSING | No project resource with UI in this MVP state | No `/api/fenrix/projects` visible in MVP | None | No Fenrix project workspace exists yet | Need minimal project resource, list/create/open, persisted | MVP-1: introduce minimal project resource and read/list API | MVP-1 |
| Visible current project and strategy | MISSING | n/a | n/a | n/a | Not present in current shell | Need UI affordances to show current project/strategy | MVP-1 | MVP-1 |
| Progress states and actionable errors | PARTIAL | existing static UIs have status blocks | `/strategy-lab`, `/synthetic-market-world` | No E2E | Status containers exist in existing pages | Inconsistent across pages; no unified long-running request handling | MVP-1 + MVP-6 | MVP-1, MVP-6 |
| Empty states suggest next action | PARTIAL | some pages show "No experiments yet" | `/synthetic-market-world` | Manual | Some empty states exist | Many MVP screens still missing empty state guidance | MVP-1 | MVP-1 |
| Browser refresh recovers state | PARTIAL | existing pages reload some state from API | `/synthetic-market-world` | No E2E | `stress-lab.html` reloads worlds/packs/strategies/experiments from API | Persisted experiment/run state not fully part of Fenrix project workflow yet | MVP-1 + MVP-5 | MVP-1, MVP-5 |

## 2. Strategy validation — compile/approve/execute

| Requirement | Status | Implementing file / function | Existing route | Existing test | Observed behavior | Missing behavior | Planned change | PR |
|---|---|---|---|---|---|---|---|---|
| Plain-English compiler | PARTIAL | `app/strategy_language.py: compile_strategy_brief`; `app/domain/strategy_spec.py` | `POST /api/enterprise/strategies/compile-brief` | Needs direct verification | Compiler exists and is wired into `stress-lab.html` brief form | Not yet part of Fenrix unified workflow; example strategy templates not all verified executable | MVP-2: expose compiled interpretation, confirm families, wire into Fenrix | MVP-2 |
| Show interpreted rules/assumptions/unsupported clauses | MISSING | compile returns proposal + ambiguities + claim_boundary | `POST /api/enterprise/strategies/compile-brief` | No dedicated UI yet | Current `stress-lab.html` shows JSON proposal in a `<pre>` | No human-readable interpreted strategy panel in Fenrix | MVP-2 | MVP-2 |
| Revise brief / resolve ambiguities | PARTIAL | compile -> ambiguities; strategy registration exists | `/api/enterprise/strategies/compile-brief`, `/api/enterprise/strategies` | Manual | Ambiguities returned; registration exists | No Fenrix resolution flow for compile ambiguities yet | MVP-2 | MVP-2 |
| Strategy not approvable if unresolved | MISSING now | Approval semantics exist in canonical services | canonical approval service | Needs verification | Not yet exposed in Fenrix flow | Need explicit approval gate with unresolved-clause blocking | MVP-2 | MVP-2 |
| Versioned strategy persistence | PARTIAL | `app/experiments.py`, `app/api/app.py` strategy endpoints | `/api/enterprise/strategies` and experiment store | Needs verification | Strategy and experiment persistence exists in backend | Not surfaced as Fenrix versioned strategy list with comparison yet | MVP-1 + MVP-5 | MVP-1, MVP-5 |
| No silent fallback to another family | MISSING now | compiler returns proposal/ambiguities | compile endpoint | Needs verification | Compiler returns proposal model; no fallback behavior confirmed | Need to verify no hidden fallback and block approval on unsupported executable meaning | MVP-2 | MVP-2 |

## 3. Executable strategy examples

| Requirement | Status | Implementing file / function | Existing route | Existing test | Observed behavior | Missing behavior | Planned change | PR |
|---|---|---|---|---|---|---|---|---|
| Static allocation example | PARTIAL | registry + pipeline + canonical executors | canonical strategy pipeline | Needs MVP verification | Strategy families are registered; examples likely expressible | Not verified as complete Fenrix UI flow with real trade/accounting output | MVP-2 | MVP-2 |
| SMA crossover example | PARTIAL | strategy language + canonical pipeline | compile + backtest paths | Needs MVP verification | SMA-like rule expressible; needs verification that registered executor runs it | Not verified as complete Fenrix flow | MVP-2 | MVP-2 |
| Momentum ranking example | PARTIAL | canonical pipeline + executors | compile + backtest paths | Needs MVP verification | Likely expressible; needs execution verification | Not verified | MVP-2 | MVP-2 |
| Long/short factor example | PARTIAL | canonical pipeline + executors | compile + backtest paths | Needs MVP verification | Likely expressible; needs long/short sign + accounting verification | Not verified | MVP-2 | MVP-2 |
| Tactical allocation example | PARTIAL | canonical pipeline + executors | compile + backtest paths | Needs MVP verification | Likely expressible; needs verification | Not verified | MVP-2 | MVP-2 |
| At least three materially different executable examples through UI | MISSING | n/a | n/a | n/a | Not yet demonstrated through Fenrix UI | Need real UI flow with persisted trade/accounting results | MVP-2 | MVP-2 |

## 4. Accounting and execution correctness

| Requirement | Status | Implementing file / function | Existing route | Existing test | Observed behavior | Missing behavior | Planned change | PR |
|---|---|---|---|---|---|---|---|---|
| Cash/position conservation | PARTIAL | `app/strategies/pipeline.py`, registry executors | canonical execution | Existing strategy tests likely cover parts | Pipeline exists; accounting likely covered in existing tests | Needs explicit MVP-level verification with manually verifiable fixtures | MVP-2 + MVP-6 | MVP-2, MVP-6 |
| Portfolio valuation | PARTIAL | pipeline + result assembly | backtest result path | Needs verification | Result metrics likely assembled | Need traceable field-to-metric mapping in UI | MVP-2 + MVP-5 | MVP-2, MVP-5 |
| Long/short sign handling | PARTIAL | executors + pipeline | canonical execution | Needs verification | Likely implemented | Needs explicit MVP verification | MVP-2 | MVP-2 |
| Transaction costs and timing | PARTIAL | pipeline + executors | canonical execution | Needs verification | Costs likely supported | Needs MVP verification and UI traceability | MVP-2 | MVP-2 |
| No same-bar lookahead when next-open specified | MISSING verification | execution timing rules | canonical execution | Needs verification | Not yet verified in MVP context | Need explicit test | MVP-2 + MVP-6 | MVP-2, MVP-6 |
| Deterministic strategy/result hashes | PARTIAL | canonical domain + storage | compiled strategy + experiment artifact paths | Needs verification | Hashing likely present in canonical layer | Needs explicit Fenrix-facing proof in UI/export | MVP-2 + MVP-5 | MVP-2, MVP-5 |
| Identical approved strategy content across backtest and stress | MISSING verification | canonical spec + campaign service | canonical approval + campaign | Needs verification | Not yet demonstrated as Fenrix matched comparison | MVP-3 + MVP-5 | MVP-3, MVP-5 |

## 5. Backtest presentation

| Requirement | Status | Implementing file / function | Existing route | Existing test | Observed behavior | Missing behavior | Planned change | PR |
|---|---|---|---|---|---|---|---|---|
| Equity curve | PARTIAL | backtest result + UI | `/strategy-lab` and `/synthetic-market-world` style reports | Manual | Some result metrics exist | Not yet Fenrix unified equity chart | MVP-2 | MVP-2 |
| Benchmark curve if available | PARTIAL | result assembly | backtest result | Needs verification | May be available in result | Needs UI wiring + n/a handling | MVP-2 | MVP-2 |
| Total return / drawdown / Sharpe / turnover / trades / costs | PARTIAL | result metrics | backtest result | Needs verification | Metrics likely available | Need traceable display + n/a reasoning + no fake zeroes | MVP-2 + MVP-6 | MVP-2, MVP-6 |
| Position/exposure history | PARTIAL | result artifacts | experiment artifacts | Needs verification | Artifact backing may exist | Needs UI presentation | MVP-5 | MVP-5 |

## 6. Synthetic stress and failure

| Requirement | Status | Implementing file / function | Existing route | Existing test | Observed behavior | Missing behavior | Planned change | PR |
|---|---|---|---|---|---|---|---|---|
| Connect approved strategy to campaign service | PARTIAL | `app/strategy_lab/canonical/campaign_service.py` and experiment store | stress experiment API | Needs verification | Campaign/stress machinery exists in backend | Not yet Fenrix unified with same approved strategy | MVP-3 | MVP-3 |
| User-selectable bounded stress settings | PARTIAL | scenario studio + experiment API | `/api/enterprise/...` | Manual | Some configuration exists | Not yet Fenrix UX with honest developer-vs-held-out labeling | MVP-3 | MVP-3 |
| Real failure predicates | PARTIAL | experiment/campaign predicates | campaign API | Needs verification | Predicate-style failures likely supported | Not yet Fenrix failure report flow | MVP-3 | MVP-3 |
| No-failure outcome is honest | MISSING now | campaign service | campaign API | Needs verification | Not yet Fenrix UI behavior | Need explicit "no failure found within tested budget/conditions" | MVP-3 | MVP-3 |
| Failure details | MISSING now | campaign/confirmation | campaign API | Needs verification | Not yet Fenrix failure detail UI | MVP-3 | MVP-3 |
| Replay timeline | MISSING now | persisted evidence | evidence APIs | Needs verification | Not yet Fenrix replay UI | MVP-3 | MVP-3 |
| Fix and retest with matched comparison | MISSING now | strategy versioning + experiment store | experiment API | Needs verification | Not yet Fenrix retest flow | MVP-3 + MVP-5 | MVP-3, MVP-5 |

## 7. Exchange agent benchmark in UI

| Requirement | Status | Implementing file / function | Existing route | Existing test | Observed behavior | Missing behavior | Planned change | PR |
|---|---|---|---|---|---|---|---|---|
| Reuse actual benchmark runner | PARTIAL | `app/benchmark/runner.py`, `app/benchmark/session.py`, `app/benchmark/tasks.py`, `app/benchmark/port.py`, `app/exchange/v2.py` | benchmark CLI / API | Existing benchmark tests | Benchmark machinery exists | Not yet Fenrix browser flow | MVP-4 | MVP-4 |
| Benchmark setup screen | MISSING | n/a | n/a | n/a | No Fenrix benchmark setup UI yet | Need bounded setup UI | MVP-4 | MVP-4 |
| Real benchmark results displayed | MISSING | `run_benchmark()` results | benchmark API | Needs verification | Not yet Fenrix UI | Need truthful score/gap presentation | MVP-4 | MVP-4 |
| Invalid-agent semantics | PARTIAL | benchmark + port security model | benchmark API | Needs verification | Invalid-agent handling likely exists | Not yet Fenrix UI explanation | MVP-4 | MVP-4 |
| Long-running job handling | PARTIAL | experiment job infrastructure | `/api/enterprise/experiment-jobs` | Manual | Resumable job model exists in stress lab | Not yet Fenrix benchmark job UX | MVP-4 | MVP-4 |
| Dataset Releases inspection | MISSING | corpus + release metadata | corpus endpoints | Needs verification | M10.7 corpus exists | No Fenrix dataset release browser yet | MVP-4 | MVP-4 |

## 8. Comparison, evidence, decision support

| Requirement | Status | Implementing file / function | Existing route | Existing test | Observed behavior | Missing behavior | Planned change | PR |
|---|---|---|---|---|---|---|---|---|
| Saved research runs | MISSING | experiment store exists but not Fenrix results history | experiment API | Needs verification | Backend storage exists | No Fenrix results history UI yet | MVP-5 | MVP-5 |
| Version comparison | MISSING | strategy + experiment store | experiment API | Needs verification | Not yet Fenrix comparison UI | MVP-5 | MVP-5 |
| Evidence-linked explanations | MISSING | result data exists; no deterministic explanation layer yet | n/a | n/a | Not yet implemented | MVP-5 | MVP-5 |
| Decision sensitivity diagnostic | MISSING | M10.8 workbook flags this as open; no implementation yet | n/a | n/a | Not yet implemented | MVP-5 (lower priority) | MVP-5 |
| Verifiable evidence export | MISSING | export endpoints exist in parts | experiment export APIs | Needs verification | Not yet Fenrix complete package export flow | MVP-5 | MVP-5 |

## 9. Release hardening

| Requirement | Status | Implementing file / function | Existing route | Existing test | Observed behavior | Missing behavior | Planned change | PR |
|---|---|---|---|---|---|---|---|---|
| Fresh install offline demo | PARTIAL | demo fixture provider exists | `make run` + static pages + API | Needs MVP verification | Local demo likely usable | No explicit `make mvp-demo` yet | MVP-6 | MVP-6 |
| Browser E2E onboarding + real strategy + failure + exchange + evidence | MISSING | Playwright tests referenced | n/a | `tests/browser_e2e_strategy_lab.py` may exist; verify | Not yet complete MVP E2E suite | MVP-6 | MVP-6 |
| Financial correctness regressions | PARTIAL | existing strategy/exchange/corpus tests | test suites | Existing tests exist | Some correctness tests exist | Need MVP regression pass on final commit | MVP-6 | MVP-6 |
| Security/privacy audit of new endpoints | MISSING | n/a | n/a | n/a | Not yet done for MVP endpoints | MVP-6 | MVP-6 |
| UI quality audit | MISSING | n/a | n/a | n/a | Not yet done | MVP-6 | MVP-6 |
| MVP acceptance report + release tag | MISSING | n/a | n/a | n/a | Not yet done | MVP-6 | MVP-6 |

## 10. Quick verdict

- MVP-0: NOT STARTED. M10.8 doc branch exists and is pushed; merge/confirm and then begin implementation.
- MVP-1: MOSTLY MISSING as a unified Fenrix workspace. Existing `strategy-lab.html` is the best starting shell fragment.
- MVP-2: CRITICAL UNKNOWN. Compiler and pipeline exist, but Fenrix does not yet expose a complete executable strategy validation flow with verified trade/accounting results.
- MVP-3: MISSING Fenrix failure/replay/retest flow; backend campaign/stress machinery likely available.
- MVP-4: MISSING Fenrix exchange benchmark UI; backend benchmark exists.
- MVP-5: MISSING comparison/evidence/export/sensitivity.
- MVP-6: MISSING E2E, security audit, UI audit, release candidate.

Shortest correct path: MVP-1 (unified shell + project + offline demo onboarding) feeding MVP-2 (real executable strategy validation). Those two are the MVP blockers.
