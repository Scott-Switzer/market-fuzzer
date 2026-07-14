from app.simulation import run_simulation
from app.world import build_demo_world

first = run_simulation(build_demo_world(31415))
replay = run_simulation(build_demo_world(31415))
different = run_simulation(build_demo_world(31416))
assert first.result_hash == replay.result_hash, "same-seed replay diverged"
assert first.result_hash != different.result_hash, "different seed did not change the result"
print(f"determinism ok {first.result_hash}")
