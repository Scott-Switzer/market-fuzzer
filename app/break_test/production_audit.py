from __future__ import annotations

import csv
import hashlib
import io
import json
import math
from collections import Counter
from datetime import datetime, timezone
from typing import Any

import numpy as np


def compute_returns(prices: list[float]) -> list[float]:
    arr = np.array(prices, dtype=float)
    return np.diff(arr) / arr[:-1]


def sorted_list(values: list[float]) -> list[float]:
    return sorted(values)


def percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    arr = np.array(values, dtype=float)
    return float(np.percentile(arr, q))


def threshold_violations(mark: dict[str, object], historical: dict[str, float | int]) -> list[str]:
    thresholds = mark.get("thresholds") or {}
    violations: list[str] = []
    for key, threshold in thresholds.items():
        op = threshold.get("operator", "gt")
        value = threshold.get("value", 0.0)
        actual = historical.get(key)
        if actual is None:
            continue
        if op == "lt" and float(actual) >= float(value):
            violations.append(f"{key} {actual} >= {value}")
        elif op == "gt" and float(actual) <= float(value):
            violations.append(f"{key} {actual} <= {value}")
        elif op == "eq":
            if isinstance(actual, bool):
                if bool(actual) != bool(value):
                    violations.append(f"{key} mismatch")
            else:
                if float(actual) != float(value):
                    violations.append(f"{key} mismatch")
    return violations


def audit_snapshot_json(mark: dict[str, object]) -> str:
    data = {
        "schema_version": "audit/v1",
        "mark": mark,
        "snapshot_at": datetime.now(timezone.utc).isoformat(),
    }
    return json.dumps(data, sort_keys=True)


def audit_snapshot_text(mark: dict[str, object]) -> str:
    lines = [
        f"AUDIT {mark.get('id','')} :: {mark.get('label','')}",
        f"Timestamp: {datetime.now(timezone.utc).isoformat()}",
        f"Inputs: prices={mark.get('inputs',{}).get('prices_len','')}, strategy={mark.get('inputs',{}).get('strategy_type','')}, params={mark.get('inputs',{}).get('params','')}, worlds={mark.get('inputs',{}).get('worlds_per_regime','')}",
    ]
    hist = mark.get("historical", {})
    lines.append("Historical metrics:")
    for key, value in hist.items():
        lines.append(f"  {key}: {value}")
    violations = mark.get("violations") or []
    lines.append(f"Violations: {len(violations)}")
    for violation in violations:
        lines.append(f"  - {violation}")
    return "\n".join(lines)


def rule_flag(prices: list[float], strategy_type: str, params: dict[str, int]) -> list[dict[str, object]]:
    marks: list[dict[str, object]] = []
    violation_count = 0
    entry = {
        "id": "",
        "label": "core",
        "inputs": {
            "prices_len": len(prices),
            "strategy_type": strategy_type,
            "params": params,
            "worlds_per_regime": 0,
        },
        "historical": {},
        "thresholds": {},
        "violations": [],
        "decision": "advance",
    }
    try:
        from app.break_test.metrics import backtest_metrics
        from app.break_test.strategies import compute_positions
        px = np.array(prices, dtype=float)
        pos = compute_positions(strategy_type, px, **params)
        hist = backtest_metrics(px, pos)
        entry["historical"] = hist
        marks.append({
            "id": "M1",
            "label": "core",
            "inputs": entry["inputs"],
            "historical": hist,
            "thresholds": [
                {"name": "return", "operator": "gt", "value": 0.0, "actual": hist.get("total_return_pct")},
                {"name": "sharpe", "operator": "gt", "value": 0.0, "actual": hist.get("sharpe")},
                {"name": "win_rate", "operator": "gt", "value": 35.0, "actual": hist.get("win_rate_pct")},
            ],
            "violations": [
                f"return {hist.get('total_return_pct')} <= 0.0" if float(hist.get("total_return_pct", 0.0)) <= 0 else None,
                f"sharpe {hist.get('sharpe')} <= 0.0" if float(hist.get("sharpe", 0.0)) <= 0 else None,
                f"win_rate {hist.get('win_rate_pct')} <= 35.0" if float(hist.get("win_rate_pct", 0.0)) <= 35 else None,
            ],
            "decision": "advance",
        })
        mark = marks[-1]
        mark["violations"] = [v for v in mark["violations"] if v]
        mark["decision"] = "reject" if mark["violations"] else "advance"
        violation_count += len(mark["violations"])
    except Exception as exc:  # noqa: BLE001
        entry["historical"] = {"error": str(exc)}
    return marks


