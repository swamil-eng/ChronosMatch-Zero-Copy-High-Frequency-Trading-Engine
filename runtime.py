"""EngineRuntime: owns the rings, the matching thread, metrics, ledger and simulator process.

Data path (no serialization anywhere):
    simulator process --mmap ring--> [consumer thread: book.submit/cancel] --> trades --> ledger
    API (Flask threads) --mmap ring-->                                         |-> snapshot -> UI

Everything the UI reads is built by the consumer thread itself and published as one
immutable dict, so request threads never touch the live book.
"""
import gc
import multiprocessing as mp
import os
import tempfile
import threading
import time
from collections import deque
from time import perf_counter_ns

from . import core
from .constants import ACT_CANCEL, ACT_NEW, MAX_TICKS, SIM_ID_BASE, TICK
from .ledger import Ledger
from .metrics import LatencyHistogram
from .ringbuffer import RingBuffer
from . import simulator

BATCH = 512
BUSY_POLL = bool(os.environ.get("CHRONOS_BUSY_POLL"))   # spin instead of sleeping: lower latency, burns a core
WHALE_LEVELS = 3


def _workdir():
    base = "/dev/shm" if os.path.isdir("/dev/shm") else tempfile.gettempdir()
    return tempfile.mkdtemp(prefix="chronosmatch-", dir=base)


def _rs(ticks):
    return round(ticks * TICK, 2)


