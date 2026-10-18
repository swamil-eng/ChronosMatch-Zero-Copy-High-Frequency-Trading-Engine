(() => {
  const $ = (id) => document.getElementById(id);
  let last = null, lastWhale = 0, simRunning = false;

  const side = () => document.querySelector('input[name="side"]:checked').value;
  const syncButton = () => { const b = $("place"); b.textContent = "Place " + side().toLowerCase() + " order"; b.className = "btn " + (side() === "BUY" ? "buy" : "sell"); };
  document.querySelectorAll('input[name="side"]').forEach((r) => r.addEventListener("change", syncButton));

  function say(text, bad) { const m = $("msg"); m.textContent = text; m.className = "msg" + (bad ? " err" : ""); }

  $("place").addEventListener("click", async () => {
    try {
      const r = await CM.api("/api/orders", "POST", { side: side(), price: parseFloat($("price").value), qty: parseInt($("qty").value, 10) });
      say("Order " + r.order_id + " sent to the engine.");
    } catch (e) { say(e.message, true); }
  });
  $("feed").addEventListener("click", async () => {
    try {
      await CM.api("/api/simulator", "POST", simRunning ? { action: "stop" } : { action: "start", rate: parseInt($("rate").value, 10) });
    } catch (e) { say(e.message, true); }
  });
  $("rate").addEventListener("change", () => { if (simRunning) CM.api("/api/simulator", "POST", { action: "start", rate: parseInt($("rate").value, 10) }).catch((e) => say(e.message, true)); });
  $("reset").addEventListener("click", () => CM.api("/api/reset", "POST").then(() => say("Book and statistics cleared. The ledger keeps its history.")).catch((e) => say(e.message, true)));
  $("busy").addEventListener("change", () => CM.api("/api/engine", "POST", { busy_poll: $("busy").checked }).then(() => say($("busy").checked ? "Busy-poll on: lower latency, one CPU core at 100%." : "Busy-poll off.")).catch((e) => say(e.message, true)));
  document.addEventListener("click", (e) => { const row = e.target.closest(".row"); if (row && row.dataset.price) $("price").value = row.dataset.price; });
  $("mine").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-id]");
    if (b) CM.api("/api/orders/" + b.dataset.id, "DELETE").then(() => say("Cancel sent for order " + b.dataset.id + ".")).catch((x) => say(x.message, true));
  });

  function render(s) {
    last = s;
    const m = s.metrics, b = s.book;
    simRunning = m.sim.running;
    $("feed").textContent = simRunning ? "Stop feed" : "Start feed";
    if (simRunning && document.activeElement !== $("rate")) { const o = [...$("rate").options].find((x) => +x.value === m.sim.rate); if (o) $("rate").value = o.value; }
    $("backend").textContent = "Order book: " + m.backend;
    if (document.activeElement !== $("busy")) $("busy").checked = !!m.busy_poll;
    $("sBackend").textContent = m.backend === "cython" ? "Cython (C structs)" : "Python fallback";

    // latency
    const e = m.engine_us, t = m.e2e_us;
    $("e50").textContent = CM.us(e.p50); $("e95").textContent = CM.us(e.p95); $("e99").textContent = CM.us(e.p99); $("emax").textContent = CM.us(e.max);
    $("t50").textContent = CM.us(t.p50); $("t95").textContent = CM.us(t.p95); $("t99").textContent = CM.us(t.p99); $("tmax").textContent = CM.us(t.max);
    $("ops").textContent = CM.int(m.orders_per_sec);
    $("ntrades").textContent = CM.int(m.trades);
    $("gcm").textContent = m.gc.during_match;
    CM.trace($("trace"), [{ values: m.series.e2e_us, color: CM.css("--bid") }, { values: m.series.engine_us, color: CM.css("--ink") }], 50);

    // book
    const mineSet = new Set(s.mine.map((o) => o.price));
    const max = Math.max(1, ...b.bids.slice(0, 10).map((l) => l[1]), ...b.asks.slice(0, 10).map((l) => l[1]));
    $("asks").innerHTML = CM.ladderRows(b.asks.slice(0, 10), "ask", mineSet, max);
    $("bids").innerHTML = CM.ladderRows(b.bids.slice(0, 10), "bid", mineSet, max);
    $("spread").textContent = b.spread == null ? "–" : CM.inr(b.spread);
    $("last").textContent = b.last_price == null ? "–" : CM.inr(b.last_price);

    // whales
    const whaleIds = new Set(s.whales.map((w) => w.order));
    if (s.whales.length && s.whales[0].order !== lastWhale) {
      if (lastWhale) { const l = $("ladder"); l.classList.remove("flash"); void l.offsetWidth; l.classList.add("flash"); }
      lastWhale = s.whales[0].order;
    }
    $("whales").innerHTML = s.whales.length ? s.whales.map((w) =>
      '<div class="item whale-item"><span>' + w.side + " " + CM.int(w.qty) + "</span><span>" + w.levels + " levels from " + CM.inr(w.price) + "</span></div>").join("") : '<div class="empty">None yet.</div>';

    // trades
    if (!s.trades.length) {
      $("tape").innerHTML = '<div class="empty">No trades yet. Start the feed or place two crossing orders.</div>';
    } else {
      $("tape").innerHTML = s.trades.map((t, i) => {
        const prev = s.trades[i + 1], dir = !prev ? "" : t.price > prev.price ? "up" : t.price < prev.price ? "down" : "";
        const time = new Date(t.t * 1000).toLocaleTimeString([], { hour12: false });
        return '<div class="tape-row ' + dir + (whaleIds.has(t.taker) ? " whale" : "") + '"><span>' + t.id + '</span><span class="px">' + CM.inr(t.price) + "</span><span>" + CM.int(t.qty) + "</span><span>" + time + "</span></div>";
      }).join("");
    }

    // my orders
    $("mine").innerHTML = s.mine.length ? s.mine.map((o) =>
      '<div class="item"><span>#' + o.id + " " + o.side + " " + CM.int(o.qty) + " @ " + CM.inr(o.price) + '</span><button data-id="' + o.id + '">Cancel</button></div>').join("")
      : '<div class="empty">None. Click a price in the book to copy it into the form.</div>';

    // system
    $("sRing").textContent = CM.int(m.ring.api + m.ring.sim) + " / " + CM.int(m.ring.capacity);
    $("sDrop").textContent = CM.int(m.sim.dropped);
    $("sRej").textContent = CM.int(m.rejected);
    $("sLedger").textContent = CM.int(m.ledger.written);
  }

  CM.connect(render, (ok) => { $("conn").className = "dot " + (ok ? "on" : "off"); $("connText").textContent = ok ? "Live" : "Reconnecting"; });
  window.addEventListener("resize", () => last && render(last));
  syncButton();
})();
