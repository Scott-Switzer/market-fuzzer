from __future__ import annotations


class FactorModelLibrary:
    @staticmethod
    def available() -> list[str]:
        return ["single_factor", "three_factor", "five_factor"]