class EngineRuntime:
    def __init__(self, ledger_path="chronosmatch_ledger.db", ring_capacity=1 << 16,
                 whale_levels=WHALE_LEVELS):
        self.workdir = _workdir()
        self.ring_api = RingBuffer.create(os.path.join(self.workdir, "api.ring"), ring_capacity)
        self.ring_sim = RingBuffer.create(os.path.join(self.workdir, "sim.ring"), ring_capacity)
        self.ledger = Ledger(ledger_path)
        self.whale_levels = whale_levels
        self.backend = core.BACKEND
        self._api_lock = threading.Lock()
        self._next_api_id = 1
        self._ctx = mp.get_context("spawn")
        self._rate = self._ctx.Value("d", 0.0)
        self._sim_sent = self._ctx.Value("q", 0)
        self._sim_dropped = self._ctx.Value("q", 0)
        self._sim_stop = None
        self._sim_proc = None
        self._stop = threading.Event()
        self._thread = None
        self._reset_flag = False
        self._in_match = False
        self.busy_poll = BUSY_POLL          # can be toggled live from the dashboard
        self._reset_state()
        self._snapshot = {"ready": False}

    # ---- state -----------------------------------------------------------------
    def _reset_state(self):
        self.book = core.Book()
        self.e2e = LatencyHistogram()      # producer timestamp -> match finished
        self.eng = LatencyHistogram()      # duration of the submit() call only
        self.recent_e2e = deque(maxlen=600)
        self.recent_eng = deque(maxlen=600)
        self.trades = deque(maxlen=300)
        self.whales = deque(maxlen=20)
        self.mine = set()
        self.processed = self.cancels = self.rejected = 0
        self.trade_count = self.volume = 0
        self.last_price = 0
        self.gc_during_match = 0
        self.idle_collections = 0
        self.errors = 0
        self.last_error = None
        self._rate_prev = (time.monotonic(), 0)
        self._rate_now = 0.0
        self._sim_sent.value = 0
        self._sim_dropped.value = 0

    # ---- lifecycle ----------------------------------------------------------------
    def start(self):
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="engine", daemon=True)
            self._thread.start()
        return self

    def close(self):
        self.stop_simulator()
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)
        self.ledger.close()
        for ring in (self.ring_api, self.ring_sim):
            ring.close(unlink=True)
        try:
            os.rmdir(self.workdir)
        except OSError:
            pass

    # ---- commands from the API ----------------------------------------------------------
    def submit_order(self, side, price_ticks, qty):
        """Queue a manual order. Returns the order id, or None if the ring is full."""
        with self._api_lock:
            oid = self._next_api_id
            if not self.ring_api.push(oid, side, ACT_NEW, qty, price_ticks):
                return None
            self._next_api_id += 1
            return oid

    def cancel_order(self, order_id):
        with self._api_lock:
            return self.ring_api.push(order_id, 0, ACT_CANCEL, 0, 0)

    def start_simulator(self, rate):
        self._rate.value = float(rate)
        if self._sim_proc is not None and self._sim_proc.is_alive():
            return
        self._sim_stop = self._ctx.Event()
        self._sim_proc = self._ctx.Process(
            target=simulator.run_process, name="chronos-sim", daemon=True,
            args=(self.ring_sim.path, self._rate, self._sim_stop,
                  self._sim_sent, self._sim_dropped, None))
        self._sim_proc.start()

    def stop_simulator(self):
        if self._sim_proc is not None:
            self._sim_stop.set()
            self._sim_proc.join(timeout=3)
            if self._sim_proc.is_alive():
                self._sim_proc.terminate()
                self._sim_proc.join(timeout=2)     # never leave a second producer on the ring
            self._sim_proc = None
        self._rate.value = 0.0

    @property
    def simulator_running(self):
        return self._sim_proc is not None and self._sim_proc.is_alive()

    def reset(self):
        """Stop the feed, empty the book and statistics (the SQLite ledger is kept)."""
        self.stop_simulator()
        self._reset_flag = True
        for _ in range(100):
            if not self._reset_flag:
                break
            time.sleep(0.01)

    def snapshot(self):
        return self._snapshot

    def recent_ledger(self, limit=50):
        total, rows = self.ledger.recent(limit)
        return {"total": total, "rows": [
            {"run": r[0], "id": r[1], "t": r[2], "buy": r[3], "sell": r[4],
             "price": _rs(r[5]), "qty": r[6]} for r in rows]}

    # ---- engine thread --------------------------------------------------------------------
    def _gc_callback(self, phase, info):
        if phase == "start" and self._in_match:
            self.gc_during_match += 1

    def _run(self):
        gc.disable()                      # no automatic GC pauses while matching
        gc.callbacks.append(self._gc_callback)
        last_pub = last_gc = time.monotonic()
        while not self._stop.is_set():
            try:
                if self._reset_flag:
                    self.ring_api.discard_pending()
                    self.ring_sim.discard_pending()
                    self._reset_state()
                    self._publish()
                    self._reset_flag = False
                did = 0
                for ring, is_api in ((self.ring_api, True), (self.ring_sim, False)):
                    recs = ring.pop_batch(BATCH)
                    if recs:
                        did += len(recs)
                        self._in_match = True
                        self._process(recs, is_api)
                        self._in_match = False
                now = time.monotonic()
                if now - last_pub >= 0.1:
                    self._publish()
                    last_pub = now
                if now - last_gc >= (1.0 if not did else 5.0):
                    gc.collect()              # between batches, never inside the match loop
                    self.idle_collections += 1
                    last_gc = now
                if not did and not self.busy_poll:
                    time.sleep(0.0002)
            except Exception as exc:          # keep matching alive; surface the error in /api/metrics
                self._in_match = False
                self.errors += 1
                self.last_error = "%s: %s" % (type(exc).__name__, exc)
                time.sleep(0.01)

        gc.callbacks.remove(self._gc_callback)

    def _process(self, recs, is_api):
        book = self.book
        submit, cancel, lookup = book.submit, book.cancel, book.lookup
        e2e_add, eng_add = self.e2e.add, self.eng.add
        recent_e2e, recent_eng = self.recent_e2e, self.recent_eng
        stride = 0 if len(recs) < 16 else 15
        whales = []
        for i, (oid, side, act, _r, qty, price, ts) in enumerate(recs):
            t0 = perf_counter_ns()
            if act == ACT_NEW:
                st = submit(oid, side, price, qty)
                t1 = perf_counter_ns()
                if st < 0:
                    self.rejected += 1
                    continue
                e2e_add(t1 - ts)
                eng_add(t1 - t0)
                if not (i & stride):
                    recent_e2e.append((t1 - ts) / 1000)
                    recent_eng.append((t1 - t0) / 1000)
                if st and book.last_levels_cleared >= self.whale_levels:
                    whales.append((oid, side, book.last_levels_cleared, qty, price))
                if is_api and lookup(oid) is not None:
                    self.mine.add(oid)
            else:
                cancel(oid)
                self.cancels += 1
        self.processed += len(recs)
        fills = book.drain_fills()
        if fills:
            wall = time.time()
            for taker, maker, tside, price, qty in fills:
                self.trade_count += 1
                self.volume += qty
                self.last_price = price
                buy, sell = (taker, maker) if tside == 0 else (maker, taker)
                tid = self.trade_count
                self.trades.append((tid, wall, buy, sell, price, qty, taker))
                self.ledger.put(tid, wall, buy, sell, price, qty)
        for oid, side, levels, qty, price in whales:
            self.whales.append({"order": oid, "side": "BUY" if side == 0 else "SELL",
                                "levels": levels, "qty": qty, "price": _rs(price), "t": time.time()})

    def _publish(self):
        book, now = self.book, time.monotonic()
        t_prev, n_prev = self._rate_prev
        if now - t_prev >= 0.5:
            inst = (self.processed - n_prev) / (now - t_prev)
            self._rate_now = inst if not self._rate_now else 0.6 * self._rate_now + 0.4 * inst
            self._rate_prev = (now, self.processed)
        bids, asks = book.depth(15)
        bb, ba = book.best_bid, book.best_ask
        mine = []
        for oid in list(self.mine):
            r = book.lookup(oid)
            if r is None:
                self.mine.discard(oid)
            else:
                mine.append({"id": oid, "side": "BUY" if r[0] == 0 else "SELL",
                             "price": _rs(r[1]), "qty": r[2]})
        mine.sort(key=lambda m: m["id"])
        self._snapshot = {
            "ready": True, "t": time.time(),
            "book": {"bids": [[_rs(p), q] for p, q in bids], "asks": [[_rs(p), q] for p, q in asks],
                     "best_bid": _rs(bb) if bb else None, "best_ask": _rs(ba) if ba else None,
                     "spread": _rs(ba - bb) if bb and ba else None,
                     "resting": book.resting, "last_price": _rs(self.last_price) if self.last_price else None},
            "mine": mine,
            "trades": [{"id": t[0], "t": t[1], "buy": t[2], "sell": t[3], "price": _rs(t[4]),
                        "qty": t[5], "taker": t[6]} for t in list(self.trades)[-40:]][::-1],
            "whales": list(self.whales)[-6:][::-1],
            "metrics": {
                "backend": self.backend, "errors": self.errors, "last_error": self.last_error,
                "busy_poll": self.busy_poll, "processed": self.processed, "orders_per_sec": round(self._rate_now),
                "trades": self.trade_count, "volume": self.volume, "rejected": self.rejected,
                "cancels": self.cancels, "e2e_us": self.e2e.summary_us(), "engine_us": self.eng.summary_us(),
                "gc": {"during_match": self.gc_during_match, "idle_collections": self.idle_collections,
                       "auto_enabled": gc.isenabled()},
                "sim": {"running": self.simulator_running, "rate": self._rate.value,
                        "sent": self._sim_sent.value, "dropped": self._sim_dropped.value},
                "ring": {"api": len(self.ring_api), "sim": len(self.ring_sim),
                         "capacity": self.ring_sim.capacity},
                "ledger": {"queued": self.ledger.queued, "written": self.ledger.written,
                           "run": self.ledger.run_id},
                "series": {"e2e_us": list(self.recent_e2e), "engine_us": list(self.recent_eng)},
            },
        }
