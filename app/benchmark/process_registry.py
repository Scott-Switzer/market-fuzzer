"""Evaluator-private registry of market process families (M10.6.1).

M10.6 selected the evaluation process family from the public
:class:`~app.market.process.ProcessFamilyKind` enum, so every family an official
evaluation could use had to exist in the participant-facing repository. That
supports *open-source* process-family OOD evaluation, but not an
**evaluator-private mechanism holdout**, where the model is scored on a valid
process family whose implementation was never available during training.

This module keeps :class:`~app.market.process.ProcessFamily` as the behavioural
contract and adds an explicit registry of *family definitions*:

* a definition names a family with a stable, versioned identifier and builds one
  :class:`ProcessFamily` per :class:`ProcessNodeRole` at a requested volatility
  scale;
* :func:`default_process_registry` holds exactly the three public families, so
  the open M10.6 benchmark is unchanged;
* a trusted evaluator builds its own registry and calls
  :meth:`MarketProcessRegistry.register` with a definition of its own. The public
  enum never has to change.

Nothing is loaded dynamically. A definition is a Python object supplied by code
that already runs inside the evaluator process: there is no import path, no
``eval``, no module name read from an HTTP request, and no environment variable
naming a module to import. The registry, a private family id, and a family
commitment are evaluator-private; a participant's model never receives any of
them (see the public/private boundary in ``docs/SYNTHETIC_EXCHANGE_BENCHMARK.md``).

``ProcessFamilyKind`` and ``MECHANISM_FAMILIES`` remain as convenience metadata
for the built-in families. They are no longer the type boundary for evaluation:
:func:`default_process_registry` is the authoritative public list, and a test pins
the enum and the registry together so the two cannot drift.
"""

from __future__ import annotations

import math
import re
from abc import ABC, abstractmethod
from dataclasses import fields, is_dataclass
from enum import StrEnum
from typing import Any, ClassVar

from app.benchmark.hashing import digest
from app.market.engine import GjrGarchT, ProcessFamily
from app.market.process import (
    GJR_FACTOR_T_V1,
    MARKOV_REGIME_JUMP_FACTOR_T_V1,
    STOCHASTIC_VOL_FACTOR_T_V1,
    MarkovRegimeJumpFactorT,
    StochasticVolFactorT,
)

__all__ = [
    "EVALUATOR_PRIVATE_FAMILY",
    "FAMILIAR_FAMILY_ID",
    "FamilyVisibility",
    "MarketProcessRegistry",
    "ProcessFamilyDefinition",
    "ProcessNodeRole",
    "UNKNOWN_PROCESS_FAMILY",
    "DuplicateProcessFamilyError",
    "UnknownProcessFamilyError",
    "default_process_registry",
    "family_commitment",
    "family_version",
    "validate_family_id",
]

#: Machine-readable label a sealed world carries in public output instead of the
#: family id it actually ran on.
EVALUATOR_PRIVATE_FAMILY = "evaluator-private"

#: The family every familiar-partition world uses (the GJR-GARCH-t family).
FAMILIAR_FAMILY_ID = GJR_FACTOR_T_V1

#: Stable, versioned family identifiers: a lower-case slug that ends in ``_v<digit>``.
#: The version suffix is part of the contract -- a family whose *dynamics* change
#: must be published under a new identifier, so an old commitment cannot silently
#: start describing a different generator.
_FAMILY_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]{2,63}_v[0-9]+$")

#: The validity state a registry reports for an identifier it does not hold.
UNKNOWN_PROCESS_FAMILY = "UNKNOWN_PROCESS_FAMILY"


class ProcessNodeRole(StrEnum):
    """The three kinds of node a process family is instantiated for."""

    MARKET = "market"
    SECTOR = "sector"
    ENTITY = "entity"


class FamilyVisibility(StrEnum):
    """Whether a family is published in the participant-facing repository."""

    #: Shipped with the benchmark; a model may have been trained on it.
    PUBLIC = "public"
    #: Supplied by trusted evaluator code and never published.
    EVALUATOR_PRIVATE = "evaluator-private"


def validate_family_id(family_id: str) -> str:
    """Return ``family_id`` unchanged, or raise if it is not a stable versioned id."""

    if not isinstance(family_id, str) or not _FAMILY_ID_PATTERN.match(family_id):
        raise ValueError(
            f"a process family id must be a lower-case slug ending in a version, got {family_id!r}"
        )
    return family_id


