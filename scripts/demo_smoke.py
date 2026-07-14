from app.compiler import compile_offline
from app.simulation import run_simulation

compiled = compile_offline("thin liquidity, earnings shock, and forced seller", 42)
result = run_simulation(compiled.spec)
assert result.trades and result.timeline and result.summary["filled_quantity"] > 0
print(f"no-key demo smoke ok {result.result_hash}")
