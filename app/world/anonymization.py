from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.world.manifest_schema import (
    AnonymizationManifest,
    AssetManifest,
    SectorManifestEntry,
)

_HELDOUT_SECTORS = {"Crypto", "Commodities/Metals"}


@dataclass
class AssetAnonymizationContext:
    ticker: str
    company_name: str
    sector: str
    is_heldout_sector: bool
    is_synthetic: bool


def classify_assets(
    assets: list[AssetAnonymizationContext],
) -> tuple[list[AssetAnonymizationContext], list[AssetAnonymizationContext]]:
    heldout = [asset for asset in assets if asset.is_heldout_sector]
    regular = [asset for asset in assets if not asset.is_heldout_sector]
    return regular, heldout


def anonymize_ticker(asset: AssetAnonymizationContext, assignment_index: int) -> str:
    if asset.is_synthetic:
        return f"PREGEN{assignment_index:04d}"
    if asset.is_heldout_sector:
        return f"HELDOUT-{assignment_index:04d}"
    return f"REAL-{assignment_index:04d}"


def build_asset_manifest(
    assets: list[AssetAnonymizationContext],
    mode: str = "HELDOUT_HIDDEN",
    *,
    prompt_decoded_tickers: tuple[str, ...] = (),
    decoded_names: dict[str, str] | None = None,
    decoded_sector_map: dict[str, str] | None = None,
) -> tuple[AssetManifest, AnonymizationManifest]:
    regular, heldout = classify_assets(assets)
    synthetic_assets = [asset for asset in assets if asset.is_synthetic]
    real_assets = [asset for asset in assets if not asset.is_synthetic]

    sector_manifest: list[SectorManifestEntry] = []
    for asset in assets:
        sector_manifest.append(
            SectorManifestEntry(
                sector=asset.sector,
                asset_count=1,
                anonymized=(mode == "PROMPT_SAFE"),
                heldout=asset.is_heldout_sector,
                forward_injected=False,
            )
        )

    asset_manifest = AssetManifest(
        asset_count=len(assets),
        real_ticker_count=len(real_assets),
        synthetic_asset_count=len(synthetic_assets),
        anonymized_count=len(assets) if mode == "PROMPT_SAFE" else 0,
        strategy_asset_ticker="SYNTH",
        sector_manifest=sector_manifest,
    )

    limits_applied: list[str] = []
    if mode == "PROMPT_SAFE":
        limits_applied.append("prompt_excludes_real_tickers")
    if heldout:
        limits_applied.append("heldout_sector_forward_injection_requires_explicit_flag")

    anonymization_manifest = AnonymizationManifest(
        mode=mode,
        applied=(mode != "FULL_DECODED"),
        heldout_sector_forward_count=len(heldout),
        prompt_decoded_tickers=prompt_decoded_tickers,
        decoded_names=decoded_names,
        decoded_sector_map=decoded_sector_map,
        limits_applied=tuple(limits_applied),
    )
    return asset_manifest, anonymization_manifest


def forward_inject_heldout_only(
    universe_assets: list[Any],
    heldout_forward_count: int,
    heldout_pool: list[Any],
) -> list[Any]:
    if heldout_forward_count <= 0:
        return list(universe_assets)
    if not heldout_pool:
        return list(universe_assets)
    selected = heldout_pool[:heldout_forward_count]
    return list(universe_assets) + selected
