import cProfile
import io
import pstats

from app.break_test.exchange_fwd import build_world
from app.simulation import run_simulation

world = build_world(
    "steady_trend", seed=40_001, asset_count=8, target_asset="AAPL", universe_preset="eight_assets"
)
pr = cProfile.Profile()
pr.enable()
sim = run_simulation(world)
pr.disable()
s = io.StringIO()
ps = pstats.Stats(pr, stream=s).sort_stats("cumulative")
ps.print_stats(40)
print(s.getvalue())