def family_version(family_id: str) -> str:
    """The version suffix of a validated family id (``gjr_factor_t_v1`` -> ``v1``)."""

    return "v" + validate_family_id(family_id).rsplit("_v", 1)[1]


class UnknownProcessFamilyError(LookupError):
    """A plan or a world referenced a family the registry does not hold."""

    code: ClassVar[str] = UNKNOWN_PROCESS_FAMILY

    def __init__(self, family_id: str) -> None:
        super().__init__(f"{UNKNOWN_PROCESS_FAMILY}: no process family is registered as {family_id!r}")
        self.family_id = family_id


class DuplicateProcessFamilyError(ValueError):
    """A registry was asked to hold two definitions under one identifier."""


class ProcessFamilyDefinition(ABC):
    """A registered, versioned source of :class:`ProcessFamily` instances.

    A definition is the evaluator-side half of a family: it owns the *construction*
    policy (which parameters a role gets, how the ecology volatility scale is
    applied) while :class:`ProcessFamily` owns the *behaviour* (the innovations).
    An evaluator-private family therefore only has to implement the public
    :class:`ProcessFamily` interface plus this one small factory.
    """

    #: Stable, versioned identifier; validated on registration.
    family_id: ClassVar[str]
    #: Whether the family is published with the benchmark.
    visibility: ClassVar[FamilyVisibility] = FamilyVisibility.PUBLIC

    @abstractmethod
    def build(self, *, role: ProcessNodeRole, volatility_scale: float) -> ProcessFamily:
        """One family instance for ``role``, scaled by ``volatility_scale``."""

    @property
    def version(self) -> str:
        """The version suffix of :attr:`family_id`."""

        return family_version(self.family_id)

    @property
    def is_public(self) -> bool:
        return self.visibility is FamilyVisibility.PUBLIC

    @property
    def public_label(self) -> str:
        """What a *public* result may say about this family."""

        return self.family_id if self.is_public else EVALUATOR_PRIVATE_FAMILY


def family_descriptor(instance: ProcessFamily) -> dict[str, Any]:
    """A structural description of one family instance, for the commitment.

    Dataclass families contribute their field values; anything else contributes
    its class identity alone. The module name is deliberately excluded so moving
    a family between modules does not change its commitment.
    """

    descriptor: dict[str, Any] = {
        "family": instance.name,
        "class": type(instance).__qualname__,
        "config": {},
    }
    if is_dataclass(instance) and not isinstance(instance, type):
        descriptor["config"] = {field.name: getattr(instance, field.name) for field in fields(instance)}
    return descriptor


def family_commitment(definition: ProcessFamilyDefinition) -> str:
    """A digest of one family's role-normalized structure.

    The commitment is built at ``volatility_scale=1.0`` for every role, so it
    identifies the *generator* -- not the ecology level a particular world ran it
    at. It is evaluator-private evidence: it lets an authorized replay prove which
    generator produced a sealed world without naming the family in public output.
    """

    payload = []
    for role in ProcessNodeRole:
        instance = definition.build(role=role, volatility_scale=1.0)
        if not isinstance(instance, ProcessFamily):
            raise TypeError(
                f"family {definition.family_id!r} built a {type(instance).__name__}, "
                "which does not implement ProcessFamily"
            )
        payload.append({"role": role.value, "descriptor": family_descriptor(instance)})
    return digest(payload)


