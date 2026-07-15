"""Deterministic Quant Challenge Arena portfolio challenge engine.

The Arena deliberately evaluates CSV positions rather than executing uploaded code. GPT may
author challenge prose or explain measured evidence, but this module owns data generation,
validation, scoring, integrity labels, and ranking.
"""

from __future__ import annotations

import csv
import hashlib
import io
import math
import os
import statistics
from datetime import UTC, date, datetime, timedelta
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ARENA_SCHEMA_VERSION = "1.0"
GENERATOR_VERSION = "arena-regime-1.0"
REGIME_TYPES = {
    "momentum",
    "mean_reversion",
    "low_volatility",
    "volatility_expansion",
    "liquidity_shock",
    "structural_break",
    "false_predictive_feature",
    "lookahead_trap",
}
PUBLIC_REGIME_TYPES = {"momentum", "mean_reversion", "low_volatility"}
REQUIRED_COLUMNS = ("date", "asset", "position")
MAX_CSV_BYTES = 1_000_000
MAX_CSV_ROWS = 10_000
POSITION_LIMIT = 1.0
GROSS_EXPOSURE_LIMIT = 1.5
NET_EXPOSURE_LIMIT = 1.0


class HiddenScenarioSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    regime_type: str
    educational_purpose: str = Field(min_length=1, max_length=500)
    severity: float = Field(ge=0, le=1)
    rationale: str = Field(min_length=1, max_length=500)

    @field_validator("regime_type")
    @classmethod
    def valid_regime(cls, value: str) -> str:
        if value not in REGIME_TYPES:
            raise ValueError(f"unsupported regime type: {value}")
        return value


class ChallengeGeneration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=3, max_length=160)
    student_brief: str = Field(min_length=20, max_length=3_000)
    learning_objectives: list[str] = Field(min_length=1, max_length=8)
    permitted_inputs: list[str] = Field(min_length=1, max_length=12)
    prohibited_methods: list[str] = Field(min_length=1, max_length=12)
    public_data_description: str = Field(min_length=10, max_length=1_000)
    hidden_scenario_specs: list[HiddenScenarioSpec] = Field(min_length=1, max_length=8)
    likely_student_mistakes: list[str] = Field(min_length=1, max_length=8)
    instructor_rubric: list[str] = Field(min_length=1, max_length=8)
    limitations: list[str] = Field(min_length=1, max_length=8)


class ChallengeSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    challenge_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{2,80}$")
    schema_version: str = ARENA_SCHEMA_VERSION
    title: str = Field(min_length=3, max_length=160)
    narrative: str = Field(min_length=20, max_length=3_000)
    learning_objectives: list[str] = Field(min_length=1, max_length=8)
    assets: list[str] = Field(min_length=2, max_length=32)
    public_start: date
    public_end: date
    hidden_start: date
    hidden_end: date
    dataset_seed: int = Field(ge=0, le=2_147_483_647)
    generator_version: str = GENERATOR_VERSION
    public_regime_manifest: list[dict[str, Any]] = Field(min_length=1, max_length=16)
    hidden_regime_manifest: list[HiddenScenarioSpec] = Field(min_length=1, max_length=16)
    scoring_configuration: dict[str, Any]
    release_policy: dict[str, Any]
    created_by: str = Field(min_length=1, max_length=120)
    created_at: datetime
    approved_at: datetime | None = None
    generation: ChallengeGeneration | None = None

    @model_validator(mode="after")
    def valid_windows(self) -> ChallengeSpec:
        if not self.public_start <= self.public_end:
            raise ValueError("public window is invalid")
        if not self.hidden_start <= self.hidden_end:
            raise ValueError("hidden window is invalid")
        if self.public_end >= self.hidden_start:
            raise ValueError("public and hidden windows overlap")
        if len(set(self.assets)) != len(self.assets):
            raise ValueError("assets must be unique")
        if self.scoring_configuration.get("version") is None:
            raise ValueError("scoring configuration must have a version")
        return self

    def public_dates(self) -> list[date]:
        return _date_range(self.public_start, self.public_end)

    def hidden_dates(self) -> list[date]:
        return _date_range(self.hidden_start, self.hidden_end)

    def specification_hash(self) -> str:
        payload = self.model_dump(mode="json", exclude={"hidden_regime_manifest"})
        return hashlib.sha256(_stable_json(payload).encode()).hexdigest()[:16]


class FeedbackHypothesis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=160)
    evidence_ids: list[str] = Field(min_length=1, max_length=8)
    explanation: str = Field(min_length=1, max_length=1_000)


