# Sealed evaluation product contract

## Product thesis

The product is a reproducible, sealed, procedurally generated market-evaluation system. It measures whether a frozen strategy artifact remains robust across declared mechanisms and hidden world families. Historical data is calibration evidence, not the hidden evaluation set.

The product cannot guarantee unbiasedness or that a strategy cannot learn public mechanisms. It mitigates those risks through transparent coverage, multiple generator families, fresh hidden seed material, family holdouts, no-network execution, and uncertainty-aware evidence.

## Validated initial scope

The target first validated profile is a single-venue, lit, multi-asset, cash-like continuous double auction with price-time priority. Its acceptance scope includes market and limit execution, TWAP/VWAP/POV/implementation-shortfall policies, market making, directional strategies, pairs/statistical arbitrage, cross-asset allocation, inventory, and risk management.

The core owns versioned `InstrumentSpec`, `VenueRules`, `SessionRules`, `SettlementRules`, `MarginRules`, `FeeRules`, and `MarketDataRules` interfaces. Futures-like instruments are the next extension.

Options, auction mechanisms, fragmented routing, dark pools, fixed income, OTC, real-market best execution, and venue-specific microstructure fidelity are unsupported until independently implemented and tested.

## One-engine rule

Arena, Strategy Stress Lab, and Market Fuzzer are separate workflows over one exchange kernel, strategy protocol, event ledger, and evidence system. A workflow may present different permissions and reports; it may not use a private scoring engine or invent incompatible fill semantics.

## Evaluation contract

1. Publish limited development worlds labeled public and non-ranking.
2. Freeze a strategy artifact and record its digest before evaluation.
3. Commit the primary campaign policy, generator bundle digests, family allocation, hidden parameter ranges, and a commitment to secret seed material before submissions close. Do not publish secret seeds at this stage.
4. After freeze, generate hidden same-family and hidden family-holdout worlds independently of the strategy.
5. Execute the artifact under strict observation-time and resource boundaries.
6. Finalize immutable manifests and results, then reveal enough commitment material for verification.
7. Run strategy-aware adaptive search only afterward and label it as a diagnostic rather than a primary ranking input.

Primary scores are campaign aggregates with uncertainty. A leaderboard, when used, is a challenge-author-locked metric vector and weights committed before the artifact freezes. Commercial reports retain the vector, uncertainty, and failure mechanism.

## Required evidence and acceptance criteria

- Identical declared specification, artifact digest, generator bundle, campaign commitment, and seed material reproduce byte-equivalent event-ledger bytes and result digest.
- Every order command has a validated outcome: acknowledgement or typed rejection; every admitted lifecycle transition appears in the ledger.
- Hidden observations exclude seeds, hidden IDs, future timestamps, generator metadata, and labels that can reveal a world.
- Primary-world selection never reads submitted strategy behavior; adaptive diagnostics are stored in a separate result namespace.
- A claim is shown only with its evidence scope, version/digest, sample size, uncertainty, and stated limitations.

## Permitted and prohibited claims

Permitted: deterministic synthetic evidence within declared mechanisms; bounded calibration evidence; protected family-holdout and adaptive-diagnostic results when the corresponding gates pass.

Prohibited: unbiasedness, impossible memorization, universal realism, live profitability, best execution, production readiness, or coverage of unsupported instruments/venues.