class MarketProcessRegistry:
    """An explicit ``family id -> definition`` registry.

    Resolution is by exact, validated identifier. There is no discovery, no
    import-by-name, and no fallback: an identifier the registry does not hold
    raises :class:`UnknownProcessFamilyError`, which is what makes a sealed plan
    fail loudly against the public registry instead of silently degrading.
    """

    def __init__(self) -> None:
        self._definitions: dict[str, ProcessFamilyDefinition] = {}
        self._commitments: dict[str, str] = {}

    def register(self, definition: ProcessFamilyDefinition) -> None:
        """Add ``definition``; duplicate identifiers are rejected."""

        family_id = validate_family_id(definition.family_id)
        if family_id in self._definitions:
            raise DuplicateProcessFamilyError(f"process family {family_id!r} is already registered")
        self._definitions[family_id] = definition
        self._commitments.pop(family_id, None)

    def resolve(self, family_id: str) -> ProcessFamilyDefinition:
        """The definition registered as ``family_id``."""

        try:
            return self._definitions[str(family_id)]
        except KeyError as exc:
            raise UnknownProcessFamilyError(str(family_id)) from exc

    def build(self, family_id: str, *, role: ProcessNodeRole, volatility_scale: float) -> ProcessFamily:
        """Instantiate one node of ``family_id`` for ``role``."""

        return self.resolve(family_id).build(role=role, volatility_scale=volatility_scale)

    def contains(self, family_id: str) -> bool:
        return str(family_id) in self._definitions

    def family_ids(self) -> tuple[str, ...]:
        """Every registered identifier, sorted."""

        return tuple(sorted(self._definitions))

    def public_family_ids(self) -> tuple[str, ...]:
        """The identifiers a participant-facing benchmark may publish, sorted."""

        return tuple(sorted(family_id for family_id, item in self._definitions.items() if item.is_public))

    def visibility(self, family_id: str) -> FamilyVisibility:
        return self.resolve(family_id).visibility

    def public_label(self, family_id: str) -> str:
        """What public output may say instead of ``family_id``."""

        return self.resolve(family_id).public_label

    def commitment(self, family_id: str) -> str:
        """The structural commitment of ``family_id`` (computed once per registry)."""

        definition = self.resolve(family_id)
        cached = self._commitments.get(definition.family_id)
        if cached is None:
            cached = family_commitment(definition)
            self._commitments[definition.family_id] = cached
        return cached


# --- the three public families ---------------------------------------------------
#
# Base GJR-GARCH-t parameters per node role (M10.5 values). The two held-out
# families are normalized to the same unconditional per-session variance, so the
# family axis changes the *dynamics* of the path, not its scale.

_GJR_PARAMS: dict[str, dict[str, float]] = {
    "market": {"omega": 4.0e-6, "alpha": 0.03, "gamma": 0.09, "beta": 0.88, "nu": 6.0},
    "sector": {"omega": 2.5e-6, "alpha": 0.04, "gamma": 0.08, "beta": 0.86, "nu": 7.0},
    "entity": {"omega": 6.0e-6, "alpha": 0.05, "gamma": 0.10, "beta": 0.83, "nu": 5.0},
}

# (phi, sigma_eta, nu) for the stochastic-volatility family, per node role.
_SV_SHAPE: dict[str, tuple[float, float, float]] = {
    "market": (0.96, 0.14, 5.0),
    "sector": (0.95, 0.15, 6.0),
    "entity": (0.97, 0.13, 4.5),
}

# Shared regime-jump template; the per-role instance is rescaled to match the
# GJR baseline's unconditional variance.
_MRJ_TEMPLATE: dict[str, Any] = {
    "transition": ((0.95, 0.05), (0.20, 0.80)),
    "means": (0.0006, -0.0015),
    "sigmas": (0.006, 0.018),
    "jump_scale": (0.4, 2.0),
    "jump_prob": 0.02,
    "jump_mean": -0.0002,
    "jump_sigma": 0.02,
    "nu": 5.0,
}


def _gjr_base_variance(role: ProcessNodeRole) -> float:
    params = _GJR_PARAMS[role]
    persistence = params["alpha"] + 0.5 * params["gamma"] + params["beta"]
    return params["omega"] / (1.0 - persistence)


def _scaled_garch(scale: float, *, role: ProcessNodeRole) -> GjrGarchT:
    """A GJR-GARCH-t with its variance scaled by ``scale`` (persistence unchanged)."""

    params = _GJR_PARAMS[role]
    return GjrGarchT(
        omega=params["omega"] * scale * scale,
        alpha=params["alpha"],
        gamma=params["gamma"],
        beta=params["beta"],
        nu=params["nu"],
    )


def _stochastic_vol(*, role: ProcessNodeRole, volatility_scale: float) -> StochasticVolFactorT:
    phi, sigma_eta, nu = _SV_SHAPE[role]
    log_variance = sigma_eta**2 / (1.0 - phi**2)
    # E[eps^2] = volatility_scale^2 * exp(mu + log_variance / 2), so matching the
    # GJR baseline's variance fixes mu exactly.
    mu = math.log(_gjr_base_variance(role)) - 0.5 * log_variance
    return StochasticVolFactorT(mu=mu, phi=phi, sigma_eta=sigma_eta, nu=nu, volatility_scale=volatility_scale)