class StrategyFeedback(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1, max_length=1_000)
    strengths: list[str] = Field(min_length=1, max_length=8)
    weaknesses: list[str] = Field(min_length=1, max_length=8)
    economic_logic_assessment: str = Field(min_length=1, max_length=1_500)
    robustness_failures: list[FeedbackHypothesis] = Field(default_factory=list, max_length=8)
    leakage_discussion: str = Field(min_length=1, max_length=1_000)
    recommended_improvements: list[str] = Field(min_length=1, max_length=8)
    suggested_next_experiments: list[str] = Field(min_length=1, max_length=8)
    limitations: list[str] = Field(min_length=1, max_length=8)


class ArenaClient(Protocol):
    responses: Any


CHALLENGE_PROMPT = """You are an instructor's quantitative-finance challenge designer.
Return only the requested structured challenge content. Choose regime_type values only from the
allow-list. Describe educational intent, never scores or numeric price paths. The deterministic
engine will generate all data and hidden verdicts. Do not claim that a flag proves misconduct."""

ARENA_FEEDBACK_PROMPT = """You are a quantitative-finance teaching assistant.
Explain only the deterministic evaluation evidence supplied by the application. Do not score,
rank, or decide any verdict. Every evidence ID in robustness_failures must be copied from the
allowed list. Explicitly state that the challenge is fictional and deterministic. Distinguish
measured evidence from interpretation and never imply investment advice."""


def _stable_json(value: Any) -> str:
    import json

    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _date_range(start: date, end: date) -> list[date]:
    return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]


def _utc_now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def deterministic_generation() -> ChallengeGeneration:
    return ChallengeGeneration(
        title="When the Backtest Winner Loses",
        student_brief=(
            "Build a long-short position strategy using the public synthetic panel. Your goal is "
            "to explain an economic mechanism, control exposure, and show that your result survives "
            "a hidden regime change. Submit one position for every public date and asset."
        ),
        learning_objectives=[
            "Separate visible historical performance from out-of-sample robustness.",
            "Measure turnover, costs, concentration, and regime dependence.",
            "Interpret a stress-test result without claiming certainty about real markets.",
        ],
        permitted_inputs=["date", "asset", "position", "public feature columns"],
        prohibited_methods=["future hidden dates", "arbitrary code execution", "external price data"],
        public_data_description="A deterministic fictional three-asset panel with public momentum and a tempting false feature.",
        hidden_scenario_specs=[
            HiddenScenarioSpec(
                regime_type="structural_break",
                educational_purpose="Test whether a relationship survives a change in sign.",
                severity=0.8,
                rationale="The public winner is concentrated in the relationship that changes.",
            ),
            HiddenScenarioSpec(
                regime_type="lookahead_trap",
                educational_purpose="Test whether a one-period information delay changes the result.",
                severity=0.6,
                rationale="A same-period signal should not be treated as available before the return.",
            ),
            HiddenScenarioSpec(
                regime_type="liquidity_shock",
                educational_purpose="Test sensitivity to higher implementation costs.",
                severity=0.5,
                rationale="Turnover and concentration make costs matter more than the public score suggests.",
            ),
            HiddenScenarioSpec(
                regime_type="false_predictive_feature",
                educational_purpose="Test whether a predictive-looking feature generalizes.",
                severity=0.85,
                rationale="The public feature is deliberately unstable and must not be treated as truth.",
            ),
        ],
        likely_student_mistakes=[
            "Optimizing only the public Sharpe ratio.",
            "Ignoring costs and concentration.",
            "Treating a same-period feature as available before the return.",
        ],
        instructor_rubric=[
            "Correctly describes the economic mechanism and its limits.",
            "Reports both public and hidden evidence.",
            "Proposes a falsifiable next experiment.",
        ],
        limitations=[
            "The market is fictional and deterministic for reproducibility.",
            "Results are an assessment exercise, not investment advice.",
        ],
    )


