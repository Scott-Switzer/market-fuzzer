"""Map local calibration-pack aggregates onto ExchangeSpec at world-build time."""

from __future__ import annotations

from typing import Any

from app.calibration.models import CalibrationPackV1
from app.schemas import ExchangeSpec, WorldSpec


def _clamp_int(value: float, lo: int, hi: int) -> int:
    return max(lo, min(hi, int(round(value))))


def _clamp_float(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(value)))


def apply_calibration_pack_to_exchange(
    exchange: ExchangeSpec,
    pack: CalibrationPackV1,
    *,
    bars_per_day: float = 78.0,
) -> ExchangeSpec:
    """Derive mutable ExchangeSpec fields from the pack's train aggregates.

    Uses only aggregate metrics (never raw rows): depth → baseline_depth,
    volume → adtv, spread → toxicity_kappa scaling.
    """
    train = next((window for window in pack.windows if window.name == "train"), None)
    if train is None:
        return exchange
    metrics = train.metrics
    updates: dict[str, Any] = {}
    if "total_depth_mean" in metrics:
        updates["baseline_depth"] = _clamp_int(metrics["total_depth_mean"].value, 10, 1_000_000)
    if "volume_mean" in metrics:
        # Scale mean bar volume to an approximate ADTV (default assumes ~78 bars/day).
        updates["adtv"] = _clamp_int(metrics["volume_mean"].value * bars_per_day * 252.0, 1, 10_000_000_000)
    if "spread_bps_mean" in metrics:
        updates["toxicity_kappa"] = _clamp_float(metrics["spread_bps_mean"].value / 2.0, 0.5, 100.0)
    if not updates:
        return exchange
    return exchange.model_copy(update=updates)


def apply_calibration_pack_to_world(
    world: WorldSpec,
    pack: CalibrationPackV1,
    *,
    bars_per_day: float = 78.0,
) -> WorldSpec:
    """Attach pack ids and mutate exchange fields from local calibration aggregates."""
    exchange = apply_calibration_pack_to_exchange(world.exchange, pack, bars_per_day=bars_per_day)
    return world.model_copy(
        update={
            "exchange": exchange,
            "calibration_pack_id": pack.pack_id,
            "calibration_parameter_set_id": world.calibration_parameter_set_id
            or f"local-{pack.pack_id[:12]}",
        },
        deep=True,
    )


def exchange_mutable_fields(exchange: ExchangeSpec) -> dict[str, Any]:
    """Spec fields that affect execution economics and should enter repro metadata."""
    return {
        "baseline_depth": exchange.baseline_depth,
        "adtv": exchange.adtv,
        "perm_eta": exchange.perm_eta,
        "temp_epsilon": exchange.temp_epsilon,
        "temp_gamma": exchange.temp_gamma,
        "locate_fee_bps_annual": exchange.locate_fee_bps_annual,
        "htb_bps_annual": exchange.htb_bps_annual,
        "htb_schedule": exchange.htb_schedule,
        "toxicity_kappa": exchange.toxicity_kappa,
        "fee_schedule": exchange.fee_schedule,
        "intraday_volume_profile": exchange.intraday_volume_profile,
        "per_step_volume_cap": exchange.per_step_volume_cap,
        "maker_fee_bps": exchange.maker_fee_bps,
        "taker_fee_bps": exchange.taker_fee_bps,
        "latency_profile": exchange.latency_profile,
    }
