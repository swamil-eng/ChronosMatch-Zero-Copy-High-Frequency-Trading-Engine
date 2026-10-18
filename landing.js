(() => {
  const $ = (id) => document.getElementById(id);
  let last = null, running = false;

  $("demo").addEventListener("click", async () => {
    try { await CM.api("/api/simulator", "POST", running ? { action: "stop" } : { action: "start", rate: 20000 }); }
    catch (e) { $("liveNote").textContent = e.message; }
  });

  function render(s) {
    last = s;
    const m = s.metrics, b = s.book;
    running = m.sim.running;
    $("demo").textContent = running ? "Stop demo feed" : "Start live demo feed";
    $("liveState").textContent = running ? "Feed running at " + CM.int(m.sim.rate) + " orders/s" : "Feed stopped";
    $("liveOps").textContent = running ? CM.int(m.orders_per_sec) + " orders/s processed" : "";
    const max = Math.max(1, ...b.bids.slice(0, 5).map((l) => l[1]), ...b.asks.slice(0, 5).map((l) => l[1]));
    $("asks").innerHTML = CM.ladderRows(b.asks.slice(0, 5), "ask", new Set(), max);
    $("bids").innerHTML = CM.ladderRows(b.bids.slice(0, 5), "bid", new Set(), max);
    $("spread").textContent = b.spread == null ? "–" : CM.inr(b.spread);
    $("trades").textContent = CM.int(m.trades);
    CM.trace($("trace"), [{ values: m.series.e2e_us, color: CM.css("--bid") }, { values: m.series.engine_us, color: CM.css("--ink") }], 50);
    $("liveNote").textContent = m.engine_us.n ? "Engine median " + CM.us(m.engine_us.p50) + ", 99th percentile " + CM.us(m.engine_us.p99) + " (" + m.backend + " order book)." : "The book is empty. Start the demo feed to watch it fill and clear.";
  }
  CM.connect(render, (ok) => { if (!ok) $("liveState").textContent = "Reconnecting to the engine"; });
  window.addEventListener("resize", () => last && render(last));

  Promise.all(["engine", "ipc", "pipeline"].map((n) => fetch("/static/data/" + n + ".json").then((r) => r.json()))).then(([e, i, p]) => {
    const rows = [
      ["Order book, " + CM.int(e.orders) + " orders (" + e.backend + " backend)", CM.int(e.orders_per_sec) + " orders/s"],
      ["Engine latency per order, p50 / p99 / max", e.latency_us.p50 + " µs / " + e.latency_us.p99 + " µs / " + CM.us(e.latency_us.max)],
      ["Shared-memory ring buffer, " + CM.int(i.orders) + " orders between two processes", i.ring_buffer.seconds + " s (" + CM.int(i.ring_buffer.orders_per_sec) + " orders/s), " + i.ring_buffer.corrupt + " corrupt"],
      ["multiprocessing.Queue with pickling, same orders", i.mp_queue_pickle.seconds + " s (" + CM.int(i.mp_queue_pickle.orders_per_sec) + " orders/s), " + i.mp_queue_pickle.corrupt + " corrupt"],
      ["Ring buffer speed-up over the pickled queue", i.speedup + "×"],
    ].concat(p.runs.map((r) => [
      "Full pipeline at " + CM.int(r.target_rate) + " orders/s: engine p50 / end-to-end p50 / end-to-end p99",
      CM.int(r.sustained_orders_per_sec) + " orders/s sustained; " + r.engine_us.p50 + " µs / " + CM.us(r.end_to_end_us.p50) + " / " + CM.us(r.end_to_end_us.p99) + "; " + r.dropped + " dropped, " + r.gc_pauses_in_match + " GC pauses in match"]));
    document.querySelector("#resTable tbody").innerHTML = rows.map((r) => "<tr><td>" + r[0] + '</td><td class="r">' + r[1] + "</td></tr>").join("");
    $("machine").textContent = "Measured " + e.when + " on " + e.machine + ", " + e.cpu_count + " CPU core(s), Python " + e.python + ".";
  }).catch(() => { document.querySelector("#resTable tbody").innerHTML = '<tr><td colspan="2">Run <code>python tools/benchmark.py --out web/static/data/engine.json</code> and <code>python tools/ipc_audit.py --out web/static/data/ipc.json</code> to fill this table.</td></tr>'; });
})();
