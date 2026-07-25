from __future__ import annotations

import hashlib
import json
import math
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import numpy as np

from app.break_test.synthetic_market import FACTOR_NAMES


@dataclass(frozen=True)
class AssetFactorConfig:
    ticker: str
    company_name: str
    sector: str
    initial_price_ticks: int
    shares_outstanding: int
    initial_fundamental_value_ticks: int
    macro_beta: float
    idiosyncratic_volatility: float
    liquidity_profile: Literal["deep", "normal", "thin"] = "normal"
    event_sensitivity: float = 1.0
    mean_reversion: float = 0.02
    price_cache_factor_loading: float | None = None
    corporate_action: str | None = None
    delisting: dict[str, object] | None = None


DEFAULT_SECTORS = [
    "Technology",
    "Financials",
    "Health Care",
    "Industrials",
    "Energy",
    "Consumer Discretionary",
    "Consumer Staples",
    "Utilities",
    "Real Estate",
    "Communication Services",
    "Materials",
    "Crypto",
    "Fixed Income",
    "Macro/Rates",
    "Macro/FX",
    "Commodities/Metals",
]

_HELDOUT_SECTORS = {"Crypto", "Commodities/Metals"}

_TIME_VARYING_NOMENCLATURE_TEMPLATES = [
    "Synthetic Alpha {index:04d}",
    "Procedural Entity {index:04d}",
    "Synthetic Universe {index:04d}",
    "Generated Asset {index:04d}",
]


def _master_seed(session_id: str | None, universe_seed: int | None = None) -> np.random.Generator:
    if universe_seed is not None:
        seed_material = f"universe_seed:{universe_seed}"
    elif session_id is not None:
        seed_material = f"session:{session_id}"
    else:
        seed_material = f"timestamp:{datetime.now(UTC).isoformat()}"
    digest = hashlib.sha256(seed_material.encode("utf-8")).hexdigest()
    seed = int(digest[:16], 16)
    return np.random.default_rng(seed % (2**63 - 1))


def _session_sector_weights(session_id: str | None, rng: np.random.Generator) -> dict[str, float]:
    sectors = [s for s in DEFAULT_SECTORS if s not in _HELDOUT_SECTORS]
    raw = rng.uniform(0.2, 1.0, size=len(sectors))
    weights = raw / raw.sum()
    return dict(zip(sectors, weights.tolist(), strict=False))


def _pick_sector_for_asset(index: int, sector_weights: dict[str, float], rng: np.random.Generator) -> str:
    sectors = list(sector_weights.keys())
    weights = np.array([sector_weights[s] for s in sectors], dtype=float)
    weights = weights / weights.sum()
    choice = int(rng.choice(len(sectors), p=weights))
    return sectors[choice]


def _clamp(value: float, low: float, high: float) -> float:
    return float(max(low, min(high, value)))


