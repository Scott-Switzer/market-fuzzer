"""Latency distributions for exchange message lifecycle realism."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class LatencyDistribution:
    """Log-normal jitter around nominal feed / entry / cancel latencies."""

    feed_ms: float = 2.0
    order_entry_ms: float = 5.0
    cancel_ms: float = 4.0
    exchange_processing_ms: float = 1.0
    jitter_sigma: float = 0.35
    drop_probability: float = 0.001
    cancel_before_arrival_slack_ms: float = 0.0

    def sample(self, nominal_ms: float, rng: np.random.Generator) -> float:
        """Draw a positive latency around ``nominal_ms`` with log-normal noise."""
        nominal = max(0.05, float(nominal_ms))
        noise = float(rng.lognormal(mean=0.0, sigma=self.jitter_sigma))
        # Center so E[noise]≈1 for small sigma.
        centered = noise / max(np.exp(0.5 * self.jitter_sigma**2), 1e-9)
        return max(0.05, nominal * centered)

    def sample_feed(self, rng: np.random.Generator) -> float:
        return self.sample(self.feed_ms, rng)

    def sample_entry(self, rng: np.random.Generator) -> float:
        proc = self.sample(self.exchange_processing_ms, rng)
        return self.sample(self.order_entry_ms, rng) + proc

    def sample_cancel(self, rng: np.random.Generator) -> float:
        return self.sample(self.cancel_ms, rng) + self.sample(self.exchange_processing_ms, rng)

    def message_dropped(self, rng: np.random.Generator) -> bool:
        return bool(rng.random() < self.drop_probability)

    def arrival_time_us(self, request_time_us: int, latency_ms: float) -> int:
        """Monotonic microsecond arrival; never before request."""
        delta_us = max(0, int(round(float(latency_ms) * 1000.0)))
        return int(request_time_us) + delta_us

    def cancel_arrives_before_order(
        self,
        order_request_us: int,
        order_latency_ms: float,
        cancel_request_us: int,
        cancel_latency_ms: float,
    ) -> bool:
        order_arrive = self.arrival_time_us(order_request_us, order_latency_ms)
        cancel_arrive = self.arrival_time_us(cancel_request_us, cancel_latency_ms)
        return cancel_arrive + int(self.cancel_before_arrival_slack_ms * 1000) < order_arrive


def profile_to_distribution(name: str) -> LatencyDistribution:
    profiles = {
        "low": LatencyDistribution(feed_ms=0.5, order_entry_ms=1.0, cancel_ms=0.8, jitter_sigma=0.2),
        "normal": LatencyDistribution(feed_ms=2.0, order_entry_ms=5.0, cancel_ms=4.0, jitter_sigma=0.35),
        "high": LatencyDistribution(
            feed_ms=10.0, order_entry_ms=25.0, cancel_ms=20.0, jitter_sigma=0.5, drop_probability=0.01
        ),
    }
    return profiles.get(name, profiles["normal"])
