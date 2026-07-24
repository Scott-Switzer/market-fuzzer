---
name: overnight-mvp-vertical-slice
description: Execute a vertical slice through an overnight MVP plan for a quant strategy validation system. Covers DSL integration with existing schemas, compile/approve API wiring, frontend patching, focused test creation, and deterministic hash alignment. Use when asked to run an overnight implementation plan end-to-end.
metadata:
  author: scottthomasswitzer
  version: "1.0"
---

# Overnight MVP Vertical Slice Execution

## When to use
- User asks you to run an overnight implementation plan as written
- Existing codebase needs a new vertical slice wired end-to-end
- Must integrate with existing DSL/schema rather than duplicating

## Execution order
1. Inspect existing schema/files before creating new ones
2. Extend existing modules with new fields/states/etc
3. Write compiler/service modules that import from actual on-disk symbols
4. Wire router into `app/api/app.py` with `app.include_router(...)`
5. Patch existing frontend pages (do not rebuild from scratch if one exists)
6. Add focused tests in `tests/strategy_lab/` or new directory
7. Run `pytest tests/... -q` and fix exact errors from test output
8. Re-run until green, then run live API gate test via `fastapi.testclient`
9. Summarize passes and offer to save as skill

## Key pitfalls
- Never replace an existing page when patch will do
- Never invent imports—inspect `__all__` and read actual symbols
- Canonical hashes must exclude transient fields (`strategy_id`, `approval`, `provenance`, `conflict_report`) to be deterministic
- `OrderedClause` not `Order`
- `MacroGate` requires `retract_by_bar`
- Use `.venv312/bin/python` consistently (not bare `python3` which may resolve to wrong venv)
- Do not claim success without running actual pytest or HTTP check

## Verification pattern
```bash
pytest tests/strategy_lab/test_*.py -q
python -c "from fastapi.testclient import TestClient; ..."
```

## Output
- 5 tests green in new file
- Live API compile → approve flow verified via TestClient
- Frontend wired to new endpoints
- Router mounted in app.py
