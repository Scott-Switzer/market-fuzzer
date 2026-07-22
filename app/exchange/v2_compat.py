"""V1 matching surface with V2 event-kernel provenance.

``MatchingExchangeV2`` is not yet a drop-in replacement for the agent
simulation loop (book snapshots, halts, latency cancel-before-arrival).
This adapter keeps the existing ``Exchange`` / ``OrderBook`` matching path
and wires ``EventKernelV2`` so forward tests default to the sealed V2
command/event contract for audit digests.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from app.schemas import ExchangeSpec, WorldSpec

from .market import Account, Exchange
from .orders import CancelRequest, Order, OrderType, Side, Trade
from .v2 import (
    CancelOrderCommandV2,
    EventKernelV2,
    EventKindV2,
    OrderCommandV2,
    OrderRejectedError,
    OrderTypeV2,
    RunManifestV2,
    SideV2,
    TimeInForceV2,
)


def build_run_manifest_v2(
    spec: WorldSpec,
    *,
    strategy_artifact_digest: str | None = None,
    generator_bundle_digest: str | None = None,
) -> RunManifestV2:
    """Build a deterministic V2 run manifest from a world specification."""

    def _digest(value: object) -> str:
        return hashlib.sha256(
            json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
        ).hexdigest()

    return RunManifestV2(
        specification_digest=spec.specification_hash(),
        strategy_artifact_digest=strategy_artifact_digest
        or _digest({"strategy": spec.experiment.strategy, "parent": spec.experiment.parent_order.model_dump()}),
        generator_bundle_digest=generator_bundle_digest
        or _digest({"bundle": "simulation-exchange-v2", "world_type": spec.world_type}),
        campaign_commitment=_digest({"world_id": spec.world_id, "seed": spec.seed}),
        seed_material_digest=_digest({"seed": spec.seed, "world_id": spec.world_id}),
        protocol_version="event-kernel-v2",
    )


class ExchangeEngineV2(Exchange):
    """Default forward-test exchange: V1 CLOB matching + V2 event-kernel ledger."""

    engine_version = "v2"

    def __init__(
        self,
        symbols: list[str],
        spec: ExchangeSpec,
        *,
        kernel: EventKernelV2,
    ) -> None:
        super().__init__(symbols, spec)
        self.kernel = kernel
        self._venue_sequence = 0
        self._command_sequence = 0
        self._last_exchange_time_ns = -1

    def _next_venue_sequence(self) -> int:
        self._venue_sequence += 1
        return self._venue_sequence

    def _next_command_id(self, prefix: str, order_id: str) -> str:
        self._command_sequence += 1
        return f"{prefix}-{self._command_sequence:020d}-{order_id}"

    def _monotonic_exchange_time_ns(self, requested_ns: int) -> int:
        """Ledger events must be totally ordered; coerce time forward when needed."""
        candidate = max(int(requested_ns), self._last_exchange_time_ns + 1)
        self._last_exchange_time_ns = candidate
        return candidate

    def _exchange_time_ns(self, step: int, order: Order | None = None) -> int:
        if order is not None and order.exchange_arrival_time_ms is not None:
            requested = int(order.exchange_arrival_time_ms) * 1_000_000
        else:
            requested = int(step) * 1_000_000
        return self._monotonic_exchange_time_ns(requested)

    def _order_command(self, order: Order, step: int) -> OrderCommandV2:
        order_type = OrderTypeV2.MARKET if order.order_type == OrderType.MARKET else OrderTypeV2.LIMIT
        return OrderCommandV2(
            command_id=self._next_command_id("cmd", order.order_id),
            order_id=order.order_id,
            account_id=order.agent_id,
            instrument_id=order.symbol,
            side=SideV2.BUY if order.side == Side.BUY else SideV2.SELL,
            order_type=order_type,
            quantity=int(order.quantity),
            exchange_time_ns=self._exchange_time_ns(step, order),
            venue_sequence=self._next_venue_sequence(),
            price_ticks=None if order_type == OrderTypeV2.MARKET else int(order.price_ticks or 0),
            time_in_force=TimeInForceV2.DAY,
        )

    def submit(self, order: Order, step: int, *, max_match_quantity: int | None = None) -> list[Trade]:
        command = self._order_command(order, step)
        try:
            acknowledgement = self.kernel.admit(command)
        except OrderRejectedError as exc:
            self.record_submission(order)
            log_index = self._pending_log_indexes.pop(order.order_id)
            self.order_log[log_index].update(
                {
                    **order.to_dict(),
                    "arrival_step": step,
                    "accepted": False,
                    "status": "rejected",
                    "rejection_reason": str(exc),
                }
            )
            raise
        if acknowledgement.kind == EventKindV2.ORDER_REJECTED:
            self.record_submission(order)
            log_index = self._pending_log_indexes.pop(order.order_id)
            reason = str(acknowledgement.payload.get("reason", "rejected"))
            self.order_log[log_index].update(
                {
                    **order.to_dict(),
                    "arrival_step": step,
                    "accepted": False,
                    "status": "rejected",
                    "rejection_reason": reason,
                }
            )
            raise OrderRejectedError(reason)
        return super().submit(order, step, max_match_quantity=max_match_quantity)

    def cancel(self, request: CancelRequest, symbol: str) -> None:
        requested_ns = (
            int(request.effective_time_ms)
            if request.effective_time_ms is not None
            else int(request.submitted_step)
        ) * 1_000_000
        self.kernel.admit_cancel(
            CancelOrderCommandV2(
                command_id=self._next_command_id("cancel", request.order_id),
                order_id=request.order_id,
                account_id=request.agent_id,
                exchange_time_ns=self._monotonic_exchange_time_ns(requested_ns),
                venue_sequence=self._next_venue_sequence(),
            )
        )
        super().cancel(request, symbol)

    def cancel_pending(
        self,
        order: Order,
        *,
        request_step: int,
        request_time_ms: int,
        effective_step: int,
        effective_time_ms: int,
    ) -> None:
        self.kernel.admit_cancel(
            CancelOrderCommandV2(
                command_id=self._next_command_id("cancel-pending", order.order_id),
                order_id=order.order_id,
                account_id=order.agent_id,
                exchange_time_ns=self._monotonic_exchange_time_ns(int(effective_time_ms) * 1_000_000),
                venue_sequence=self._next_venue_sequence(),
            )
        )
        super().cancel_pending(
            order,
            request_step=request_step,
            request_time_ms=request_time_ms,
            effective_step=effective_step,
            effective_time_ms=effective_time_ms,
        )

    def ledger_digest(self) -> str:
        return self.kernel.ledger.digest

    def provenance(self) -> dict[str, Any]:
        return {
            "engine_version": self.engine_version,
            "protocol_version": self.kernel.ledger.manifest.protocol_version,
            "ledger_digest": self.ledger_digest(),
            "manifest": {
                "specification_digest": self.kernel.ledger.manifest.specification_digest,
                "strategy_artifact_digest": self.kernel.ledger.manifest.strategy_artifact_digest,
                "generator_bundle_digest": self.kernel.ledger.manifest.generator_bundle_digest,
                "campaign_commitment": self.kernel.ledger.manifest.campaign_commitment,
                "seed_material_digest": self.kernel.ledger.manifest.seed_material_digest,
                "protocol_version": self.kernel.ledger.manifest.protocol_version,
            },
            "event_count": len(self.kernel.ledger.events),
        }
