from __future__ import annotations

from typing import Any

import numpy as np

_SAFE_BUILTINS: dict[str, Any] = {
    "abs": abs,
    "all": all,
    "any": any,
    "bool": bool,
    "dict": dict,
    "enumerate": enumerate,
    "filter": filter,
    "float": float,
    "int": int,
    "isinstance": isinstance,
    "len": len,
    "list": list,
    "map": map,
    "max": max,
    "min": min,
    "range": range,
    "round": round,
    "slice": slice,
    "sorted": sorted,
    "str": str,
    "sum": sum,
    "tuple": tuple,
    "type": type,
    "zip": zip,
    "True": True,
    "False": False,
    "None": None,
    "__import__": __import__,
}
_ALLOWED_ALIASES = {"np", "numpy"}
_STRATEGY_TEMPLATE = '''def strategy(observations, params):
    """Run a strategy against a list of observations and return actions.

    Parameters
    ----------
    observations : list[dict]
        Each dict has keys: step, symbol, side, mid_ticks, best_bid_ticks,
        best_ask_ticks, spread_bps, observed_volume, inventory,
        remaining_quantity, exchange_latency_profile, intervention_active
    params : dict
        User-supplied strategy parameters.

    Returns
    -------
    list[dict]
        One action dict per observation, each with keys: action_type
        (hold|market|limit), side, quantity, rationale_code.
    """
{code}
'''


def validate_strategy_code(code: str) -> None:
    try:
        compile(code, "<strategy>", "exec")
    except SyntaxError as exc:
        raise ValueError(f"Invalid Python syntax: {exc}") from exc


def run_python_strategy(
    code: str,
    observations: list[dict[str, Any]],
    params: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    validate_strategy_code(code)
    module_globals: dict[str, Any] = {
        "__builtins__": _SAFE_BUILTINS,
    }
    safe_locals: dict[str, Any] = {}
    try:
        exec(code, module_globals, safe_locals)
    except Exception as exc:
        raise ValueError(f"Strategy code execution failed: {exc}") from exc

    if "strategy" not in safe_locals:
        raise ValueError("Strategy code must define a `strategy(observations, params)` function")

    try:
        result = safe_locals["strategy"](observations, params or {})
    except Exception as exc:
        raise ValueError(f"Strategy function raised: {exc}") from exc

    if not isinstance(result, list):
        raise ValueError("Strategy function must return a list of actions")
    for action in result:
        _validate_action(action)
    return result


def run_python_strategy_with_np(
    code: str,
    observations: list[dict[str, Any]],
    params: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    validate_strategy_code(code)
    _assert_no_unsafe_imports(code)
    safe_np = _build_numpy_safe()
    module_globals: dict[str, Any] = {
        "__builtins__": _SAFE_BUILTINS,
        "np": safe_np,
    }
    safe_locals: dict[str, Any] = {}
    try:
        exec(code, module_globals, safe_locals)
    except Exception as exc:
        raise ValueError(f"Strategy code execution failed: {exc}") from exc

    if "strategy" not in safe_locals:
        raise ValueError("Strategy code must define a `strategy(observations, params)` function")

    try:
        result = safe_locals["strategy"](observations, params or {})
    except Exception as exc:
        raise ValueError(f"Strategy function raised: {exc}") from exc

    if not isinstance(result, list):
        raise ValueError("Strategy function must return a list of actions")
    for action in result:
        _validate_action(action)
    return result


def _build_numpy_safe() -> Any:
    ns = type("SafeNumpy", (), {})()
    ns.array = np.array
    ns.mean = np.mean
    ns.std = np.std
    ns.max = np.max
    ns.min = np.min
    ns.median = np.median
    ns.abs = np.abs
    ns.diff = np.diff
    ns.convolve = np.convolve
    ns.ones = np.ones
    ns.zeros = np.zeros
    ns.where = np.where
    ns.cumsum = np.cumsum
    ns.log = np.log
    ns.exp = np.exp
    ns.sqrt = np.sqrt
    ns.concatenate = np.concatenate
    return ns


def _assert_no_unsafe_imports(code: str) -> None:
    try:
        import ast

        tree = ast.parse(code)
    except Exception:
        return
    imports: list[str] = []

    class ImportVisitor(ast.NodeVisitor):
        def visit_Import(self, node: ast.Import) -> None:
            for alias in node.names:
                imports.append(alias.name.split(".")[0])

        def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
            if node.module:
                imports.append(node.module.split(".")[0])

    ImportVisitor().visit(tree)
    unexpected = [name for name in imports if name not in _ALLOWED_ALIASES]
    if unexpected:
        raise ValueError(
            f"Strategy code uses disallowed imports: {unexpected}. Allowed: {sorted(_ALLOWED_ALIASES)}"
        )


def _validate_action(action: Any) -> None:
    if not isinstance(action, dict):
        raise ValueError(f"Each action must be a dict, got {type(action).__name__}")
    action_type = action.get("action_type")
    if action_type not in ("hold", "market", "limit"):
        raise ValueError(f"Invalid action_type: {action_type}")
    if action_type == "hold":
        return
    side = action.get("side")
    if side not in ("buy", "sell"):
        raise ValueError(f"market/limit actions require side=buy or sell, got {side}")
    quantity = action.get("quantity", 0)
    if not isinstance(quantity, int) or quantity < 1:
        raise ValueError(f"market/limit actions require positive integer quantity, got {quantity}")
