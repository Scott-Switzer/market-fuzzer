"""Transaction cost model with Almgren-Chriss impact and adverse-selection toxicity.

Legacy ``impact_beta`` + ``impact_mode`` in {sqrt, linear} remains for callers that
constructed ``TransactionCostModel`` explicitly. New ExchangeSpec-driven paths use
``impact_mode="almgren_chriss"`` with temporary/permanent decomposition.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Literal, Optional, Sequence

import numpy as np


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def almgren_chriss_impact_bps(
    relative_size: float,
    daily_vol: float,
    *,
    perm_eta: float = 0.05,
    temp_epsilon: float = 0.005,
    temp_gamma: float = 0.20,
) -> tuple[float, float]:
    """Return (permanent_bps, temporary_bps) for relative order size x = Q/ADTV."""
    x = max(0.0, float(relative_size))
    sigma = max(1e-9, float(daily_vol))
    permanent = float(perm_eta) * x * sigma * 10_000.0
    temporary = (float(temp_epsilon) + float(temp_gamma) * math.sqrt(x)) * sigma * 10_000.0
    return permanent, temporary


def toxicity_bps(
    signed_flow_prev: float,
    depth_prev: float,
    *,
    kappa: float = 5.0,
) -> float:
    """Adverse-selection cost from lagged signed taker flow over displayed depth.

    ``toxicity_bps = kappa * 100 * tanh(|signed_flow_{t-1}| / depth_{t-1})``
    """
    depth = max(1.0, float(depth_prev))
    ratio = abs(float(signed_flow_prev)) / depth
    return float(kappa) * 100.0 * math.tanh(ratio)


def lookup_htb_bps_annual(
    short_notional_cents: int,
    schedule: Sequence[dict[str, Any]] | None,
    default_htb_bps_annual: float = 0.0,
) -> float:
    """Tiered hard-to-borrow schedule: highest threshold fully cleared wins."""
    rate = float(default_htb_bps_annual)
    if not schedule:
        return rate
    notional = max(0, int(short_notional_cents))
    for tier in schedule:
        threshold = int(tier.get("threshold_cents", tier.get("short_notional_cents", 0)) or 0)
        tier_rate = float(tier.get("htb_bps_annual", tier.get("fee_bps", default_htb_bps_annual)) or 0.0)
        if notional >= threshold:
            rate = tier_rate
        else:
            break
    return rate


def borrow_fee_bps_for_short(
    *,
    short_shares: float,
    price: float,
    locate_fee_bps_annual: float,
    htb_bps_annual: float,
    htb_schedule: Sequence[dict[str, Any]] | None = None,
    holding_days: float = 1.0,
) -> float:
    """Per-bar borrow cost in bps of notional for a short inventory mark."""
    if short_shares <= 0 or price <= 0:
        return 0.0
    notional_cents = int(round(abs(short_shares) * price * 100))
    htb = lookup_htb_bps_annual(notional_cents, htb_schedule, htb_bps_annual)
    annual = float(locate_fee_bps_annual) + float(htb)
    return annual * (float(holding_days) / 365.0)


@dataclass
class ImpactDecomposition:
    permanent_bps: float
    temporary_bps: float
    toxicity_bps: float
    total_impact_bps: float


@dataclass
class TransactionCostModel:
    spread_bps: float = 2.0
    borrow_fee_bps: float = 0.0
    impact_beta: float = 0.0
    impact_mode: Literal["sqrt", "linear", "almgren_chriss"] = "sqrt"
    default_adv: Optional[float] = None
    perm_eta: float = 0.05
    temp_epsilon: float = 0.005
    temp_gamma: float = 0.20
    daily_vol: float = 0.015
    locate_fee_bps_annual: float = 0.0
    htb_bps_annual: float = 0.0
    htb_schedule: list[dict[str, Any]] | None = None
    toxicity_kappa: float = 5.0
    holding_days: float = 1.0
    # Optional lagged flow state for adverse selection (set by callers / simulation).
    prior_signed_flow: float = 0.0
    prior_depth: float = 0.0

    def _participation(
        self,
        trade_notional: float,
        default_adv: Optional[float],
    ) -> float:
        adv_source = self.default_adv if self.default_adv is not None else default_adv
        if adv_source is None or float(adv_source) <= 0:
            return 0.0
        return min(abs(float(trade_notional)) / float(adv_source), 1.0)

    def _legacy_impact_bps(self, participation: float, trade_qty: float) -> float:
        if self.impact_beta <= 0 or participation <= 0:
            return 0.0
        alpha = self.impact_beta * participation
        if self.impact_mode == "sqrt":
            alpha *= math.sqrt(participation)
        else:
            alpha *= participation
        return float(np.sign(trade_qty) * alpha * 10_000.0)

    def decompose_impact(
        self,
        *,
        trade_qty: float,
        price: float,
        default_adv: Optional[float] = None,
        signed_flow_prev: float | None = None,
        depth_prev: float | None = None,
    ) -> ImpactDecomposition:
        trade_notional = abs(float(trade_qty)) * float(price)
        participation = self._participation(trade_notional, default_adv)
        flow = self.prior_signed_flow if signed_flow_prev is None else float(signed_flow_prev)
        depth = self.prior_depth if depth_prev is None else float(depth_prev)
        tox = toxicity_bps(flow, depth, kappa=self.toxicity_kappa) if (flow or depth) else 0.0
        # Toxicity is adverse for the taker in the direction of their trade.
        tox_signed = float(np.sign(trade_qty) * tox) if trade_qty else 0.0

        if self.impact_mode == "almgren_chriss":
            permanent, temporary = almgren_chriss_impact_bps(
                participation,
                self.daily_vol,
                perm_eta=self.perm_eta,
                temp_epsilon=self.temp_epsilon,
                temp_gamma=self.temp_gamma,
            )
            signed_perm = float(np.sign(trade_qty) * permanent)
            signed_temp = float(np.sign(trade_qty) * temporary)
            return ImpactDecomposition(
                permanent_bps=signed_perm,
                temporary_bps=signed_temp,
                toxicity_bps=tox_signed,
                total_impact_bps=signed_perm + signed_temp + tox_signed,
            )

        legacy = self._legacy_impact_bps(participation, trade_qty)
        return ImpactDecomposition(
            permanent_bps=0.0,
            temporary_bps=legacy,
            toxicity_bps=tox_signed,
            total_impact_bps=legacy + tox_signed,
        )

    def _borrow_bps(
        self,
        *,
        side: int,
        current_inventory: float,
        price: float,
        trade_qty: float,
    ) -> float:
        # Prefer ExchangeSpec annual locate/HTB schedule when present.
        if self.locate_fee_bps_annual or self.htb_bps_annual or self.htb_schedule:
            short_shares = 0.0
            if side < 0 or current_inventory < -1e-9:
                short_shares = max(abs(min(current_inventory, 0.0)), abs(min(trade_qty, 0.0)))
            return borrow_fee_bps_for_short(
                short_shares=short_shares,
                price=price,
                locate_fee_bps_annual=self.locate_fee_bps_annual,
                htb_bps_annual=self.htb_bps_annual,
                htb_schedule=self.htb_schedule,
                holding_days=self.holding_days,
            )
        # Legacy flat borrow_fee_bps path used by existing tests.
        if side < 0 or current_inventory < -1e-9:
            borrow = self.borrow_fee_bps
            if borrow and self.spread_bps > 0:
                borrow += self.spread_bps / 2.0
            return float(borrow)
        return 0.0

    def trade_cost_bps(
        self,
        price: float,
        trade_qty: float,
        side: int,
        current_inventory: float,
        default_adv: Optional[float] = None,
        *,
        signed_flow_prev: float | None = None,
        depth_prev: float | None = None,
    ) -> float:
        if price <= 0 or trade_qty == 0:
            return 0.0
        spread_cost = self.spread_bps / 2.0
        borrow = self._borrow_bps(
            side=side,
            current_inventory=current_inventory,
            price=price,
            trade_qty=trade_qty,
        )
        impact = self.decompose_impact(
            trade_qty=trade_qty,
            price=price,
            default_adv=default_adv,
            signed_flow_prev=signed_flow_prev,
            depth_prev=depth_prev,
        )
        return float(spread_cost + borrow + impact.total_impact_bps)

    def costs_for_signals(
        self,
        prices: np.ndarray,
        positions: np.ndarray,
        default_adv: Optional[float] = None,
        *,
        signed_flow: Sequence[float] | None = None,
        depth: Sequence[float] | None = None,
    ) -> np.ndarray:
        prices_a = np.asarray(prices, dtype=float)
        positions_a = np.asarray(positions, dtype=float)
        if prices_a.size < 2:
            return np.zeros(max(0, prices_a.size - 1), dtype=float)

        trade_qty = np.diff(positions_a)
        side = np.where(trade_qty > 0, 1, np.where(trade_qty < 0, -1, 0)).astype(int)
        n = len(trade_qty)
        flow = np.asarray(signed_flow if signed_flow is not None else np.zeros(n), dtype=float)
        depths = np.asarray(depth if depth is not None else np.zeros(n), dtype=float)
        if flow.size != n:
            flow = np.resize(flow, n)
        if depths.size != n:
            depths = np.resize(depths, n)

        out = np.zeros(n, dtype=float)
        for index in range(n):
            qty = float(trade_qty[index])
            if qty == 0.0:
                # Match legacy costs_for_signals: half-spread accrues every bar.
                out[index] = self.spread_bps / 2.0
                inventory = float(positions_a[index])
                if inventory < -1e-9:
                    out[index] += self._borrow_bps(
                        side=-1,
                        current_inventory=inventory,
                        price=float(prices_a[index]),
                        trade_qty=0.0,
                    )
                continue
            out[index] = self.trade_cost_bps(
                float(prices_a[index]),
                qty,
                side=int(side[index]),
                current_inventory=float(positions_a[index]),
                default_adv=default_adv,
                signed_flow_prev=float(flow[index - 1]) if index > 0 else 0.0,
                depth_prev=float(depths[index - 1]) if index > 0 else 0.0,
            )
        return out

    def for_spec(self, spec: "ExchangeSpec") -> "TransactionCostModel":
        """Build an Almgren-Chriss model from ExchangeSpec cost fields."""
        try:
            data = spec.model_dump()
        except AttributeError:
            data = {**spec.__dict__}
        adtv = float(data.get("adtv", getattr(self, "default_adv", None) or 1_000_000.0) or 1_000_000.0)
        perm_eta = float(data.get("perm_eta", 0.05) or 0.05)
        temp_epsilon = float(data.get("temp_epsilon", 0.005) or 0.005)
        temp_gamma = float(data.get("temp_gamma", 0.20) or 0.20)
        locate_annual = float(data.get("locate_fee_bps_annual", 0.0) or 0.0)
        htb_annual = float(data.get("htb_bps_annual", 0.0) or 0.0)
        htb_schedule = data.get("htb_schedule")
        toxicity_kappa = float(data.get("toxicity_kappa", 5.0) or 5.0)
        taker_fee = float(data.get("taker_fee_bps", getattr(self, "spread_bps", 2.0)) or getattr(self, "spread_bps", 2.0))
        implied_spread = max(
            float(getattr(self, "spread_bps", 2.0) or 2.0),
            taker_fee * 2.0,
        )
        return TransactionCostModel(
            spread_bps=implied_spread,
            borrow_fee_bps=locate_annual + htb_annual,
            impact_beta=0.0,
            impact_mode="almgren_chriss",
            default_adv=adtv,
            perm_eta=perm_eta,
            temp_epsilon=temp_epsilon,
            temp_gamma=temp_gamma,
            locate_fee_bps_annual=locate_annual,
            htb_bps_annual=htb_annual,
            htb_schedule=list(htb_schedule) if htb_schedule else None,
            toxicity_kappa=toxicity_kappa,
        )

    @classmethod
    def from_exchange_spec(cls, spec: "ExchangeSpec") -> "TransactionCostModel":
        return cls().for_spec(spec)
