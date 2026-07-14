from __future__ import annotations

import statistics


def build_failure_surface(rows: list[dict]) -> dict:
    cells: list[dict] = []
    groups: dict[tuple[str, float], list[float]] = {}
    for row in rows:
        key = (row["scenario"], row["participation_rate"])
        groups.setdefault(key, []).append(row["metrics"]["implementation_shortfall_bps"])
    for (scenario, participation), values in sorted(groups.items()):
        cells.append(
            {
                "scenario": scenario,
                "participation_rate": participation,
                "n": len(values),
                "mean_shortfall_bps": statistics.mean(values),
                "min_shortfall_bps": min(values),
                "max_shortfall_bps": max(values),
                "stdev_bps": statistics.stdev(values) if len(values) > 1 else 0.0,
            }
        )
    best = min(cells, key=lambda row: row["mean_shortfall_bps"])
    worst = max(cells, key=lambda row: row["mean_shortfall_bps"])
    participation_means: dict[float, list[float]] = {}
    for cell in cells:
        participation_means.setdefault(cell["participation_rate"], []).append(cell["mean_shortfall_bps"])
    trend = sorted((rate, statistics.mean(values)) for rate, values in participation_means.items())
    threshold = None
    for rate, value in trend[1:]:
        if value > trend[0][1] + max(5.0, abs(trend[0][1]) * 0.25):
            threshold = {
                "participation_rate": rate,
                "evidence": f"mean cost exceeds the lowest-participation mean by {value - trend[0][1]:.1f} bps",
            }
            break
    return {
        "axes": {"x": "participation_rate", "y": "scenario", "value": "mean_shortfall_bps"},
        "cells": cells,
        "best": best,
        "worst": worst,
        "supported_thresholds": [threshold] if threshold else [],
        "sensitivity_ranking": [
            {
                "parameter": "participation_rate",
                "range_bps": max(v for _, v in trend) - min(v for _, v in trend),
            },
            {"parameter": "scenario", "range_bps": worst["mean_shortfall_bps"] - best["mean_shortfall_bps"]},
        ],
        "interpretation": "Threshold language is emitted only when repeated runs exceed the declared cost-change rule.",
    }
