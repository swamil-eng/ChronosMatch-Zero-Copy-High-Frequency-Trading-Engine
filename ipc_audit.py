"""Mid-project IPC audit: shared-memory ring buffer vs multiprocessing.Queue (pickle).

    python tools/ipc_audit.py [--orders 1000000] [--out FILE]

Sends the same N orders from a producer process to this process both ways and reports
wall time and records/s. Also verifies that every order arrived intact and in order.
"""
import argparse
import json
import multiprocessing as mp
import os
import platform
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from chronosmatch.ringbuffer import RingBuffer  # noqa: E402


def ring_producer(path, n):
    ring = RingBuffer.attach(path)
    push = ring.push
    i = 1
    while i <= n:
        if push(i, i & 1, 0, i % 200 + 1, 10_000 + i % 97):
            i += 1
    ring.close()


def queue_producer(q, n):
    for i in range(1, n + 1):
        q.put((i, i & 1, 0, i % 200 + 1, 10_000 + i % 97))
    q.put(None)


def run_ring(n):
    path = os.path.join(tempfile.mkdtemp(), "audit.ring")
    ring = RingBuffer.create(path, 1 << 16)
    p = mp.get_context("spawn").Process(target=ring_producer, args=(path, n))
    t0 = time.perf_counter()
    p.start()
    got = bad = 0
    while got < n:
        for r in ring.pop_batch(4096):
            got += 1
            if r[0] != got or r[4] != got % 200 + 1:
                bad += 1
    dt = time.perf_counter() - t0
    p.join()
    ring.close(unlink=True)
    return dt, bad


def run_queue(n):
    ctx = mp.get_context("spawn")
    q = ctx.Queue(1 << 16)
    p = ctx.Process(target=queue_producer, args=(q, n))
    t0 = time.perf_counter()
    p.start()
    got = bad = 0
    while True:
        r = q.get()
        if r is None:
            break
        got += 1
        if r[0] != got:
            bad += 1
    dt = time.perf_counter() - t0
    p.join()
    return dt, bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--orders", type=int, default=1_000_000)
    ap.add_argument("--out")
    a = ap.parse_args()
    d1, b1 = run_ring(a.orders)
    d2, b2 = run_queue(a.orders)
    res = {"orders": a.orders, "machine": platform.platform(), "cpu_count": os.cpu_count(),
           "python": platform.python_version(),
           "ring_buffer": {"seconds": round(d1, 2), "orders_per_sec": round(a.orders / d1), "corrupt": b1},
           "mp_queue_pickle": {"seconds": round(d2, 2), "orders_per_sec": round(a.orders / d2), "corrupt": b2},
           "speedup": round(d2 / d1, 2), "when": time.strftime("%Y-%m-%d")}
    print(json.dumps(res, indent=2))
    if a.out:
        with open(a.out, "w") as f:
            json.dump(res, f, indent=2)


if __name__ == "__main__":
    main()
