# Regression fixtures

Market Fuzzer exports schema `1.1` YAML and JSON fixtures. A fixture preserves:

- case ID and source failure ID;
- scenario hash and market parameters;
- exact strategy ID, type, version, and parameters;
- safety properties and operators;
- deterministic seed list;
- expected overall and targeted-property outcomes;
- severity/minimization policy versions;
- reproduction command.

Run a fixture directly:

```bash
smw test artifacts/market_fuzzer/failure_<id>.yaml
smw test artifacts/market_fuzzer/failure_<id>.json
```

Run a directory of YAML and JSON fixtures:

```bash
smw test artifacts/market_fuzzer
```

The CLI validates the schema, verifies the scenario hash, loads the exact stored strategy ID, reruns every stored seed, checks overall and targeted outcomes, prints the result, and exits nonzero for mismatches or invalid fixtures. The API regression-suite endpoint uses the same execution helper and reports `total`, `passing`, `failing`, `fixed`, `newly_failing`, and `invalid`.
