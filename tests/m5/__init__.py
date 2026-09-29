"""M5 accounting-hardening test-only package.

This package is verification infrastructure. It is never imported by
``app`` and it is never a runtime dependency of Market Fuzzer or of the
vendored FWF accounting kernel.

Contents:

* :mod:`tests.m5.chart`      - the normalized 19-account chart shared by both
  sides of the differential oracle, plus the debit-positive sign convention.
* :mod:`tests.m5.events`     - the canonical business-event model. It imports
  neither the FWF kernel nor ``python-accounting`` and carries no precomputed
  journal lines.
* :mod:`tests.m5.sequences`  - deterministic, precondition-aware generation of
  exactly 100 valid event sequences.
* :mod:`tests.m5.fwf_side`   - applies a business event to the vendored FWF
  accounting kernel.
* :mod:`tests.m5.oracle_side` - maps the *same* business event, independently,
  into ``python-accounting`` (MIT, pinned 1.0.1).
* :mod:`tests.m5.planted`    - validation-only accounting defects used to prove
  the oracle is capable of disagreeing with the FWF kernel.
* :mod:`tests.m5.runner`     - executes sequences, compares normalized trial
  balances after every event, and builds machine-readable evidence.
"""
