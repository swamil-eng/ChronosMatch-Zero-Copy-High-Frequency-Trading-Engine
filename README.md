# ChronosMatch: Zero-Copy High-Frequency Trading Engine (educational)

A simulated matching engine that moves orders between Python processes through shared memory
and matches them in a C-level order book, with a live web dashboard, a curses terminal
monitor and a SQLite audit ledger. It is not connected to any brokerage or exchange and is not
for real trading.

```
simulator process --(mmap ring buffer, 32-byte records)--> engine thread --> SQLite ledger
 API / UI orders  --(second ring buffer)------------------>      |------> web dashboard, curses UI
```

## Run it

Python 3.10+ (developed on 3.12). Flask is the only required package.

```bash
pip install -r requirements.txt
python app.py                      # website: http://127.0.0.1:8000   dashboard: /dashboard
python -m unittest discover -s tests -v
```

Optional, compile the Cython engine (needs Cython and a C compiler; on Windows install the
Microsoft C++ Build Tools):

```bash
pip install cython
python setup.py build_ext --inplace
python app.py                      # prints "order book backend: cython"
python -m unittest tests.test_book -v   # also runs the Cython-vs-Python parity test
```

Without the build, the engine uses the pure-Python book automatically. Set `CHRONOS_FORCE_PYTHON=1`
to force it, or `CHRONOS_BUSY_POLL=1` to make the engine thread spin instead of sleeping between
batches (lower end-to-end latency, one CPU core at 100%). The same switch is the "Busy-poll engine" checkbox in the dashboard's System panel (`POST /api/engine {busy_poll: true}`). On Windows, sleeping for 0.2 ms can take about a millisecond or more, so it is worth trying there.

Terminal dashboard (Windows: `pip install windows-curses`):

```bash
python tools/dashboard_curses.py --local                       # own engine + feed
python tools/dashboard_curses.py --url http://127.0.0.1:8000   # watch a running app.py
```
Keys: `q` quit, `s` feed on/off, `+`/`-` feed rate, `r` reset.

## What is where

| Path | Role |
|---|---|
| `chronosmatch/ringbuffer.py` | Zero-copy IPC: SPSC ring buffer on an mmap'd file, `struct` records, no pickling |
| `chronosmatch/book_cy.pyx` | Cython order book: C structs, preallocated order pool, per-tick price levels, hash table for cancels |
| `chronosmatch/book_py.py` | Pure-Python book with the same API (fallback and test oracle) |
| `chronosmatch/runtime.py` | Engine thread, metrics, whale detection, simulator process control, snapshots for the UI |
| `chronosmatch/simulator.py` | asyncio order-flow generator running in its own process |
| `chronosmatch/ledger.py` | Background thread that batches trades into SQLite |
| `chronosmatch/engine.py`, `models.py` | The original starter engine, kept as a readable reference and as the test oracle |
| `app.py`, `web/` | Flask API (REST + Server-Sent Events) and the website / dashboard |
| `tools/` | `benchmark.py`, `ipc_audit.py`, `pipeline_bench.py`, `dashboard_curses.py` |

## HTTP API

| Method and path | Purpose |
|---|---|
| `POST /api/orders` `{side, price, qty}` | Queue an order (price in rupees, 2 decimals) |
| `DELETE /api/orders/<id>` | Queue a cancel |
| `GET /api/book`, `/api/trades`, `/api/metrics`, `/api/snapshot`, `/api/ledger` | Read state |
| `GET /api/stream` | Server-Sent Events, one snapshot every 100 ms |
| `POST /api/simulator` `{action: start/stop, rate}` | Control the market feed (max 150,000 orders/s) |
| `POST /api/reset` | Clear the book and statistics (ledger history is kept) |

Prices are integer ticks internally (1 tick = Rs 0.01). The matching rule is price-time priority
and a trade executes at the resting order's price.

## Measured results (this repository, one machine)

Produced by the scripts in `tools/`; raw output is in `benchmarks/`. Hardware: Linux x86-64,
**1 CPU core**, Python 3.12.3, **pure-Python book** (the Cython extension was not compiled here).
Rerun on your own machine before quoting numbers anywhere.

| Test | Result |
|---|---|
| Order book alone, 300,000 orders | about 850,000 orders/s; per-call latency p50 0.8 µs, p99 2.8 µs |
| 1,000,000 orders between two processes, ring buffer | 1.11 s, 0 corrupt |
| Same, `multiprocessing.Queue` (pickle) | 7.52 s, 0 corrupt (ring buffer 6.8x faster) |
| Full pipeline at 10,000 orders/s | engine p50 1.2 µs, end-to-end p50 213 µs, p99 459 µs |
| Full pipeline at 100,000 orders/s | 99,140 orders/s sustained, 0 dropped, engine p50 0.9 µs, end-to-end p50 1.3 ms, p99 29 ms |

How to read them: *engine* latency times only the `submit()` call; *end-to-end* runs from the
producer's timestamp to the match finishing and includes time waiting in the ring buffer. The
brief's "under 50 µs" target is met by the engine figure, not by end-to-end latency at high
load on this single-core machine, where the simulator, the engine and the web server share one core.
Percentiles come from a log-bucket histogram (about 12% resolution, lower bound of the bucket).
The GC counter on the dashboard shows pauses that began inside a match; it reads 0 because
automatic GC is disabled in the engine process and runs only between batches.

## Honest limits

- **The Cython file has not been compiled or run by the author of this code.** The environment it was
  written in had no Cython. `tests/test_book.py::test_cython_matches_python_exactly` compares it to
  the Python book on 30,000 random operations as soon as you build it; if the build or that test
  fails, the Python fallback still works and the rest of the project is unaffected.
- The "zero-copy" claim covers the process-to-process path (shared memory, no serialization).
  Reading a record into a Python tuple still allocates; the C-level book avoids Python objects
  only inside `submit()`.
- Ring-buffer indices are written with one native 8-byte store (`struct` format `"Q"`). An early version used `"<Q"`, which writes byte by byte; a stress test (`TornIndexTests`) caught a reader seeing a half-written index and the fix is in place.
- The ring buffer is single-producer / single-consumer and relies on x86-style memory ordering
  plus the GIL; it is a teaching implementation, not a lock-free production queue.
- The feed is an asyncio generator, not a real WebSocket client, and there is no live market data.
- Order ids in the fast path are integers (the starter used strings); the Flask development
  server is for demos only.