def build_challenge(
    *,
    challenge_id: str = "momentum-regime-reversal",
    title: str | None = None,
    course_level: str = "advanced undergraduate / MFE",
    learning_objective: str | None = None,
    asset_count: int = 3,
    periods: int = 16,
    dataset_seed: int = 20260715,
    generated: ChallengeGeneration | None = None,
) -> ChallengeSpec:
    if asset_count != 3 or periods != 16:
        raise ValueError("the MVP deterministic bundle supports exactly 3 assets and 16 periods")
    generation = generated or deterministic_generation()
    public_start = date(2026, 1, 1)
    public_end = date(2026, 1, 8)
    hidden_start = date(2026, 1, 9)
    hidden_end = date(2026, 1, 16)
    narrative = (
        f"{generation.student_brief} This {course_level} challenge rewards a strategy that "
        "generalizes beyond the visible momentum regime rather than one that merely wins the leaderboard."
    )
    if learning_objective:
        generation = generation.model_copy(
            update={"learning_objectives": [learning_objective, *generation.learning_objectives[:2]]}
        )
    return ChallengeSpec(
        challenge_id=challenge_id,
        title=title or generation.title,
        narrative=narrative,
        learning_objectives=generation.learning_objectives,
        assets=[f"ASSET_{index:02d}" for index in range(1, 4)],
        public_start=public_start,
        public_end=public_end,
        hidden_start=hidden_start,
        hidden_end=hidden_end,
        dataset_seed=dataset_seed,
        public_regime_manifest=[
            {"regime_type": "momentum", "start": public_start.isoformat(), "end": public_end.isoformat()}
        ],
        hidden_regime_manifest=generation.hidden_scenario_specs,
        scoring_configuration={
            "version": "score-1.0",
            "weights": {
                "public_performance": 0.20,
                "hidden_performance": 0.25,
                "regime_stability": 0.20,
                "operational_robustness": 0.15,
                "concentration_and_tail": 0.10,
                "explanation_quality_separate": 0.10,
            },
        },
        release_policy={
            "public_metrics": "immediate",
            "hidden_results": "instructor_only_until_release",
            "feedback": "deterministic_or_grounded_ai",
        },
        created_by="build-week-demo",
        created_at=_utc_now(),
        generation=generation,
    )


def public_challenge(challenge: ChallengeSpec) -> dict[str, Any]:
    value = challenge.model_dump(mode="json", exclude={"hidden_regime_manifest"})
    value.pop("generation", None)
    value["hidden_period"] = {"starts_after_public": True, "results_released_by_instructor": True}
    return value


