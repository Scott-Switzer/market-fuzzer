from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CalibrationModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class MetricEstimate(CalibrationModel):
    value: float
    standard_error: float = Field(ge=0.0)
    ci_lower: float
    ci_upper: float
    unit: str = "unitless"

    @model_validator(mode="after")
    def ordered_interval(self) -> MetricEstimate:
        if self.ci_lower > self.value or self.value > self.ci_upper:
            raise ValueError("metric confidence interval must contain its value")
        return self


class AggregateWindow(CalibrationModel):
    name: Literal["train", "validation", "test"]
    start: datetime
    end: datetime
    row_count: int = Field(ge=3)
    metrics: dict[str, MetricEstimate]

    @model_validator(mode="after")
    def valid_window(self) -> AggregateWindow:
        if self.end < self.start:
            raise ValueError("aggregate window end must not precede start")
        if not self.metrics:
            raise ValueError("aggregate window requires metrics")
        return self


class CalibrationObjective(CalibrationModel):
    parameter: str
    metric: str
    distance: Literal["relative", "absolute"]
    tolerance: float = Field(gt=0.0)
    weight: float = Field(default=1.0, gt=0.0)


class CalibrationPackV1(CalibrationModel):
    """Shareable calibration evidence containing aggregates, never source rows."""

    schema_version: Literal["1.0"] = "1.0"
    pack_id: str = Field(min_length=3, max_length=100)
    source_kind: Literal["deterministic_demo", "canonical_user_csv"]
    source_url: str = Field(min_length=3)
    retrieval_date: date
    checksum: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    usage_basis: str = Field(min_length=3, max_length=300)
    instrument: str = Field(min_length=1, max_length=80)
    venue: str = Field(min_length=1, max_length=80)
    session: str = Field(min_length=1, max_length=80)
    canonical_columns: tuple[str, ...]
    raw_rows_retained: Literal[False] = False
    split_fractions: tuple[float, float, float] = (0.6, 0.2, 0.2)
    windows: tuple[AggregateWindow, AggregateWindow, AggregateWindow]
    objectives: tuple[CalibrationObjective, ...]
    notes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def complete_chronological_split(self) -> CalibrationPackV1:
        if abs(sum(self.split_fractions) - 1.0) > 1e-9 or any(x <= 0 for x in self.split_fractions):
            raise ValueError("split_fractions must be positive and sum to one")
        if tuple(window.name for window in self.windows) != (
            "train",
            "validation",
            "test",
        ):
            raise ValueError("windows must be ordered train, validation, test")
        if not (self.windows[0].end < self.windows[1].start <= self.windows[1].end < self.windows[2].start):
            raise ValueError("calibration windows must be disjoint and chronological")
        return self

    def window(self, name: Literal["train", "validation", "test"]) -> AggregateWindow:
        return next(window for window in self.windows if window.name == name)


class ParameterSet(CalibrationModel):
    parameter_set_id: str = Field(pattern=r"^cal-[0-9a-f]{16}$")
    bootstrap_index: int = Field(ge=0)
    parameters: dict[str, float]
    validation_distance: float = Field(ge=0.0)
    heldout_distance: float = Field(ge=0.0)
    accepted: bool
    rejection_reasons: tuple[str, ...] = ()

    @model_validator(mode="after")
    def reason_consistency(self) -> ParameterSet:
        if self.accepted and self.rejection_reasons:
            raise ValueError("accepted parameter sets cannot have rejection reasons")
        if not self.accepted and not self.rejection_reasons:
            raise ValueError("rejected parameter sets require a reason")
        return self


class ParameterInterval(CalibrationModel):
    lower: float
    median: float
    upper: float
    identifiable: Literal["strong", "moderate", "weak"]
    relative_width: float = Field(ge=0.0)

    @model_validator(mode="after")
    def ordered(self) -> ParameterInterval:
        if not self.lower <= self.median <= self.upper:
            raise ValueError("parameter interval must be ordered")
        return self


class HeldoutStability(CalibrationModel):
    median_validation_distance: float = Field(ge=0.0)
    median_heldout_distance: float = Field(ge=0.0)
    degradation_ratio: float = Field(ge=0.0)
    stable: bool


class BootstrapCalibrationResult(CalibrationModel):
    mode: Literal["quick", "audit"]
    seed: int = Field(ge=0)
    requested_bootstraps: int = Field(ge=1, le=10)
    candidates_evaluated: int = Field(ge=1, le=30)
    metric_objectives: tuple[CalibrationObjective, ...]
    accepted_parameter_sets: tuple[ParameterSet, ...]
    rejected_parameter_sets: tuple[ParameterSet, ...]
    point_estimates: dict[str, float]
    bootstrap_intervals: dict[str, ParameterInterval]
    weakly_identified: tuple[str, ...]
    correlated_parameters: tuple[tuple[str, str], ...]
    heldout_stability: HeldoutStability
    method: Literal["aggregate_parametric_bootstrap"] = "aggregate_parametric_bootstrap"
    warnings: tuple[str, ...] = ()

    @model_validator(mode="after")
    def count_matches(self) -> BootstrapCalibrationResult:
        if len(self.accepted_parameter_sets) + len(self.rejected_parameter_sets) != self.candidates_evaluated:
            raise ValueError("accepted and rejected sets must equal candidates_evaluated")
        if self.mode == "quick" and len(self.accepted_parameter_sets) != 3:
            raise ValueError("quick calibration must produce exactly three accepted parameter sets")
        return self