def threshold_flags(historical: dict[str, float | int], thresholds: list[dict[str, Any]]) -> list[str]:
    return threshold_violations({"thresholds": thresholds}, historical)


def _bson_dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True)


def hash_value(value: Any) -> str:
    if isinstance(value, bytes):
        payload = value
    else:
        payload = _bson_dumps(value).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


def hash_csv(path: str) -> str:
    return hash_value(pathlib.Path(path).read_bytes())


def hash_response_json(payload: dict[str, Any], nonce: str = "") -> str:
    return hash_value((_bson_dumps(payload), nonce))


def build_repro_pack(seed: int, prices: list[float], strategy_type: str, params: dict[str, int], payload_hash: str) -> dict[str, object]:
    prices_hash = hash_value(prices)
    params_hash = hash_value({**params, "seed": seed})
    phantom_tag = hash_value((prices_hash, params_hash, "phantom"))[:16]
    return {
        "seed": seed,
        "prices_hash": prices_hash,
        "params_hash": params_hash,
        "phantom_hash": phantom_tag,
        "payload_json_sha256_truncated": payload_hash,
    }


def reproducibility_metadata(seed: int, prices: list[float], strategy_type: str, params: dict[str, int], payload: dict[str, Any] | None = None, nonce: str = "") -> dict[str, object]:
    payload_hash = hash_response_json(payload or {}, nonce)
    pack = build_repro_pack(seed, prices, strategy_type, params, payload_hash)
    return {
        "checkpoint": {
            "version": "repro/v2",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "package": pack,
            "strategy_type": strategy_type,
            "params": params,
            "seed": seed,
        },
    }


def failure_violation_report(marks: list[dict[str, object]]) -> dict[str, object]:
    if not marks:
        return {"violations": [], "summary": {"failure_count": 0, "failure_rate": 0.0}, "failure_regimes": [], "overall_decision": "advance"}
    violations: list[str] = []
    failure_counts: Counter = Counter()
    severity = Counter({"blocker": 0, "warning": 0})
    for mark in marks:
        mark_violations = mark.get("violations") or []
        violations.extend(mark_violations)
        if mark.get("decision") == "reject":
            failure_counts[mark.get("label", mark.get("id", ""))] += 1
            severity["blocker"] += sum(1 for item in mark.get("thresholds") or [] if item and item.get("operator") != "eq")
    failures = [{"mark_id": m.get("id"), "rule": m.get("label"), "violations": m.get("violations") or [], "severity": "blocker" if m.get("decision") == "reject" else "warning"} for m in marks]
    reported_mark_count = sum(failure_counts.values())
    return {
        "violations": violations,
        "summary": {"failure_count": reported_mark_count, "failure_rate": reported_mark_count / len(marks) if marks else 0.0},
        "failure_regimes": sorted(set(failure_counts.keys())),
        "overall_decision": "advance" if not violations else "review",
    }


def pca_regime_persistence(regimes: list[dict[str, object]], top_k: int = 2) -> dict[str, object]:
    mat = np.array([
        [r.get("loss_rate_pct", 0.0), r.get("worst_drawdown_pct", 0.0), r.get("median_return_pct", 0.0)] for r in regimes
    ], dtype=float)
    if mat.shape[0] < 2:
        return {"components": mat.tolist(), "eigenvalues": [], "top_k_explained": []}
    mat -= np.mean(mat, axis=0)
    cov = np.cov(mat.T)
    eigvals, eigvecs = np.linalg.eigh(cov)
    idx = np.argsort(eigvals)[::-1][:top_k]
    return {
        "components": eigvecs[:, idx].tolist(),
        "eigenvalues": eigvals[idx].tolist(),
        "top_k_explained": [float(eigval / eigvals.sum()) for eigval in eigvals[idx]],
    }
