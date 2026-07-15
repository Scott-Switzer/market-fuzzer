# Quant Challenge Arena — Devpost draft

## Tagline

The public backtest winner is not always the robust winner.

## Short description

Quant Challenge Arena is an Education-track challenge platform where students build quantitative strategies against a public synthetic market, submit strict position files, and receive deterministic evaluation across hidden regimes. Instructors create the challenge, release only the public panel, and reveal robustness evidence after the submission window.

## Full write-up

Many quant competitions reward the highest public Sharpe ratio. Quant Challenge Arena makes that objective incomplete. A student receives a fictional three-asset panel with a visible momentum regime, submits one position per date and asset, and explains the mechanism. The server then evaluates the same submission against hidden structural-break, one-day-delay, liquidity-shock, and false-feature regimes that the student never received.

The deterministic engine owns dataset generation, CSV validation, public/hidden metrics, transaction-cost sensitivity, one-day delay sensitivity, regime decomposition, integrity indicators, and scoring. Example A is designed to win the public leaderboard through concentration and then collapse in hidden regimes. Example B has a lower public score but wins the robustness-adjusted ranking. The result is a reproducible teaching exercise about out-of-sample evidence, not an investment recommendation or misconduct detector.

## Built with

Python, FastAPI, Pydantic, NumPy, Typer, OpenAI Responses API/GPT-5.6 (optional), Codex, HTML, CSS, JavaScript, Pytest, Ruff, Mypy, and GitHub Actions.

## Installation and judge testing

Follow `docs/TESTING.md`. Start the server, open the instructor console, approve the seeded challenge, bundle the public data, run the two included fixtures, then switch to Student to validate or submit a `date,asset,position` CSV. Release hidden results from the instructor console to show the ranking reversal. The `/market-fuzzer` route preserves the earlier execution-testing workflow.

## GPT-5.6 and Codex

GPT-5.6 may generate a schema-constrained challenge brief and grounded feedback. It never generates the panel, sees hidden data through student routes, scores, ranks, or decides an integrity verdict. Codex implemented and repaired the deterministic Arena engine, role-scoped API, submission contract, public/hidden leaderboard, UI, tests, and documentation. Add the final `/feedback` session ID before submission.

## Limitations

The product evaluates strategies only within its declared fictional deterministic environment. It does not prove alpha, future profitability, real-market generalization, student intent, institutional fidelity, or regulatory compliance. Hidden-data role checks use a demo header rather than production authentication. The earlier Market Fuzzer workflow remains secondary infrastructure and is not silently represented as the Arena.

## License

MIT. No proprietary market data or secrets are committed.