def _factor_loading_for_sector(
    sector: str,
    asset_index: int,
    rng: np.random.Generator,
    *,
    jitter_std: float = 0.12,
) -> tuple[float, ...]:
    base_loadings = {
        "Technology": (0.85, 0.15, 0.25, 0.10, -0.25, 0.10, 0.18, 0.30),
        "Financials": (0.75, 0.20, 0.10, 0.05, 0.35, 0.40, 0.05, 0.10),
        "Health Care": (0.65, 0.18, 0.15, 0.20, -0.15, 0.15, 0.10, 0.20),
        "Industrials": (0.70, 0.22, 0.08, 0.35, -0.10, 0.25, 0.22, 0.15),
        "Energy": (0.55, 0.12, 0.05, -0.05, -0.45, 0.15, 0.55, 0.20),
        "Consumer Discretionary": (0.78, 0.16, 0.20, 0.15, -0.20, 0.10, 0.12, 0.25),
        "Consumer Staples": (0.50, 0.10, 0.05, -0.10, 0.10, 0.05, 0.05, 0.08),
        "Utilities": (0.35, 0.08, 0.05, -0.25, 0.70, 0.20, -0.05, 0.05),
        "Real Estate": (0.45, 0.12, 0.05, -0.15, 0.65, 0.30, 0.05, 0.08),
        "Communication Services": (0.82, 0.14, 0.28, 0.12, -0.22, 0.12, 0.20, 0.28),
        "Materials": (0.60, 0.15, 0.05, 0.25, -0.20, 0.20, 0.45, 0.18),
        "Crypto": (0.20, 0.15, 0.30, 0.35, -0.50, 0.30, 0.40, 0.90),
        "Fixed Income": (0.08, 0.05, 0.00, -0.05, 0.75, 0.70, -0.10, -0.05),
        "Macro/Rates": (0.05, -0.08, 0.05, 0.05, 0.85, 0.15, -0.05, -0.10),
        "Macro/FX": (0.05, 0.02, 0.05, 0.10, 0.75, 0.10, 0.10, -0.08),
        "Commodities/Metals": (0.12, 0.05, 0.05, 0.00, -0.25, -0.10, 0.80, 0.15),
    }
    base = np.array(base_loadings.get(sector, (0.55, 0.10, 0.10, 0.10, 0.10, 0.10, 0.10, 0.10)), dtype=float)
    if base.shape[0] != len(FACTOR_NAMES):
        base = np.pad(base, (0, len(FACTOR_NAMES) - base.shape[0]), mode="constant")[: len(FACTOR_NAMES)]
    jitter = rng.normal(0.0, jitter_std, size=base.shape[0])
    loaded = np.clip(base + jitter, -0.95, 0.95)
    loaded[0] = _clamp(loaded[0], 0.05, 0.99)
    return tuple(float(x) for x in loaded)


