from __future__ import annotations

import numpy as np
import pytest

from app.break_test.exchange_fwd import (
    EIGHT_ASSET_UNIVERSE,
    EXPANDED_UNIVERSE_PRESETS,
    TWELVE_ASSET_UNIVERSE,
    _resolve_asset_universe,
    build_world,
)
from app.break_test.synthetic_market import (
    FACTOR_LOADINGS,
    FACTOR_NAMES,
    ResearchSyntheticMarketGenerator,
)


class TestExpandedUniverseCorrelation:
    def test_eight_asset_preset_length(self) -> None:
        assert len(EIGHT_ASSET_UNIVERSE) == 8
        assert "XLK" in {asset.ticker for asset in EIGHT_ASSET_UNIVERSE}
        assert "RATES" in {asset.ticker for asset in EIGHT_ASSET_UNIVERSE}
        assert "FX" in {asset.ticker for asset in EIGHT_ASSET_UNIVERSE}

    def test_twelve_asset_preset_length(self) -> None:
        assert len(TWELVE_ASSET_UNIVERSE) == 12
        assert "XLV" in {asset.ticker for asset in TWELVE_ASSET_UNIVERSE}
        assert "XLI" in {asset.ticker for asset in TWELVE_ASSET_UNIVERSE}

    def test_covariance_matrix_positive_eigenvalues(self) -> None:
        generator = ResearchSyntheticMarketGenerator()
        asset_tickers = [asset.ticker for asset in EIGHT_ASSET_UNIVERSE]
        covariance = generator._build_asset_factor_covariance(asset_tickers)
        assert covariance.shape == (8, 8)
        eigenvals = np.linalg.eigvalsh(covariance)
        assert np.all(eigenvals > 0), f"Non-positive eigenvalues found: {eigenvals}"

    def test_correlation_matrix_non_unit_off_diagonal(self) -> None:
        generator = ResearchSyntheticMarketGenerator()
        asset_tickers = [asset.ticker for asset in EIGHT_ASSET_UNIVERSE]
        covariance = generator._build_asset_factor_covariance(asset_tickers)
        variances = np.sqrt(np.diag(covariance))
        corr = covariance / np.outer(variances, variances)
        np.fill_diagonal(corr, 0.0)
        max_off_diag = float(np.max(np.abs(corr)))
        assert max_off_diag > 0.05, f"Off-diagonal correlations are too small: {max_off_diag}"

    def test_paths_show_cross_sectional_correlation(self) -> None:
        generator = ResearchSyntheticMarketGenerator()
        asset_tickers = [asset.ticker for asset in EIGHT_ASSET_UNIVERSE]
        base_prices = [float(asset.initial_price_ticks) for asset in EIGHT_ASSET_UNIVERSE]
        paths = generator.generate_correlated_gbm_paths(
            regime_key="steady_trend",
            seed=7,
            asset_tickers=asset_tickers,
            base_prices=base_prices,
            length=120,
        )
        assert set(paths.keys()) == set(asset_tickers)
        returns_matrix = np.array([paths[ticker]["returns"] for ticker in asset_tickers])
        assert returns_matrix.shape == (8, 119)
        corr = np.corrcoef(returns_matrix)
        np.fill_diagonal(corr, 0.0)
        max_abs_corr = float(np.max(np.abs(corr)))
        assert max_abs_corr > 0.05, f"Path correlations too small: {max_abs_corr}"

    def test_backward_compatibility_asset_count_three(self) -> None:
        world = build_world("steady_trend", seed=1, asset_count=3, target_asset="SYNTH")
        assert len(world.assets) == 3
        assert world.experiment.target_asset == "SYNTH"

    def test_presets_return_expected_asset_counts(self) -> None:
        assets, _ = _resolve_asset_universe(asset_count=8, universe_preset="eight_assets")
        assert len(assets) == 8
        assets, _ = _resolve_asset_universe(asset_count=12, universe_preset="twelve_assets")
        assert len(assets) == 12
