from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.analyst import (
    FailureAnalysis,
    _validate_grounding,
    analyze_failure,
    evidence_package,
)
from app.api.app import app
from app.product import DEFAULT_PROPERTIES, STRATEGIES, run_search


def _failure() -> dict:
    strategy = {
        "id": "pov_fragile",
        **STRATEGIES["pov_fragile"],
        "parameters": STRATEGIES["pov_fragile"]["defaults"],
    }
    return run_search(strategy, DEFAULT_PROPERTIES)


def test_no_key_analysis_is_explicit_and_evidence_bound(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    failure = _failure()
    result = analyze_failure(failure)
    assert result["status"] == "unavailable"
    assert result["mode"] == "deterministic_fallback"
    assert "no-key mode" in result["message"]
    assert result["analysis"]["evidence_references"]
    assert any("synthetic" in value.lower() for value in result["analysis"]["limitations"])


def test_grounding_rejects_unknown_references_and_numbers():
    evidence = evidence_package(_failure())
    valid = FailureAnalysis(
        summary="The property failed in the configured synthetic harness.",
        failure_mechanism="The recorded execution exceeded the configured property.",
        evidence_references=["property.participation"],
        why_the_neighbor_passes="The verified neighbor passes the same property.",
        why_the_correction_works="The corrected strategy passes the recorded test.",
        recommended_regression_assertions=["Replay the stored failure fixture."],
        limitations=["This is a synthetic-environment result only."],
    )
    assert _validate_grounding(valid, evidence) is valid
    with pytest.raises(ValueError, match="unknown evidence"):
        _validate_grounding(valid.model_copy(update={"evidence_references": ["invented.ref"]}), evidence)
    with pytest.raises(ValueError, match="unsupported numeric"):
        _validate_grounding(valid.model_copy(update={"summary": "The result proves 999 cases."}), evidence)


class _FakeResponses:
    def __init__(self, parsed: FailureAnalysis):
        self.parsed = parsed
        self.kwargs = None

    def parse(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(output_parsed=self.parsed)


class _FakeClient:
    def __init__(self, parsed: FailureAnalysis):
        self.responses = _FakeResponses(parsed)


def test_structured_client_output_is_validated_and_model_is_recorded():
    failure = _failure()
    parsed = FailureAnalysis(
        summary="The participation property failed in the synthetic harness.",
        failure_mechanism="The deterministic run records a participation violation.",
        evidence_references=["property.participation", "scenario.minimized"],
        why_the_neighbor_passes="The verified passing neighbor is evaluated independently.",
        why_the_correction_works="The corrected run passes the same recorded property.",
        recommended_regression_assertions=["Replay the failure fixture."],
        limitations=["This explanation is limited to the synthetic environment."],
    )
    client = _FakeClient(parsed)
    result = analyze_failure(failure, client=client, model="gpt-5.6")
    assert result["status"] == "complete"
    assert result["mode"] == "gpt-5.6"
    assert result["model"] == "gpt-5.6"
    assert client.responses.kwargs["text_format"] is FailureAnalysis
    assert client.responses.kwargs["model"] == "gpt-5.6"


def test_api_analysis_no_key_returns_explicit_fallback(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    client = TestClient(app)
    failure = client.post("/api/searches", json={"strategy_id": "pov_fragile"}).json()
    response = client.post(f"/api/failures/{failure['id']}/analysis", json={})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "unavailable"
    assert body["mode"] == "deterministic_fallback"
    assert body["evidence"]["scenario"]["hash"] == failure["scenario_hash"]