def _markov_regime_jump(*, role: ProcessNodeRole, volatility_scale: float) -> MarkovRegimeJumpFactorT:
    """A regime-jump family rescaled to the GJR baseline's unconditional variance.

    Every innovation component (regime mean, regime volatility, and jump size) is
    scaled by a common factor, so the whole path scales by that factor and the
    matching is exact.
    """

    transition = _MRJ_TEMPLATE["transition"]
    jump_scale = _MRJ_TEMPLATE["jump_scale"]
    base = MarkovRegimeJumpFactorT(
        means=_MRJ_TEMPLATE["means"],
        sigmas=_MRJ_TEMPLATE["sigmas"],
        jump_scale=jump_scale,
        transition=transition,
        jump_prob=_MRJ_TEMPLATE["jump_prob"],
        jump_mean=_MRJ_TEMPLATE["jump_mean"],
        jump_sigma=_MRJ_TEMPLATE["jump_sigma"],
        nu=_MRJ_TEMPLATE["nu"],
    )
    factor = math.sqrt(_gjr_base_variance(role) / base.unconditional_variance())
    return MarkovRegimeJumpFactorT(
        means=tuple(value * factor for value in _MRJ_TEMPLATE["means"]),
        sigmas=tuple(value * factor for value in _MRJ_TEMPLATE["sigmas"]),
        jump_scale=jump_scale,
        transition=transition,
        jump_prob=_MRJ_TEMPLATE["jump_prob"],
        jump_mean=_MRJ_TEMPLATE["jump_mean"] * factor,
        jump_sigma=_MRJ_TEMPLATE["jump_sigma"] * factor,
        nu=_MRJ_TEMPLATE["nu"],
        volatility_scale=volatility_scale,
    )


class GjrFactorTDefinition(ProcessFamilyDefinition):
    """``gjr_factor_t_v1``: GJR-GARCH-t, the familiar family.

    ``sigma2_t = omega + alpha*eps2 + gamma*[eps<0]*eps2 + beta*sigma2`` with
    unit-variance Student-t shocks; volatility is a function of past squared
    returns.
    """

    family_id: ClassVar[str] = GJR_FACTOR_T_V1

    def build(self, *, role: ProcessNodeRole, volatility_scale: float) -> ProcessFamily:
        return _scaled_garch(volatility_scale, role=role)


class StochasticVolFactorTDefinition(ProcessFamilyDefinition):
    """``stochastic_vol_factor_t_v1``: log-normal stochastic volatility.

    A latent AR(1) log-variance with its own shock source, so volatility does not
    respond to past returns.
    """

    family_id: ClassVar[str] = STOCHASTIC_VOL_FACTOR_T_V1

    def build(self, *, role: ProcessNodeRole, volatility_scale: float) -> ProcessFamily:
        return _stochastic_vol(role=role, volatility_scale=volatility_scale)


class MarkovRegimeJumpFactorTDefinition(ProcessFamilyDefinition):
    """``markov_regime_jump_factor_t_v1``: a hidden regime chain with jumps."""

    family_id: ClassVar[str] = MARKOV_REGIME_JUMP_FACTOR_T_V1

    def build(self, *, role: ProcessNodeRole, volatility_scale: float) -> ProcessFamily:
        return _markov_regime_jump(role=role, volatility_scale=volatility_scale)


#: The three definitions the open benchmark publishes, in a fixed order.
PUBLIC_PROCESS_DEFINITIONS: tuple[ProcessFamilyDefinition, ...] = (
    GjrFactorTDefinition(),
    StochasticVolFactorTDefinition(),
    MarkovRegimeJumpFactorTDefinition(),
)


def default_process_registry() -> MarketProcessRegistry:
    """A fresh registry holding exactly the public families.

    A fresh instance each call: an evaluator that registers a private family into
    its own registry can never widen the public one by accident.
    """

    registry = MarketProcessRegistry()
    for definition in PUBLIC_PROCESS_DEFINITIONS:
        registry.register(definition)
    return registry
