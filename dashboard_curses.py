"""Terminal latency monitor and order-book view (Python curses).

    python tools/dashboard_curses.py --local               # runs its own engine + feed
    python tools/dashboard_curses.py --url http://127.0.0.1:8000   # watches a running app.py

Keys: q quit | s start/stop feed | + / - change feed rate | r reset book
Windows needs:  pip install windows-curses
"""
import argparse
import curses
import json
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

BARS = " ▁▂▃▄▅▆▇█"
RATES = [1_000, 5_000, 10_000, 25_000, 50_000, 100_000]


class Source:
    def snapshot(self): raise NotImplementedError
    def feed(self, on, rate): raise NotImplementedError
    def reset(self): raise NotImplementedError


class LocalSource(Source):
    def __init__(self):
        from chronosmatch.runtime import EngineRuntime
        self.rt = EngineRuntime(ledger_path="chronosmatch_ledger.db").start()

    def snapshot(self): return self.rt.snapshot()
    def feed(self, on, rate): self.rt.start_simulator(rate) if on else self.rt.stop_simulator()
    def reset(self): self.rt.reset()
    def close(self): self.rt.close()


class RemoteSource(Source):
    def __init__(self, url): self.url = url.rstrip("/")

    def _call(self, path, body=None):
        req = urllib.request.Request(self.url + path, data=None if body is None else json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=2) as r:
            return json.load(r)

    def snapshot(self):
        try: return self._call("/api/snapshot")
        except OSError: return {"ready": False}

    def feed(self, on, rate):
        self._call("/api/simulator", {"action": "start", "rate": rate} if on else {"action": "stop"})

    def reset(self): self._call("/api/reset", {})


def sparkline(values, width, ascii_only):
    if not values: return ""
    vals = values[-width:]
    import math
    logs = [math.log10(max(v, 0.1)) for v in vals]
    lo, hi = min(logs), max(logs)
    span = (hi - lo) or 1
    chars = " .:-=+*#%@" if ascii_only else BARS
    return "".join(chars[int((x - lo) / span * (len(chars) - 1))] for x in logs)


def fmt_us(v):
    return "-" if v is None else ("%.1f ms" % (v / 1000) if v >= 1000 else "%.2f us" % v)


def draw(scr, s, state, ascii_only):
    scr.erase()
    h, w = scr.getmaxyx()
    def put(y, x, text, attr=0):
        if 0 <= y < h and x < w:
            try: scr.addnstr(y, x, text, max(0, w - x - 1), attr)
            except curses.error: pass
    put(0, 0, " ChronosMatch latency monitor ".ljust(w), curses.A_REVERSE)
    if not s.get("ready"):
        put(2, 2, "Waiting for the engine...  (q to quit)")
        scr.refresh(); return
    m, b = s["metrics"], s["book"]
    sim = m["sim"]
    put(1, 1, "feed: %s @ %s/s   backend: %s   processed: %s   trades: %s   GC pauses in match: %d"
        % ("ON" if sim["running"] else "off", format(int(state["rate"]), ","), m["backend"],
           format(m["processed"], ","), format(m["trades"], ","), m["gc"]["during_match"]))
    e, t = m["engine_us"], m["e2e_us"]
    put(3, 1, "            p50        p95        p99        max", curses.A_BOLD)
    put(4, 1, "engine   %-10s %-10s %-10s %-10s" % tuple(fmt_us(e[k]) for k in ("p50", "p95", "p99", "max")))
    put(5, 1, "end2end  %-10s %-10s %-10s %-10s" % tuple(fmt_us(t[k]) for k in ("p50", "p95", "p99", "max")))
    put(6, 1, "throughput: %s orders/s   ring backlog: %s" % (format(m["orders_per_sec"], ","), m["ring"]["api"] + m["ring"]["sim"]))
    width = max(10, w - 14)
    put(8, 1, "engine  " + sparkline(m["series"]["engine_us"], width, ascii_only))
    put(9, 1, "end2end " + sparkline(m["series"]["e2e_us"], width, ascii_only))
    # order book
    whale = s["whales"][0] if s["whales"] and time.time() - s["whales"][0]["t"] < 3 else None
    y0 = 11
    put(y0, 1, "      ASK price      qty          BID price      qty", curses.A_BOLD)
    rows = min(10, max(len(b["asks"]), len(b["bids"])), max(1, h - y0 - 5))
    for i in range(rows):
        if i < len(b["asks"]): put(y0 + 1 + i, 1, "      %9.2f %8d" % tuple(b["asks"][i]), curses.color_pair(2))
        if i < len(b["bids"]): put(y0 + 1 + i, 28, "%9.2f %8d" % tuple(b["bids"][i]), curses.color_pair(1))
    yb = y0 + 2 + rows
    put(yb, 1, "spread: %s   last: %s" % (b["spread"], b["last_price"]))
    if whale:
        put(yb + 1, 1, " WHALE %s %d cleared %d price levels from %.2f " % (whale["side"], whale["qty"], whale["levels"], whale["price"]),
            curses.A_REVERSE | curses.A_BOLD)
    for i, tr in enumerate(s["trades"][: max(0, h - yb - 4)]):
        put(yb + 3 + i, 1, "trade %-8d %9.2f x %d" % (tr["id"], tr["price"], tr["qty"]))
    put(h - 1, 0, " q quit | s feed on/off | +/- rate | r reset ", curses.A_REVERSE)
    scr.refresh()


def main(stdscr, src, ascii_only):
    curses.curs_set(0)
    stdscr.nodelay(True)
    if curses.has_colors():
        curses.start_color(); curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_BLUE, -1); curses.init_pair(2, curses.COLOR_RED, -1)
    state = {"rate": 10_000, "on": False}
    while True:
        draw(stdscr, src.snapshot(), state, ascii_only)
        k = stdscr.getch()
        try:
            if k in (ord("q"), 27): return
            if k == ord("s"): state["on"] = not state["on"]; src.feed(state["on"], state["rate"])
            elif k in (ord("+"), ord("=")): state["rate"] = next((r for r in RATES if r > state["rate"]), RATES[-1]); state["on"] and src.feed(True, state["rate"])
            elif k == ord("-"): state["rate"] = next((r for r in reversed(RATES) if r < state["rate"]), RATES[0]); state["on"] and src.feed(True, state["rate"])
            elif k == ord("r"): src.reset()
        except OSError:
            pass
        time.sleep(0.1)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--local", action="store_true")
    ap.add_argument("--url", default="http://127.0.0.1:8000")
    ap.add_argument("--ascii", action="store_true", help="ASCII sparkline for terminals without block characters")
    a = ap.parse_args()
    src = LocalSource() if a.local else RemoteSource(a.url)
    try:
        curses.wrapper(main, src, a.ascii)
    finally:
        if a.local: src.close()
