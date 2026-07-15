# Student submission contract

Submit a UTF-8 CSV with exactly this header:

```csv
date,asset,position
2026-01-01,ASSET_01,0.20
```

Requirements:

- One row for every public date and every public asset.
- ISO dates inside the published public window only.
- Asset names exactly as published.
- Finite numeric positions between -1.0 and 1.0.
- Gross exposure no greater than 1.5 per date.
- Absolute net exposure no greater than 1.0 per date.
- No duplicate date/asset keys.

The public dataset includes returns and features for research. The submission itself contains positions only. Hidden dates and hidden regime labels are never accepted.

The explanation is assessed separately from deterministic metrics. It should state the hypothesized mechanism, why it might generalize, and what evidence would change the conclusion.

GPT feedback is an interpretation of measured evidence, not a score or an integrity verdict.
