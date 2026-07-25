"""Stand-in Account that matches the schema used by
``app.exchange.market.Account`` but avoids importing ``app.exchange``
via the package ``__init__`` (which transitively imports ``pydantic``).

This module is intentionally isolated so it can run in environments
where the project's editable ``.venv`` does not include all runtime
dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class BridgeAccount:
    agent_id: str
    cash_cents: int
    inventory: dict[str, int] = field(default_factory=dict)
