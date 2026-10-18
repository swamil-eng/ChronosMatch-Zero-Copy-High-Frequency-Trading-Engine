"""Week-4 resiliency: a background thread flushes matched trades to a permanent SQLite ledger."""
import queue
import sqlite3
import threading
import time

SCHEMA = """CREATE TABLE IF NOT EXISTS trades(
    run_id TEXT NOT NULL, trade_id INTEGER NOT NULL, ts REAL NOT NULL,
    buy_order_id INTEGER NOT NULL, sell_order_id INTEGER NOT NULL,
    price_ticks INTEGER NOT NULL, quantity INTEGER NOT NULL,
    PRIMARY KEY (run_id, trade_id))"""


class Ledger:
    def __init__(self, path, flush_interval=0.05, max_batch=5000):
        self.path = path
        self.run_id = time.strftime("%Y%m%d-%H%M%S")
        self.written = 0
        self._q = queue.SimpleQueue()
        self._interval, self._max = flush_interval, max_batch
        conn = sqlite3.connect(path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(SCHEMA)
        conn.commit()
        conn.close()
        self._thread = threading.Thread(target=self._run, name="ledger", daemon=True)
        self._thread.start()

    def put(self, trade_id, ts, buy_id, sell_id, price_ticks, qty):
        self._q.put((self.run_id, trade_id, ts, buy_id, sell_id, price_ticks, qty))

    @property
    def queued(self):
        return self._q.qsize()

    def _run(self):
        conn = sqlite3.connect(self.path)
        conn.execute("PRAGMA synchronous=NORMAL")
        stop = False
        while not stop:
            rows = []
            try:
                item = self._q.get(timeout=self._interval)
                while True:
                    if item is None:
                        stop = True
                        break
                    rows.append(item)
                    if len(rows) >= self._max:
                        break
                    item = self._q.get_nowait()
            except queue.Empty:
                pass
            if rows:
                conn.executemany("INSERT OR IGNORE INTO trades VALUES (?,?,?,?,?,?,?)", rows)
                conn.commit()
                self.written += len(rows)
        conn.close()

    def recent(self, limit=50):
        conn = sqlite3.connect(self.path)
        try:
            rows = conn.execute(
                "SELECT run_id, trade_id, ts, buy_order_id, sell_order_id, price_ticks, quantity "
                "FROM trades ORDER BY rowid DESC LIMIT ?", (limit,)).fetchall()
            total = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
        finally:
            conn.close()
        return total, rows

    def close(self):
        self._q.put(None)
        self._thread.join(timeout=5)
