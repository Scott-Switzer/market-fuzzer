# Arena data and role boundary

This Build Week prototype uses a simple `X-Role: instructor|student` header to demonstrate API separation. It is not authentication and must be replaced by authenticated course membership before deployment.

Student-facing endpoints return:

- The approved public challenge brief.
- Public rows without hidden dates or regime labels.
- Public validation results and public score.
- A public leaderboard without hidden metrics.

Instructor-only endpoints return:

- Hidden regime manifest and latent labels.
- The instructor dataset bundle.
- Hidden metrics and robustness ranking.
- All submission evidence and the release action.

The hidden dataset is generated from the challenge seed inside the server process. It is not written into the public static bundle and is not included in `public_challenge`, `public_dataset`, student submission responses, or unreleased leaderboards. Production hardening should add authenticated roles, persistent access-control audit logs, rate limits, CSRF protection for browser writes, encrypted storage, and separate instructor/student data stores.

The project accepts CSV positions only. It does not execute uploaded Python or accept arbitrary strategy code. CSV size, row count, dates, assets, positions, and exposures are bounded before evaluation.