class ProceduralUniverseGenerator:
    def __init__(
        self,
        session_id: str | None = None,
        universe_seed: int | None = None,
        base_asset_count: int = 1000,
        sector_emphasis: dict[str, float] | None = None,
        jitter_std: float = 0.10,
        time_varying_replacement_rate: float = 0.15,
        time_varying_interval: int = 1,
        adversarial_select_top_k: int | None = None,
        frozen_assets: tuple[str, ...] | None = None,
    ) -> None:
        if base_asset_count < 1:
            raise ValueError("base_asset_count must be >= 1")
        self.session_id = session_id
        self.universe_seed = universe_seed
        self.base_asset_count = int(base_asset_count)
        self.jitter_std = float(jitter_std)
        self.time_varying_replacement_rate = float(np.clip(time_varying_replacement_rate, 0.0, 0.5))
        self.time_varying_interval = max(1, int(time_varying_interval))
        self.adversarial_select_top_k = adversarial_select_top_k
        self.frozen_assets = tuple(frozen_assets or ())
        self.frozen_tickers = {a.ticker for a in self.frozen_assets}
        self._registry: list[AssetFactorConfig] = []
        self._registry_meta: list[dict[str, Any]] = []
        self._session_registry_cache: dict[str, list[AssetFactorConfig]] = {}
        self._meta_registry_cache: dict[str, list[dict[str, Any]]] = {}
        self._freshness_log: list[dict[str, Any]] = []
        self._session_count = 0
        self._build_base_registry()

    def _current_rng(self) -> np.random.Generator:
        return _master_seed(self.session_id, self.universe_seed)

    def _registry_cache_key(self) -> str:
        return f"{self.session_id or ''}:{self.universe_seed or ''}:{self.base_asset_count}"

    def _build_base_registry(self) -> None:
        key = self._registry_cache_key()
        if key in self._session_registry_cache:
            self._registry = list(self._session_registry_cache[key])
            self._registry_meta = list(self._meta_registry_cache[key])
            return
        rng = self._current_rng()
        [s for s in DEFAULT_SECTORS if s not in _HELDOUT_SECTORS]
        sector_weights = _session_sector_weights(self.session_id, rng)
        registry: list[AssetFactorConfig] = []
        meta: list[dict[str, Any]] = []
        for idx in range(self.base_asset_count):
            sector = _pick_sector_for_asset(idx, sector_weights, rng)
            _factor_loading_for_sector(sector, idx, rng, jitter_std=self.jitter_std)
            base_price = int(rng.integers(800, 21_000))
            shares = int(rng.integers(8_000_000, 220_000_000, endpoint=True))
            fundamental = int(rng.integers(700, 22_000, endpoint=True))
            macro_beta = _clamp(0.35 + rng.normal(0.0, 0.20), -0.5, 2.5)
            idio_vol = _clamp(0.0008 + abs(rng.normal(0.0, 0.0012)), 0.0003, 0.008)
            liquidity_profile = str(rng.choice(["deep", "normal", "thin"], p=[0.35, 0.45, 0.20]))
            event_sensitivity = _clamp(0.80 + rng.normal(0.0, 0.18), 0.20, 1.60)
            mean_reversion = _clamp(0.012 + abs(rng.normal(0.0, 0.012)), 0.002, 0.12)
            price_cache_factor_loading = _clamp(0.30 + rng.normal(0.0, 0.18), 0.05, 0.95)
            company_template = str(rng.choice(_TIME_VARYING_NOMENCLATURE_TEMPLATES))
            ticker = f"PREGEN{idx:04d}"
            name = company_template.format(index=idx + 1)
            registry.append(
                AssetFactorConfig(
                    ticker=ticker,
                    company_name=name,
                    sector=sector,
                    initial_price_ticks=base_price,
                    shares_outstanding=shares,
                    initial_fundamental_value_ticks=fundamental,
                    macro_beta=round(float(macro_beta), 6),
                    idiosyncratic_volatility=round(float(idio_vol), 6),
                    liquidity_profile=liquidity_profile,
                    event_sensitivity=round(float(event_sensitivity), 6),
                    mean_reversion=round(float(mean_reversion), 6),
                    price_cache_factor_loading=round(float(price_cache_factor_loading), 6),
                )
            )
            meta.append(
                {
                    "generator_index": idx,
                    "sector": sector,
                    "sector_weight_at_creation": float(sector_weights.get(sector, 0.0)),
                    "is_heldout_sector": sector in _HELDOUT_SECTORS,
                    "base_macro_beta": round(float(macro_beta), 6),
                    "base_idio_vol": round(float(idio_vol), 6),
                }
            )
        self._registry = registry
        self._registry_meta = meta
        self._session_registry_cache.setdefault(key, list(registry))
        self._meta_registry_cache.setdefault(key, list(meta))

    def _select_heldout_forward_assets(
        self, asset_count: int, rng: np.random.Generator
    ) -> list[AssetFactorConfig]:
        heldout_pool = [
            asset
            for asset, meta in zip(self._registry, self._registry_meta, strict=False)
            if meta.get("is_heldout_sector")
        ]
        count = min(asset_count, len(heldout_pool))
        if count <= 0:
            return []
        indices = np.arange(len(heldout_pool))
        rng.shuffle(indices)
        selected = [heldout_pool[int(i)] for i in indices[:count]]
        return selected

    def _adversarial_select(
        self,
        candidates: list[AssetFactorConfig],
        k: int,
        rng: np.random.Generator,
    ) -> list[AssetFactorConfig]:
        if k >= len(candidates):
            return list(candidates)
        unpredictability = []
        for asset in candidates:
            spread = abs(float(asset.price_cache_factor_loading or 0.5) - 0.5) * 2.0
            illiquidity = (
                1.0
                if asset.liquidity_profile == "thin"
                else 0.66
                if asset.liquidity_profile == "normal"
                else 0.33
            )
            event_noise = float(asset.event_sensitivity) * 0.25
            score = spread + illiquidity + event_noise + abs(rng.normal(0.0, 0.05))
            unpredictability.append(float(score))
        order = np.argsort(unpredictability)[::-1]
        return [candidates[int(i)] for i in order[:k]]

    def _apply_time_variation(
        self, assets: list[AssetFactorConfig], session_index: int
    ) -> list[AssetFactorConfig]:
        if self.time_varying_interval <= 0 or session_index % self.time_varying_interval != 0:
            return assets
        replace_count = max(1, int(math.ceil(len(assets) * self.time_varying_replacement_rate)))
        keep = list(assets)
        if replace_count >= len(keep):
            return list(assets)
        rng = self._current_rng()
        replace_indices = rng.choice(len(keep), size=replace_count, replace=False).tolist()
        for idx in sorted(replace_indices, reverse=True):
            original = keep[idx]
            new_sector = _pick_sector_for_asset(idx, _session_sector_weights(self.session_id, rng), rng)
            _factor_loading_for_sector(new_sector, idx, rng, jitter_std=self.jitter_std)
            refreshed = AssetFactorConfig(
                ticker=original.ticker,
                company_name=original.company_name.replace(str(idx), str(idx + 1))
                if str(idx) in original.company_name
                else original.company_name,
                sector=new_sector,
                initial_price_ticks=int(rng.integers(800, 21_000)),
                shares_outstanding=original.shares_outstanding,
                initial_fundamental_value_ticks=max(
                    100, original.initial_fundamental_value_ticks + int(rng.integers(-250, 251))
                ),
                macro_beta=round(float(_clamp(original.macro_beta + rng.normal(0.0, 0.05), -0.5, 2.5)), 6),
                idiosyncratic_volatility=round(
                    float(
                        _clamp(
                            original.idiosyncratic_volatility + abs(rng.normal(0.0, 0.0004)), 0.0003, 0.008
                        )
                    ),
                    6,
                ),
                liquidity_profile=original.liquidity_profile,
                event_sensitivity=round(
                    float(_clamp(original.event_sensitivity + rng.normal(0.0, 0.04), 0.20, 1.60)), 6
                ),
                mean_reversion=round(
                    float(_clamp(original.mean_reversion + rng.normal(0.0, 0.003), 0.002, 0.12)), 6
                ),
                price_cache_factor_loading=round(
                    float(_clamp(original.price_cache_factor_loading + rng.normal(0.0, 0.04), 0.05, 0.95)), 6
                ),
            )
            keep[idx] = refreshed
        return keep

    def generate(
        self,
        asset_count: int,
        session_index: int = 0,
        *,
        include_heldout_forward: bool = False,
        heldout_forward_count: int = 0,
        adversarial_top_k: int | None = None,
    ) -> tuple[tuple[AssetFactorConfig, ...], dict[str, Any]]:
        if asset_count < 1:
            raise ValueError("asset_count must be >= 1")
        if asset_count > len(self._registry) + 5000:
            raise ValueError(f"asset_count {asset_count} exceeds supported registry size for this mode")
        universe_seed_used = int(self._current_rng().integers(0, 2**63 - 1))
        generation_start = time.perf_counter()
        all_candidates = list(self._registry)
        all_meta = list(self._registry_meta)
        rng = self._current_rng()
        rng.shuffle(all_candidates)
        all_meta = [all_meta[i] for i in range(len(all_meta))]
        frozen = [a for a in all_candidates if a.ticker in self.frozen_tickers]
        candidates = [a for a in all_candidates if a.ticker not in self.frozen_tickers]
        candidate_meta = [
            m for a, m in zip(all_candidates, all_meta, strict=False) if a.ticker not in self.frozen_tickers
        ]
        if adversarial_top_k is None:
            adversarial_top_k = self.adversarial_select_top_k
        need = max(0, asset_count - len(frozen))
        adversarial_subset_needed = 0
        if adversarial_top_k and adversarial_top_k >= 1 and need > 0:
            adversarial_subset_needed = min(int(adversarial_top_k), need)
        if adversarial_subset_needed > 0 and len(candidates) > adversarial_subset_needed:
            selected_candidates = self._adversarial_select(candidates, adversarial_subset_needed, rng)
            remaining = [c for c in candidates if c not in selected_candidates]
            candidates = list(selected_candidates) + list(remaining)
            candidate_meta = [m for a, m in zip(candidates, candidate_meta, strict=False)]
        if need > len(candidates):
            need = len(candidates)
        indices = np.arange(len(candidates))
        rng.shuffle(indices)
        chosen_candidates = [candidates[int(i)] for i in indices[:need]]
        chosen_meta = [candidate_meta[int(i)] for i in indices[:need]]
        assets = list(frozen) + chosen_candidates[:need]
        [{"frozen": True, "ticker": a.ticker} for a in frozen] + chosen_meta[:need]
        assets = assets[:asset_count]
        assets = self._apply_time_variation(assets, session_index)
        heldout_extra: list[AssetFactorConfig] = []
        if include_heldout_forward and heldout_forward_count > 0:
            heldout_extra = self._select_heldout_forward_assets(heldout_forward_count, rng)
        self._session_count = max(self._session_count, session_index + 1)
        sector_counts: dict[str, int] = {}
        for asset in assets:
            sector_counts[asset.sector] = sector_counts.get(asset.sector, 0) + 1
        freshness_entry = {
            "session_id": self.session_id or f"auto-{universe_seed_used}",
            "universe_seed": universe_seed_used,
            "selected_assets": [{"ticker": a.ticker, "sector": a.sector} for a in assets],
            "heldout_sectors_forward": [{"ticker": a.ticker, "sector": a.sector} for a in heldout_extra],
            "regime": {
                "sector_counts": sector_counts,
                "session_index": session_index,
                "replacement_rate": self.time_varying_replacement_rate if session_index > 0 else 0.0,
            },
            "timestamp": datetime.now(UTC).isoformat(),
            "generation_ms": round((time.perf_counter() - generation_start) * 1000, 3),
            "asset_count": len(assets),
            "heldout_count": len(heldout_extra),
        }
        self._freshness_log.append(freshness_entry)
        metadata = {
            "session_id": freshness_entry["session_id"],
            "universe_seed": universe_seed_used,
            "session_index": session_index,
            "selected_tickers": [a.ticker for a in assets],
            "heldout_forward_tickers": [a.ticker for a in heldout_extra],
            "sector_counts": sector_counts,
            "replacement_rate": freshness_entry["regime"]["replacement_rate"],
            "timestamp": freshness_entry["timestamp"],
            "generation_ms": freshness_entry["generation_ms"],
            "frozen_count": len(frozen),
            "adversarial_top_k": adversarial_top_k or 0,
            "heldout_sectors": sorted(_HELDOUT_SECTORS),
        }
        return tuple(list(assets) + heldout_extra), metadata

    def get_freshness_log(self, session_id: str | None = None) -> list[dict[str, Any]]:
        if session_id is None:
            return list(self._freshness_log)
        return [entry for entry in self._freshness_log if entry.get("session_id") == session_id]

    def write_freshness_log(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w", encoding="utf-8") as handle:
            json.dump(
                {
                    "generator_version": "1.0",
                    "written_at": datetime.now(UTC).isoformat(),
                    "entries": self._freshness_log,
                },
                handle,
                indent=2,
                default=str,
            )
        return target


def create_session_generator(
    session_id: str | None = None,
    preset: str | None = None,
    asset_count: int = 1000,
    universe_seed: int | None = None,
    adversarial: bool = True,
    time_varying: bool = True,
    include_heldout: bool = True,
) -> tuple[ProceduralUniverseGenerator, dict[str, Any]]:
    if preset == "200-asset" or (preset is None and asset_count <= 200):
        base_asset_count = 220
    elif preset == "fenrix":
        base_asset_count = 16
    else:
        base_asset_count = max(asset_count, 1000)
    replacement_rate = 0.15 if time_varying else 0.0
    adversarial_top_k = 0 if not adversarial else max(1, int(base_asset_count * 0.35))
    generator = ProceduralUniverseGenerator(
        session_id=session_id,
        universe_seed=universe_seed,
        base_asset_count=base_asset_count,
        time_varying_replacement_rate=replacement_rate,
        time_varying_interval=1,
        adversarial_select_top_k=adversarial_top_k,
    )
    session_index = 0
    if session_id:
        digest = hashlib.sha256(f"session_counter:{session_id}".encode()).hexdigest()
        session_index = int(digest[:8], 16) % 1000
    assets, metadata = generator.generate(
        asset_count=asset_count,
        session_index=session_index,
        include_heldout_forward=include_heldout,
        heldout_forward_count=max(10, asset_count // 20),
        adversarial_top_k=adversarial_top_k,
    )
    return generator, metadata
