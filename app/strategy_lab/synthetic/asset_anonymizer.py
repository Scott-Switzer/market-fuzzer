from __future__ import annotations


class AssetAnonymizer:
    @staticmethod
    def anonymize(tickers: list[str]) -> list[str]:
        return [f"anon_{i:04d}" for i in range(len(tickers))]
