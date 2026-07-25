from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class WorldManifest(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    manifest_id: str = Field(min_length=3, max_length=120)
    family_id: str = Field(min_length=2, max_length=80)
    generator_version: str = Field(min_length=1, max_length=40)
    seed: int = Field(ge=0, le=2_147_483_647)
    entropy_source: Literal["ENTROPY", "FROZEN", "DISCRETE_DIFFICULTY"]
    created_at: str
    generator_assumptions: tuple[str, ...]
    limitations: tuple[str, ...]
    supported_claims: tuple[str, ...]
    prohibited_claims: tuple[str, ...]
    calibration_pack_id: str | None = None
    asset_manifest: AssetManifest
    parameter_overrides: dict[str, float | int] | None = None
    seed_manifest: SeedManifest
    stylized_fact_diagnostics: dict[str, float | int]
    correlation_stress_applied: CorrelationStressManifest | None = None
    anonymization_manifest: AnonymizationManifest | None = None
    leakage_tests: LeakageManifest | None = None
    digest: str
    signature_alg: Literal["sha256"] = "sha256"

    def canonical_json(self) -> str:
        import json

        data = self.__class__.model_dump(self, mode="json", exclude={"digest", "signature_alg"})
        return json.dumps(data, sort_keys=True, separators=(",", ":"))

    def recompute_digest(self) -> str:
        import hashlib

        raw = self.canonical_json()
        return hashlib.sha256(raw.encode()).hexdigest()


class AssetManifest(StrictModel):
    asset_count: int = Field(ge=1, le=10_000)
    real_ticker_count: int = Field(default=0, ge=0, le=10_000)
    synthetic_asset_count: int = Field(default=0, ge=0, le=10_000)
    anonymized_count: int = Field(default=0, ge=0, le=10_000)
    strategy_asset_ticker: str | None = None
    sector_manifest: list[SectorManifestEntry] = Field(default_factory=list, max_length=200)
    corporate_actions: dict[str, dict[str, object]] | None = None
    delisted_assets: tuple[str, ...] = ()


class SectorManifestEntry(StrictModel):
    sector: str
    asset_count: int
    anonymized: bool = False
    heldout: bool = False
    forward_injected: bool = False


class SeedManifest(StrictModel):
    policy: Literal["ENTROPY", "FROZEN", "DISCRETE_DIFFICULTY"]
    user_supplied_seed: int | None = None
    derived_session_hash: str | None = None
    universe_seed_hash: str | None = None
    challenge_seed: str | None = None
    commit_hash: str | None = None


class CorrelationStressManifest(StrictModel):
    scenario: Literal[
        "base",
        "neutral",
        "flight_to_quality",
        "dollar_crunch",
        "commodity_surge",
        "crypto_contagion",
    ]
    applied_at: str
    multiplier: float = Field(gt=0.0)
    offdiagonal_scale_bps: float | None = None
    factor_rotations: dict[str, tuple[float, float]] | None = None


class AnonymizationManifest(StrictModel):
    mode: Literal["PROMPT_SAFE", "FULL_DECODED", "HELDOUT_HIDDEN"]
    applied: bool
    heldout_sector_forward_count: int = Field(ge=0)
    prompt_decoded_tickers: tuple[str, ...] = ()
    decoded_names: dict[str, str] | None = None
    decoded_sector_map: dict[str, str] | None = None
    limits_applied: tuple[str, ...] = ()


class LeakageManifest(StrictModel):
    world_version: str
    reference_checksums: dict[str, str]
    exact_duplicate_trajectories: bool = False
    nearest_window_correlation: float | None = None
    nearest_window_normalized_rmse: float | None = None
    similarity_warning: bool
    empirical_bootstrap_train_test_distance: float | None = None
    empirical_bootstrap_acceptance_threshold: float = Field(gt=0.0)
    passed: bool
