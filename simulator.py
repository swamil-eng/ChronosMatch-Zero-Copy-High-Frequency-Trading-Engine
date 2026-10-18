"""Market simulator (asyncio). Runs in its OWN process and writes orders straight into the
shared-memory ring buffer; it never talks to the engine through pickles or sockets.

Order flow: buys cluster just under a drifting mid price, sells just over it, ~12% of events
cancel a recent order, and about 1 in 1500 is a "whale" that sweeps many price levels.
"""
import asyncio
import random
from collections import deque

from .constants import ACT_CANCEL, ACT_NEW, SIM_ID_BASE
from .ringbuffer import RingBuffer

SLICE = 0.005   # seconds of order flow generated per event-loop turn


async def firehose(ring, rate_val, stop_evt, sent_val, dropped_val, seed=None):
    rng = random.Random(seed)
    rnd, gauss, randint = rng.random, rng.gauss, rng.randint
    push = ring.push
    mid = 10_000                       # Rs 100.00
    next_id = SIM_ID_BASE + 1
    live = []                          # recent ids we might cancel
    budget = 0.0
    loop = asyncio.get_running_loop()
    prev = loop.time()
    while not stop_evt.is_set():
        t0 = loop.time()
        budget += rate_val.value * min(t0 - prev, 0.05)   # real elapsed time, so the rate holds
        prev = t0
        n = int(budget)
        budget -= n
        sent = dropped = 0
        mid = min(14_000, max(6_000, mid + rng.choice((-1, 0, 0, 1))))
        for _ in range(n):
            r = rnd()
            if r < 0.12 and live:
                i = randint(0, len(live) - 1)
                oid, live[i] = live[i], live[-1]
                live.pop()
                ok = push(oid, 0, ACT_CANCEL, 0, 0)
            else:
                side = 1 if rnd() < 0.5 else 0
                if r > 0.99933:        # whale: huge and aggressive, sweeps ~40 ticks
                    qty = randint(3_000, 8_000)
                    price = mid + 40 if side == 0 else mid - 40
                else:
                    qty = randint(1, 200)
                    price = mid + int(gauss(-3 if side == 0 else 3, 8))
                price = min(199_999, max(1, price))
                oid = next_id
                next_id += 1
                ok = push(oid, side, ACT_NEW, qty, price)
                if ok:
                    if len(live) < 20_000:
                        live.append(oid)
                    else:
                        live[randint(0, 19_999)] = oid
            if ok:
                sent += 1
            else:
                dropped += 1           # ring full: the engine is not keeping up
        if sent:
            sent_val.value += sent
        if dropped:
            dropped_val.value += dropped
        await asyncio.sleep(max(0.0, SLICE - (loop.time() - t0)))


def run_process(ring_path, rate_val, stop_evt, sent_val, dropped_val, seed=None):
    """Entry point for multiprocessing (spawn-safe)."""
    ring = RingBuffer.attach(ring_path)
    try:
        asyncio.run(firehose(ring, rate_val, stop_evt, sent_val, dropped_val, seed))
    finally:
        ring.close()
