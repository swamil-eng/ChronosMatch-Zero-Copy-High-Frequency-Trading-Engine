"""Reproducible engine benchmark.  python tools/benchmark.py [--orders 300000] [--out FILE]

Measures the order book alone (no IPC): throughput, then per-call latency percentiles with
time.perf_counter_ns around each submit(). Workload is a seeded random flow, so reruns on
the same machine are comparable. Results depend on hardware: record the machine you used.
"""
import argparse
import gc
import json
import os
import platform
import random
import sys
import time
from time import perf_counter_ns

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from chronosmatch import core                      # noqa: E402
from chronosmatch.metrics import LatencyHistogram  # noqa: E402


def workload(n, seed=42):
    rng = random.Random(seed)
    out, mid = [], 10_000
    for oid in range(1, n + 1):
        mid = min(14_000, max(6_000, mid + rng.choice((-1, 0, 0, 1))))
        side = rng.getrandbits(1)
        out.append((oid, side, mid + int(rng.gauss(-3 if side == 0 else 3, 8)), rng.randint(1, 200)))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--orders", type=int, default=300_000)
    ap.add_argument("--out")
    a = ap.parse_args()
    orders = workload(a.orders)

    book = core.Book()
    gc.disable()
    t0 = perf_counter_ns()
    for o in orders:
        book.submit(*o)
    dt = (perf_counter_ns() - t0) / 1e9
    fills = len(book.drain_fills())
    thr = a.orders / dt

    book = core.Book()
    hist, add = LatencyHistogram(), None
    add = hist.add
    submit = book.submit
    for o in orders:
        t = perf_counter_ns()
        submit(*o)
        add(perf_counter_ns() - t)
        if not (o[0] & 4095):
            book.drain_fills()
    s = hist.summary_us()
    res = {"backend": core.BACKEND, "python": platform.python_version(), "machine": platform.platform(),
           "cpu_count": os.cpu_count(), "orders": a.orders, "fills": fills,
           "orders_per_sec": round(thr), "latency_us": {k: round(v, 2) for k, v in s.items() if k != "n"},
           "note": "engine-only submit() latency, histogram lower bounds (~12% resolution)",
           "when": time.strftime("%Y-%m-%d")}
    print(json.dumps(res, indent=2))
    if a.out:
        with open(a.out, "w") as f:
            json.dump(res, f, indent=2)


if __name__ == "__main__":
    main()
