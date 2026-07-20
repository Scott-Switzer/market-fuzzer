# Decision evidence contract

`paired_decision_evidence` compares a candidate and baseline only on the same
declared world/seed block. It reports a paired mean effect, deterministic
nonparametric bootstrap interval, exact two-sided sign-test value, and
generator-family effects. It deliberately does not choose worlds, alter a
primary score, or infer live-market profitability.

The result is `insufficient_evidence` when fewer than eight unique paired
blocks are available or when the 95% bootstrap interval crosses zero. A metric
with a non-zero point estimate therefore cannot be presented as supported
without uncertainty evidence. Generator-family effects are sensitivity outputs,
not independent family-level inference.

The pairing follows common-random-number simulation comparisons; repeated
metric decisions must apply a declared multiplicity procedure before any
customer-facing discovery claim. See [Nelson (1991)](https://doi.org/10.1287/opre.39.4.583)
and [Benjamini and Hochberg (1995)](https://www.jstor.org/stable/2346101).

`DecisionMetricPolicyV1` fixes the metric vector, ranking weights, and false
discovery rate before artifact freeze. Its digest must be the campaign's
precommitted `scoring_policy_digest`. `sealed_decision_report` then rejects a
result that was produced under a different policy, retains every metric's
effect, interval, and adjusted value, and does not manufacture a scalar result
from the weights. Duplicate receipt/metric cells are invalid evidence, not an
opportunity for last-value-wins aggregation.

This is sealed synthetic-campaign evidence, not proof of live-market
profitability. `SealedV2WorldRunnerV1` now supplies a V2 exchange-backed metric
path; the older callback remains a compatibility seam and cannot support an
exchange-execution claim. Arena lifecycle integration, richer strategy commands,
and live-market fidelity evidence remain separate work.
