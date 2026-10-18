"""Full-pipeline benchmark: simulator process -> shared-memory ring -> engine thread.

    python tools/pipeline_bench.py [--rates 10000 50000 100000] [--seconds 6] [--out FILE]

Reports sustained orders/s and end-to-end latency (producer timestamp to match finished).
perf_counter_ns is a system-wide monotonic clock, so timestamps compare across processes.
"""
import argparse
import json
import os
import platform
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rates", type=int, nargs="+", default=[10_000, 50_000, 100_000])
    ap.add_argument("--seconds", type=float, default=6)
    ap.add_argument("--out")
    a = ap.parse_args()
    from chronosmatch import core
    from chronosmatch.runtime import EngineRuntime
    rt = EngineRuntime(ledger_path=os.path.join(tempfile.mkdtemp(), "bench.db")).start()
    runs = []
    try:
        for rate in a.rates:
            rt.reset()
            rt.start_simulator(rate)
            time.sleep(1.0)
            rt.reset_counters = None
            base = rt.snapshot()["metrics"]["processed"]
            t0 = time.time()
            time.sleep(a.seconds)
            m = rt.snapshot()["metrics"]
            dt = time.time() - t0
            rt.stop_simulator()
            r = lambda d: {k: round(v, 1) for k, v in d.items() if k != "n"}
            runs.append({"target_rate": rate, "sustained_orders_per_sec": round((m["processed"] - base) / dt),
                         "dropped": m["sim"]["dropped"], "engine_us": r(m["engine_us"]), "end_to_end_us": r(m["e2e_us"]),
                         "gc_pauses_in_match": m["gc"]["during_match"]})
            print(json.dumps(runs[-1]))
    finally:
        rt.close()
    res = {"backend": core.BACKEND, "python": platform.python_version(), "machine": platform.platform(),
           "cpu_count": os.cpu_count(), "seconds_per_run": a.seconds, "runs": runs, "when": time.strftime("%Y-%m-%d")}
    if a.out:
        with open(a.out, "w") as f:
            json.dump(res, f, indent=2)


if __name__ == "__main__":
    main()