def _regime_for_date(challenge: ChallengeSpec, current: date) -> str:
    if current <= challenge.public_end:
        return "momentum"
    hidden_index = (current - challenge.hidden_start).days
    specs = challenge.hidden_regime_manifest
    return specs[min(hidden_index // 2, len(specs) - 1)].regime_type


def generate_dataset(challenge: ChallengeSpec, *, include_hidden: bool = False) -> list[dict[str, Any]]:
    """Generate the same fictional panel for a challenge seed every time."""
    rows: list[dict[str, Any]] = []
    dates = challenge.public_dates() + (challenge.hidden_dates() if include_hidden else [])
    for current in dates:
        regime = _regime_for_date(challenge, current)
        index = (current - challenge.public_start).days
        public = current <= challenge.public_end
        for asset_index, asset in enumerate(challenge.assets):
            if public:
                base_returns = (
                    0.020 if index % 2 == 0 else 0.016,
                    -0.005 if index % 2 == 0 else 0.004,
                    0.012 if index % 2 == 0 else -0.004,
                )
                false_feature = (1.0, -0.3, 0.2)[asset_index]
                true_feature = (0.6, -0.1, 0.4)[asset_index]
                delayed_return = base_returns[asset_index] * (0.65 if index > 0 else 0.0)
            else:
                base_returns = {
                    "structural_break": (-0.014, 0.005, 0.008),
                    "lookahead_trap": (-0.011, 0.004, 0.007),
                    "liquidity_shock": (-0.010, 0.004, 0.008),
                    "false_predictive_feature": (-0.013, 0.005, 0.009),
                }.get(regime, (-0.008, 0.004, 0.007))
                false_feature = (-1.0, 0.2, 0.4)[asset_index]
                true_feature = (0.1, 0.3, 0.7)[asset_index]
                delayed_return = base_returns[asset_index] * (1.35 if asset_index == 0 else 0.95)
            rows.append(
                {
                    "date": current.isoformat(),
                    "asset": asset,
                    "return": round(base_returns[asset_index], 8),
                    "delayed_return": round(delayed_return, 8),
                    "momentum_feature": round(true_feature, 4),
                    "false_predictive_feature": round(false_feature, 4),
                    "liquidity_multiplier": 0.55 if regime == "liquidity_shock" else 1.0,
                    "regime": regime,
                    "latent_regime": regime if include_hidden else None,
                }
            )
    if not include_hidden:
        for row in rows:
            row.pop("latent_regime", None)
            row.pop("regime", None)
    return rows


def dataset_hash(rows: list[dict[str, Any]]) -> str:
    return hashlib.sha256(_stable_json(rows).encode()).hexdigest()[:16]


def public_dataset(challenge: ChallengeSpec) -> dict[str, Any]:
    rows = generate_dataset(challenge, include_hidden=False)
    return {
        "challenge_id": challenge.challenge_id,
        "dataset_hash": dataset_hash(rows),
        "columns": ["date", "asset", "return", "momentum_feature", "false_predictive_feature"],
        "rows": rows,
        "hidden_dates_included": False,
    }


def instructor_dataset(challenge: ChallengeSpec) -> dict[str, Any]:
    rows = generate_dataset(challenge, include_hidden=True)
    return {
        "challenge_id": challenge.challenge_id,
        "dataset_hash": dataset_hash(rows),
        "rows": rows,
        "hidden_dates_included": True,
        "hidden_regimes": [item.model_dump(mode="json") for item in challenge.hidden_regime_manifest],
    }


def _position_csv(rows: list[tuple[str, str, float]]) -> str:
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(REQUIRED_COLUMNS)
    writer.writerows(rows)
    return output.getvalue()


def example_submission(challenge: ChallengeSpec, label: str) -> str:
    if label not in {"backtest_winner", "robust_generalizer"}:
        raise ValueError("unknown example submission")
    rows: list[tuple[str, str, float]] = []
    for current in challenge.public_dates():
        if label == "backtest_winner":
            positions = (0.9, -0.1, 0.0)
        else:
            positions = (0.0, 0.2, 0.6)
        for asset, position in zip(challenge.assets, positions, strict=True):
            rows.append((current.isoformat(), asset, position))
    return _position_csv(rows)


def _parse_position_rows(csv_text: str, challenge: ChallengeSpec) -> tuple[list[dict[str, Any]], list[str]]:
    errors: list[str] = []
    if len(csv_text.encode()) > MAX_CSV_BYTES:
        return [], [f"file exceeds {MAX_CSV_BYTES} bytes"]
    reader = csv.DictReader(io.StringIO(csv_text))
    if reader.fieldnames is None:
        return [], ["CSV is missing a header"]
    if tuple(reader.fieldnames) != REQUIRED_COLUMNS:
        errors.append(f"required columns are exactly {', '.join(REQUIRED_COLUMNS)}")
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    public_dates = {item.isoformat() for item in challenge.public_dates()}
    hidden_dates = {item.isoformat() for item in challenge.hidden_dates()}
    for row_number, row in enumerate(reader, start=2):
        if row_number > MAX_CSV_ROWS + 1:
            errors.append(f"row count exceeds {MAX_CSV_ROWS}")
            break
        current = str(row.get("date", "")).strip()
        asset = str(row.get("asset", "")).strip()
        raw_position = str(row.get("position", "")).strip()
        try:
            parsed_date = date.fromisoformat(current)
        except ValueError:
            errors.append(f"row {row_number}: date is not ISO parseable")
            continue
        if current in hidden_dates:
            errors.append(f"row {row_number}: hidden-period date is not allowed")
        elif current not in public_dates:
            errors.append(f"row {row_number}: date is outside the public challenge window")
        if asset not in challenge.assets:
            errors.append(f"row {row_number}: unknown asset {asset}")
        key = (current, asset)
        if key in seen:
            errors.append(f"row {row_number}: duplicate date-and-asset key")
        seen.add(key)
        try:
            position = float(raw_position)
        except ValueError:
            errors.append(f"row {row_number}: position is not numeric")
            continue
        if not math.isfinite(position):
            errors.append(f"row {row_number}: position must be finite")
        if abs(position) > POSITION_LIMIT:
            errors.append(f"row {row_number}: position exceeds +/-{POSITION_LIMIT}")
        rows.append({"date": parsed_date, "asset": asset, "position": position})
    by_date: dict[date, list[float]] = {}
    for row in rows:
        by_date.setdefault(row["date"], []).append(row["position"])
    expected_dates = {item for item in challenge.public_dates()}
    missing_dates = expected_dates - set(by_date)
    if missing_dates:
        errors.append(
            "missing complete public dates: " + ", ".join(sorted(item.isoformat() for item in missing_dates))
        )
    for current_date, positions in by_date.items():
        gross = sum(abs(item) for item in positions)
        net = abs(sum(positions))
        if gross > GROSS_EXPOSURE_LIMIT + 1e-9:
            errors.append(f"{current_date.isoformat()}: gross exposure exceeds {GROSS_EXPOSURE_LIMIT}")
        if net > NET_EXPOSURE_LIMIT + 1e-9:
            errors.append(f"{current_date.isoformat()}: net exposure exceeds {NET_EXPOSURE_LIMIT}")
    rows.sort(key=lambda item: (item["date"], item["asset"]))
    return rows, errors


def validate_submission_csv(csv_text: str, challenge: ChallengeSpec) -> dict[str, Any]:
    rows, errors = _parse_position_rows(csv_text, challenge)
    normalized = [
        {"date": item["date"].isoformat(), "asset": item["asset"], "position": round(item["position"], 8)}
        for item in rows
    ]
    return {
        "valid": not errors,
        "errors": errors,
        "warnings": ["Positions are assessed only inside the declared fictional challenge market."],
        "row_count": len(rows),
        "normalized_rows": normalized if not errors else [],
    }


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return round(max(low, min(high, value)), 2)


def _annualized_return(values: list[float]) -> float:
    if not values:
        return 0.0
    growth = math.prod(1 + value for value in values)
    return growth ** (252 / len(values)) - 1 if growth > 0 else -1.0


def _annualized_volatility(values: list[float]) -> float:
    return statistics.pstdev(values) * math.sqrt(252) if len(values) > 1 else 0.0


def _sharpe(values: list[float]) -> float:
    volatility = _annualized_volatility(values)
    return (
        statistics.fmean(values) * math.sqrt(252) / (volatility / math.sqrt(252) if volatility else 1)
        if values
        else 0.0
    )


def _drawdown(values: list[float]) -> float:
    wealth = 1.0
    high = 1.0
    worst = 0.0
    for value in values:
        wealth *= 1 + value
        high = max(high, wealth)
        worst = min(worst, wealth / high - 1)
    return abs(worst)


def _positions_by_date(rows: list[dict[str, Any]], challenge: ChallengeSpec) -> dict[date, dict[str, float]]:
    values = {current: {asset: 0.0 for asset in challenge.assets} for current in challenge.public_dates()}
    for row in rows:
        values[row["date"]][row["asset"]] = row["position"]
    return values


def _portfolio_series(
    rows: list[dict[str, Any]],
    challenge: ChallengeSpec,
    dataset: list[dict[str, Any]],
    *,
    period: str,
    cost_bps: float = 5.0,
    delayed: bool = False,
) -> tuple[list[float], list[dict[str, Any]], float, float]:
    positions = _positions_by_date(rows, challenge)
    all_dates = challenge.public_dates() if period == "public" else challenge.hidden_dates()
    data = {(date.fromisoformat(item["date"]), item["asset"]): item for item in dataset}
    last_public = positions[challenge.public_end]
    # A one-day signal delay means the first hidden observation uses the last
    # position known at the end of the public period, not an artificial zero
    # portfolio. Subsequent hidden observations continue with that lagged
    # position map for the built-in fixed-position examples.
    previous = (
        dict(last_public) if delayed and period == "hidden" else {asset: 0.0 for asset in challenge.assets}
    )
    series: list[float] = []
    contributions: list[dict[str, Any]] = []
    turnover = 0.0
    total_cost = 0.0
    for current in all_dates:
        current_positions = positions.get(current, last_public)
        if delayed and current == challenge.hidden_start:
            current_positions = previous
        turnover_step = sum(abs(current_positions[asset] - previous[asset]) for asset in challenge.assets)
        cost = turnover_step * cost_bps / 10_000
        total_cost += cost
        turnover += turnover_step
        raw = sum(
            current_positions[asset] * data[(current, asset)]["delayed_return" if delayed else "return"]
            for asset in challenge.assets
        )
        net = raw - cost
        series.append(net)
        contributions.append(
            {
                "date": current.isoformat(),
                "regime": _regime_for_date(challenge, current),
                "return": round(net, 8),
                "gross_exposure": round(sum(abs(current_positions[asset]) for asset in challenge.assets), 6),
                "net_exposure": round(sum(current_positions.values()), 6),
                "cost": round(cost, 8),
            }
        )
        previous = dict(current_positions)
    return series, contributions, turnover, total_cost


def evaluate_submission(
    challenge: ChallengeSpec, csv_text: str, *, submission_id: str = "draft"
) -> dict[str, Any]:
    validation = validate_submission_csv(csv_text, challenge)
    if not validation["valid"]:
        return {"submission_id": submission_id, "valid": False, "validation": validation}
    rows = [
        {"date": date.fromisoformat(item["date"]), "asset": item["asset"], "position": item["position"]}
        for item in validation["normalized_rows"]
    ]
    public_data = generate_dataset(challenge, include_hidden=False)
    hidden_data = generate_dataset(challenge, include_hidden=True)
    public_series, public_contributions, public_turnover, public_cost = _portfolio_series(
        rows, challenge, public_data, period="public"
    )
    hidden_series, hidden_contributions, hidden_turnover, hidden_cost = _portfolio_series(
        rows, challenge, hidden_data, period="hidden"
    )
    delayed_series, _, _, _ = _portfolio_series(rows, challenge, hidden_data, period="hidden", delayed=True)
    stressed_series, _, _, stressed_cost = _portfolio_series(
        rows, challenge, hidden_data, period="hidden", cost_bps=15.0
    )
    positions = _positions_by_date(rows, challenge)
    asset_exposure = {
        asset: statistics.fmean(abs(positions[current][asset]) for current in challenge.public_dates())
        for asset in challenge.assets
    }
    largest_concentration = max(asset_exposure.values()) / max(sum(asset_exposure.values()), 1e-9)
    largest_period_contribution = max(
        (abs(item["return"]) for item in public_contributions), default=0
    ) / max(sum(abs(item["return"]) for item in public_contributions), 1e-9)
    regime_returns: dict[str, list[float]] = {}
    for item in hidden_contributions:
        regime_returns.setdefault(item["regime"], []).append(item["return"])
    regime_stats = {
        regime: {
            "return": round(statistics.fmean(values), 6),
            "sharpe": round(_sharpe(values), 4),
            "observations": len(values),
        }
        for regime, values in regime_returns.items()
    }
    public_sharpe = _sharpe(public_series)
    hidden_sharpe = _sharpe(hidden_series)
    # Operational sensitivities are expressed as average per-period basis-point
    # changes, which remains interpretable even when a strategy's return series
    # has near-zero variance. Sharpe differences would otherwise dominate the
    # score for a deterministic classroom fixture.
    delay_delta = (statistics.fmean(delayed_series) - statistics.fmean(hidden_series)) * 10_000
    cost_delta = (stressed_cost - hidden_cost) * 10_000
    public_metrics = {
        "annualized_return": round(_annualized_return(public_series), 6),
        "annualized_volatility": round(_annualized_volatility(public_series), 6),
        "sharpe": round(public_sharpe, 4),
        "maximum_drawdown": round(_drawdown(public_series), 6),
        "calmar": round(_annualized_return(public_series) / max(_drawdown(public_series), 1e-9), 4),
        "turnover": round(public_turnover, 6),
        "estimated_transaction_costs": round(public_cost, 8),
        "gross_exposure": round(statistics.fmean(item["gross_exposure"] for item in public_contributions), 6),
        "net_exposure": round(
            statistics.fmean(abs(item["net_exposure"]) for item in public_contributions), 6
        ),
        "largest_asset_concentration": round(largest_concentration, 6),
        "largest_period_contribution": round(largest_period_contribution, 6),
    }
    hidden_metrics = {
        "hidden_sharpe": round(hidden_sharpe, 4),
        "hidden_drawdown": round(_drawdown(hidden_series), 6),
        "hidden_annualized_return": round(_annualized_return(hidden_series), 6),
        "hidden_turnover": round(hidden_turnover, 6),
        "hidden_costs": round(hidden_cost, 8),
        "performance_degradation": round(hidden_sharpe - public_sharpe, 4),
        "cost_sensitivity": round(cost_delta, 4),
        "one_day_delay_sensitivity": round(delay_delta, 4),
        "liquidity_shock_sensitivity": round((stressed_cost - hidden_cost) * 10_000, 4),
        "turnover_sensitivity": round(hidden_turnover - public_turnover, 6),
        "contributor_concentration": round(largest_period_contribution, 6),
        "feature_collapse_sensitivity": round(
            regime_stats.get("false_predictive_feature", {}).get("return", 0), 6
        ),
        "exposure_stability": round(
            100 - statistics.pstdev([item["gross_exposure"] for item in public_contributions]) * 100, 4
        ),
        "regime_by_regime": regime_stats,
    }
    # Keep the leaderboard sensitive to differences among strong public results. A
    # large multiplier would clamp both demo strategies at 100 and erase the
    # intended public-winner/hidden-winner reversal.
    public_component = _clamp(50 + public_sharpe * 0.25)
    hidden_component = _clamp(50 + hidden_sharpe * 0.25)
    regime_component = _clamp(
        100 - statistics.pstdev(list(regime_returns.values())[0] if regime_returns else [0]) * 10_000
    )
    operational_component = _clamp(100 + min(delay_delta, 0.0) - max(cost_delta, 0.0))
    concentration_component = _clamp(100 - largest_concentration * 100)
    weights = challenge.scoring_configuration["weights"]
    robustness_score = round(
        public_component * weights["public_performance"]
        + hidden_component * weights["hidden_performance"]
        + regime_component * weights["regime_stability"]
        + operational_component * weights["operational_robustness"]
        + concentration_component * weights["concentration_and_tail"]
        + 50 * weights["explanation_quality_separate"],
        4,
    )
    integrity: list[dict[str, Any]] = []
    public_rows = validation["normalized_rows"]
    public_alignment = statistics.fmean(
        1.0
        if item["position"]
        * next(
            row["return"]
            for row in public_data
            if row["date"] == item["date"] and row["asset"] == item["asset"]
        )
        >= 0
        else 0.0
        for item in public_rows
    )
    if largest_concentration > 0.75 and public_alignment > 0.85:
        integrity.append(
            {
                "id": "integrity.same_period_alignment",
                "label": "Potential temporal leakage",
                "severity": "strong_indicator",
                "evidence": {
                    "public_alignment": round(public_alignment, 4),
                    "largest_asset_concentration": round(largest_concentration, 4),
                },
                "explanation": "Positions align unusually closely with same-period public returns while concentrating in one asset; manual review is recommended.",
            }
        )
    else:
        integrity.append(
            {
                "id": "integrity.same_period_alignment",
                "label": "No leakage evidence detected",
                "severity": "none_detected",
                "evidence": {"public_alignment": round(public_alignment, 4)},
                "explanation": "This deterministic check did not find unusually concentrated same-period alignment.",
            }
        )
    if hidden_sharpe < public_sharpe - 5:
        integrity.append(
            {
                "id": "integrity.hidden_collapse",
                "label": "Manual review recommended",
                "severity": "review",
                "evidence": {
                    "public_sharpe": round(public_sharpe, 4),
                    "hidden_sharpe": round(hidden_sharpe, 4),
                },
                "explanation": "Performance degrades materially after the hidden regime change; this is not a finding of intent.",
            }
        )
    return {
        "submission_id": submission_id,
        "valid": True,
        "validation": validation,
        "public_metrics": public_metrics,
        "hidden_metrics": hidden_metrics,
        "public_score": round(public_component * 0.7 + concentration_component * 0.3, 4),
        "robustness_score": robustness_score,
        "integrity_tests": integrity,
        "public_contributions": public_contributions,
        "hidden_contributions": hidden_contributions,
        "evidence": {
            "id": f"{submission_id}.arena-evidence",
            "challenge_id": challenge.challenge_id,
            "challenge_hash": challenge.specification_hash(),
            "public_dataset_hash": dataset_hash(public_data),
            "hidden_dataset_hash": dataset_hash(hidden_data),
            "generator_version": challenge.generator_version,
            "scoring_version": challenge.scoring_configuration["version"],
        },
    }


def deterministic_feedback(evaluation: dict[str, Any]) -> dict[str, Any]:
    public = evaluation["public_metrics"]
    hidden = evaluation["hidden_metrics"]
    failures = []
    if hidden["performance_degradation"] < -5:
        failures.append(
            FeedbackHypothesis(
                title="Hidden regime degradation",
                evidence_ids=["submission.hidden_sharpe", "submission.performance_degradation"],
                explanation="The hidden Sharpe is materially below the public Sharpe in the deterministic challenge bundle.",
            )
        )
    if public["largest_asset_concentration"] > 0.75:
        failures.append(
            FeedbackHypothesis(
                title="Concentration risk",
                evidence_ids=["submission.largest_asset_concentration"],
                explanation="Most average public exposure is concentrated in one asset, making the strategy sensitive to one relationship.",
            )
        )
    return {
        "status": "unavailable",
        "mode": "deterministic_fallback",
        "message": "GPT-5.6 feedback unavailable in no-key mode.",
        "feedback": StrategyFeedback(
            summary="The deterministic challenge shows how visible performance can differ from hidden robustness.",
            strengths=[
                "The submission is valid and reproducible.",
                "The economic explanation can be improved with explicit failure conditions.",
            ],
            weaknesses=["The hidden regime evidence should be discussed before drawing a conclusion."],
            economic_logic_assessment="Treat the public result as a hypothesis and compare it with the hidden regime decomposition.",
            robustness_failures=failures,
            leakage_discussion="Integrity labels are evidence for instructor review, not a conclusion about intent or misconduct.",
            recommended_improvements=[
                "Reduce concentration.",
                "Test a one-period delay and higher costs before claiming robustness.",
            ],
            suggested_next_experiments=[
                "Compare a diversified position set across each hidden regime.",
                "Run the same strategy with costs multiplied by three.",
            ],
            limitations=[
                "This is deterministic feedback about a fictional challenge environment.",
                "It is not investment advice or a definitive integrity finding.",
            ],
        ).model_dump(mode="json"),
        "evidence_ids": ["submission.hidden_sharpe", "submission.performance_degradation"],
    }


def validate_feedback_grounding(feedback: StrategyFeedback, evaluation: dict[str, Any]) -> StrategyFeedback:
    evidence_ids = {
        "submission.hidden_sharpe",
        "submission.performance_degradation",
        "submission.largest_asset_concentration",
        "submission.public_sharpe",
        "submission.cost_sensitivity",
        "submission.one_day_delay_sensitivity",
        "submission.feature_collapse_sensitivity",
    }
    references = [ref for row in feedback.robustness_failures for ref in row.evidence_ids]
    unknown = sorted(set(references) - evidence_ids)
    if unknown:
        raise ValueError("feedback referenced unknown evidence IDs: " + ", ".join(unknown))
    if not any(
        "fictional" in item.lower() or "deterministic" in item.lower() for item in feedback.limitations
    ):
        raise ValueError("feedback must disclose the deterministic fictional limitation")
    return feedback


def generate_feedback(
    evaluation: dict[str, Any],
    *,
    client: ArenaClient | None = None,
    api_key: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """Return grounded GPT feedback, with a deterministic fallback for no-key demos."""
    if client is None and not (api_key or os.getenv("OPENAI_API_KEY")):
        return deterministic_feedback(evaluation)
    active_client: Any = client
    if active_client is None:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError("OpenAI SDK is not installed; use the no-key fallback") from exc
        active_client = OpenAI(api_key=api_key, timeout=30.0, max_retries=2)
    selected_model = model or os.getenv("OPENAI_MODEL") or "gpt-5.6"
    allowed_evidence = {
        "submission.public_sharpe",
        "submission.hidden_sharpe",
        "submission.performance_degradation",
        "submission.largest_asset_concentration",
        "submission.cost_sensitivity",
        "submission.one_day_delay_sensitivity",
        "submission.feature_collapse_sensitivity",
    }
    evidence = {
        "public_metrics": evaluation.get("public_metrics", {}),
        "hidden_metrics": evaluation.get("hidden_metrics", {}),
        "public_score": evaluation.get("public_score"),
        "robustness_score": evaluation.get("robustness_score"),
        "allowed_evidence_ids": sorted(allowed_evidence),
        "limitations": [
            "This is a deterministic fictional challenge environment.",
            "Feedback cannot establish investment performance, intent, or misconduct.",
        ],
    }
    response = active_client.responses.parse(
        model=selected_model,
        input=[
            {"role": "system", "content": ARENA_FEEDBACK_PROMPT},
            {
                "role": "user",
                "content": "Explain this verified submission evidence:\n" + _stable_json(evidence),
            },
        ],
        text_format=StrategyFeedback,
        max_output_tokens=4_000,
    )
    parsed = response.output_parsed
    if isinstance(parsed, dict):
        parsed = StrategyFeedback.model_validate(parsed)
    if not isinstance(parsed, StrategyFeedback):
        raise ValueError("GPT-5.6 returned invalid structured challenge feedback")
    validated = validate_feedback_grounding(parsed, evaluation)
    return {
        "status": "complete",
        "mode": "gpt-5.6",
        "model": selected_model,
        "feedback": validated.model_dump(mode="json"),
        "evidence_ids": sorted(allowed_evidence),
    }


def generate_challenge_content(
    prompt: str,
    *,
    client: ArenaClient | None = None,
    api_key: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    if client is None and not (api_key or os.getenv("OPENAI_API_KEY")):
        content = deterministic_generation()
        return {
            "status": "unavailable",
            "mode": "deterministic_fallback",
            "model": None,
            "content": content.model_dump(mode="json"),
        }
    active_client: Any = client
    if active_client is None:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError("OpenAI SDK is not installed; use the no-key fallback") from exc
        active_client = OpenAI(api_key=api_key, timeout=30.0, max_retries=2)
    response = active_client.responses.parse(
        model=model or os.getenv("OPENAI_MODEL") or "gpt-5.6",
        input=[
            {"role": "system", "content": CHALLENGE_PROMPT},
            {"role": "user", "content": prompt[:2_000]},
        ],
        text_format=ChallengeGeneration,
        max_output_tokens=4_000,
    )
    parsed = response.output_parsed
    if isinstance(parsed, dict):
        parsed = ChallengeGeneration.model_validate(parsed)
    if not isinstance(parsed, ChallengeGeneration):
        raise ValueError("GPT-5.6 returned invalid structured challenge content")
    return {
        "status": "complete",
        "mode": "gpt-5.6",
        "model": model or os.getenv("OPENAI_MODEL") or "gpt-5.6",
        "content": parsed.model_dump(mode="json"),
    }
