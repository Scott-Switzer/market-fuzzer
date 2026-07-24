from app.break_test.validation_quality import validation_quality_score


def test_quality_score_full_pass() -> None:
    result = validation_quality_score(
        deflated_sharpe=1.2,
        pbo=0.01,
        turnover=1.0,
        max_drawdown_pct=-8.0,
        regime_robustness=0.8,
    )
    assert result["pass"] is True
    assert result["gates_passed"] == 5
    assert 0.0 <= result["score"] <= 100.0


def test_quality_score_fails_multiple_gates() -> None:
    result = validation_quality_score(
        deflated_sharpe=0.1,
        pbo=0.2,
        turnover=5.0,
        max_drawdown_pct=-30.0,
        regime_robustness=0.1,
    )
    assert result["pass"] is False
    assert result["gates_passed"] == 0
