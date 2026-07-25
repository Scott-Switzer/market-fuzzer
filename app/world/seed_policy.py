from __future__ import annotations

import hashlib
import os
from typing import Literal

SeedPolicy = Literal["ENTROPY", "FROZEN", "DISCRETE_DIFFICULTY"]


def _stable_hash(value: object) -> str:
    import json

    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def _entropy_seed() -> int:
    return int.from_bytes(os.urandom(8), "little") % (2**63 - 1)


def resolve_seed(
    policy: SeedPolicy,
    user_seed: int | None = None,
    *,
    session_key: str | None = None,
    universe_key: str | None = None,
    challenge_seed: str | None = None,
) -> tuple[int, dict[str, str | int | None]]:
    """Resolve a generator seed according to the requested policy.

    Returns a deterministic bound when replay is required, replay-free entropy
    otherwise, or a difficulty-weighted bound for benchmark jobs.
    """
    if policy == "FROZEN":
        if user_seed is None:
            raise ValueError("FROZEN seed policy requires user_seed")
        return int(user_seed) % (2**31 - 1), {
            "policy": policy,
            "user_supplied_seed": int(user_seed),
            "derived_session_hash": _stable_hash(session_key) if session_key else None,
            "universe_seed_hash": _stable_hash(universe_key) if universe_key else None,
            "challenge_seed": challenge_seed,
        }
    if policy == "ENTROPY":
        seed = _entropy_seed()
        return seed, {
            "policy": policy,
            "user_supplied_seed": None,
            "derived_session_hash": _stable_hash(session_key) if session_key else None,
            "universe_seed_hash": _stable_hash(universe_key) if universe_key else None,
            "challenge_seed": challenge_seed,
        }
    if policy == "DISCRETE_DIFFICULTY":
        if not challenge_seed:
            raise ValueError("DISCRETE_DIFFICULTY requires challenge_seed")
        base = int(_stable_hash({"challenge_seed": challenge_seed, "universe": universe_key})[:12], 16)
        seed = base % (2**31 - 1)
        return seed, {
            "policy": policy,
            "user_supplied_seed": None,
            "derived_session_hash": _stable_hash(session_key) if session_key else None,
            "universe_seed_hash": _stable_hash(universe_key) if universe_key else None,
            "challenge_seed": challenge_seed,
        }
    raise ValueError(f"unknown seed policy: {policy!r}")


class _CommitHash:
    """Lazy commit hash helper used in DISCRETE_DIFFICULTY metadata."""


def commit_hash() -> str | None:
    try:
        import subprocess

        return (
            subprocess.check_output(["git", "rev-parse", "HEAD"], stderr=subprocess.DEVNULL).decode().strip()
        )
    except Exception:
        return None
