"""Bounded concurrent health/readiness load gate for the Docker appliance."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import time
import urllib.request


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--requests", type=int, default=100)
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--p95-ms", type=float, default=2000)
    args = parser.parse_args()
    if not 1 <= args.requests <= 10_000 or not 1 <= args.concurrency <= 64:
        raise SystemExit("load bounds exceeded")

    def request(index: int) -> float:
        started = time.perf_counter()
        path = "/api/ready" if index % 2 else "/api/health"
        with urllib.request.urlopen(args.base_url + path, timeout=5) as response:
            if response.status != 200:
                raise RuntimeError(f"HTTP {response.status}")
        return (time.perf_counter() - started) * 1000

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        timings = list(pool.map(request, range(args.requests)))
    ordered = sorted(timings)
    p95 = ordered[max(0, int(len(ordered) * 0.95) - 1)]
    result = {
        "requests": len(timings),
        "concurrency": args.concurrency,
        "p95_ms": round(p95, 3),
        "max_ms": round(max(timings), 3),
        "errors": 0,
    }
    print(json.dumps(result, sort_keys=True))
    if p95 > args.p95_ms:
        raise SystemExit(f"p95 {p95:.1f}ms exceeds {args.p95_ms:.1f}ms gate")


if __name__ == "__main__":
    main()
