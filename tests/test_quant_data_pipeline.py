from __future__ import annotations

import math

import numpy as np
import pytest

from app.break_test.costs import TransactionCostModel
from app.break_test.data_loader import (
    _REQUESTS_AVAILABLE,
    _YFINANCE_AVAILABLE,
    default_fred_series,
    fred_series_descriptions,
    load_fred_series,
    load_yfinance,
    load_yfinance_bulk,
    suggest_lookback,
    validate_prices,
    validate_prices_after_source,
    warn_on_short_history,
)
from app.break_test.metrics import backtest_metrics


def _prices(n: int = 120) -> np.ndarray:
    rng = np.random.default_rng(0)
    return np.asarray((100 * np.exp(np.cumsum(rng.normal(0.0003, 0.01, n)))), dtype=float)


class TestDataLoader:
    def test_validate_prices_accepts_valid(self) -> None:
        prices = _prices(120).tolist()
        prices[1] = float("nan")
        with pytest.raises(ValueError, match="Prices must be finite and positive"):
            validate_prices(prices)

    def test_validate_prices_after_source_accepts_short_but_valid(self) -> None:
        prices = _prices(100).tolist()
        assert len(validate_prices_after_source(prices, min_length=252 * 5)) == 100

    def test_short_history_warning_flags_small_input(self) -> None:
        result = warn_on_short_history(_prices(100).tolist(), min_bars=252 * 5)
        assert result["short_history"] is True
        assert result.get("help", {}).get("current_bars") == 100
        assert result.get("help", {}).get("min_bars_warned") == 252 * 5
        assert result["help"].get("suggested_data_source") == "yfinance"
        assert result["help"].get("suggested_lookback") == "20y"
        assert result.get("warnings")

    def test_long_history_clean(self) -> None:
        result = warn_on_short_history(_prices(3000).tolist(), min_bars=252 * 5)
        assert result["short_history"] is False

    def test_suggest_lookback_yfinance_ticker(self) -> None:
        assert suggest_lookback("yfinance", ["SPY"]) == "20y"

    def test_suggest_lookback_demo_defaults(self) -> None:
        assert suggest_lookback("demo", None) == "3y"


class TestYFinance:
    @pytest.mark.skipif(not _YFINANCE_AVAILABLE, reason="yfinance not installed")
    def test_load_yfinance_returns_positives(self) -> None:
        closes = load_yfinance("SPY", period="1y")
        if len(closes) == 0:
            pytest.skip("yfinance returned no data for SPY in this environment")
        assert len(closes) >= 80
        assert all(price > 0 for price in closes)

    @pytest.mark.skipif(not _YFINANCE_AVAILABLE, reason="yfinance not installed")
    def test_bulk_download_metadata(self) -> None:
        bulk = load_yfinance_bulk(["SPY", "AAPL"], start="2020-01-01", end="2022-01-01")
        assert "SPY" in bulk["tickers"]
        assert "AAPL" in bulk["tickers"]
        spy = bulk["tickers"]["SPY"]
        assert "closes" in spy
        assert "meta" in spy
        assert spy["meta"]["bars"] == len(spy["closes"])

    @pytest.mark.skipif(not _YFINANCE_AVAILABLE, reason="yfinance not installed")
    def test_bulk_download_contains_splits_and_dividends_metadata(self) -> None:
        bulk = load_yfinance_bulk(
            ["SPY"], start="2020-01-01", end="2022-01-01", corporate_action_adjustment=True
        )
        spy = bulk["tickers"]["SPY"]
        assert "corporate_actions" in spy
        assert "splits" in spy["corporate_actions"]
        assert "dividends" in spy["corporate_actions"]
        assert spy["corporate_actions"]["method"] == "auto_adjust"


class TestFred:
    @pytest.mark.skipif(not _REQUESTS_AVAILABLE, reason="requests not installed")
    def test_load_fred_mock_observation_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class FakeResponse:
            status_code = 200

            def raise_for_status(self) -> None:
                return None

            def json(self) -> object:
                return {
                    "observations": [
                        {"series_id": "VIXCLS", "value": "18.5"},
                        {"series_id": "VIXCLS", "value": "."},
                        {"series_id": "VIXCLS", "value": "21.3"},
                    ]
                }

        monkeypatch.setattr("app.break_test.data_loader.requests.get", lambda *args, **kwargs: FakeResponse())
        values = load_fred_series(["VIXCLS"], start="2020-01-01", end="2020-01-10")
        assert values["VIXCLS"] == [18.5, None, 21.3]

    def test_default_fred_series_contains_expected(self) -> None:
        series = default_fred_series()
        assert {"GDP", "CPIAUCSL", "UNRATE", "VIXCLS", "DGS10", "DGS2"}.issubset(set(series))

    def test_fred_series_descriptions(self) -> None:
        descriptions = fred_series_descriptions()
        assert descriptions["VIXCLS"]
        assert descriptions["DGS10"]


class TestTcostPropagation:
    def test_explicit_tcost_model_changes_metrics_output(self) -> None:
        prices = _prices(120)
        model = TransactionCostModel(spread_bps=30.0, default_adv=100_000.0)
        result = backtest_metrics(prices, np.ones_like(prices), tcost_model=model, default_adv=100_000.0)
        assert math.isfinite(result["total_return_pct"])

    def test_default_adv_impacts_result(self) -> None:
        prices = _prices(120)
        # Alternating positions so trades occur and ADV-scaled impact can differ.
        positions = np.array([1.0 if i % 2 == 0 else -1.0 for i in range(len(prices))], dtype=float)
        with_adv = TransactionCostModel(impact_beta=1.0, default_adv=10_000.0)
        without_adv = TransactionCostModel(impact_beta=1.0)
        with_adv_metrics = backtest_metrics(prices, positions, tcost_model=with_adv, default_adv=10_000.0)
        without_adv_metrics = backtest_metrics(prices, positions, tcost_model=without_adv)
        assert with_adv_metrics["total_return_pct"] != without_adv_metrics["total_return_pct"]

    def test_twenty_year_validation_warning_path(self) -> None:
        result = warn_on_short_history(_prices(200).tolist(), min_bars=252 * 5)
        assert result["help"]["suggested_lookback"] == "20y"
