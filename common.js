/* Shared helpers for the website and the dashboard. */
const CM = (() => {
  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const inr = (n) => (n == null ? "–" : n.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 }));
  const int = (n) => (n == null ? "–" : Math.round(n).toLocaleString("en-IN"));
  const us = (n) => (n == null ? "–" : n >= 1000 ? (n / 1000).toFixed(n >= 10000 ? 0 : 1) + " ms" : n >= 100 ? n.toFixed(0) + " µs" : n.toFixed(n >= 10 ? 1 : 2) + " µs");

  async function api(path, method = "GET", body) {
    const res = await fetch(path, { method, headers: body ? { "Content-Type": "application/json" } : {}, body: body ? JSON.stringify(body) : undefined });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || "Request failed (" + res.status + ")");
    return data;
  }

  /* Live feed over Server-Sent Events, reconnecting automatically. */
  function connect(onSnap, onState) {
    let es;
    const open = () => {
      es = new EventSource("/api/stream");
      es.onopen = () => onState && onState(true);
      es.onmessage = (e) => { const s = JSON.parse(e.data); if (s.ready) onSnap(s); };
      es.onerror = () => { onState && onState(false); es.close(); setTimeout(open, 1500); };
    };
    open();
  }

  /* Latency trace on a log scale. series: [{values:[µs], color}], target: µs line. */
  function trace(canvas, series, target) {
    const dpr = window.devicePixelRatio || 1, w = canvas.clientWidth, h = canvas.clientHeight;
    if (!w || !h) return;
    if (canvas.width !== Math.round(w * dpr)) { canvas.width = Math.round(w * dpr); canvas.height = Math.round(h * dpr); }
    const g = canvas.getContext("2d");
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, w, h);
    const all = series.flatMap((s) => s.values).filter((v) => v > 0);
    const padL = 52, padR = 8, padT = 6, padB = 6;
    let lo = 1, hi = 100;
    if (all.length) { lo = Math.min(...all, target); hi = Math.max(...all, target); }
    lo = Math.pow(10, Math.floor(Math.log10(lo))); hi = Math.pow(10, Math.ceil(Math.log10(hi)));
    if (hi <= lo) hi = lo * 10;
    const y = (v) => padT + (1 - (Math.log10(Math.max(v, lo)) - Math.log10(lo)) / (Math.log10(hi) - Math.log10(lo))) * (h - padT - padB);
    g.font = "11px " + css("--mono"); g.textBaseline = "middle"; g.lineWidth = 1;
    for (let d = Math.log10(lo); d <= Math.log10(hi) + 1e-9; d++) {
      const v = Math.pow(10, d), yy = y(v);
      g.strokeStyle = css("--rule"); g.beginPath(); g.moveTo(padL, yy); g.lineTo(w - padR, yy); g.stroke();
      g.fillStyle = css("--muted"); g.fillText(v >= 1000 ? v / 1000 + " ms" : v + " µs", 2, yy);
    }
    if (target) {
      g.setLineDash([5, 4]); g.strokeStyle = css("--whale"); g.beginPath(); g.moveTo(padL, y(target)); g.lineTo(w - padR, y(target)); g.stroke(); g.setLineDash([]);
      g.fillStyle = css("--whale"); g.textAlign = "right"; g.fillText(target + " µs target", w - padR - 2, y(target) - 7); g.textAlign = "left";
    }
    series.forEach((s) => {
      if (s.values.length < 2) return;
      g.strokeStyle = s.color; g.lineWidth = 1.6; g.beginPath();
      s.values.forEach((v, i) => { const x = padL + (i / (s.values.length - 1)) * (w - padL - padR); i ? g.lineTo(x, y(v)) : g.moveTo(x, y(v)); });
      g.stroke();
    });
  }

  /* Order-book rows from best-first levels. Asks are shown reversed so the spread sits between the sides. */
  function ladderRows(levels, side, mineKeys, max) {
    if (!levels.length) return '<div class="empty">No ' + (side === "ask" ? "sell" : "buy") + " orders resting.</div>";
    let cum = 0;
    const rows = levels.map(([p, q]) => {
      cum += q;
      const pct = Math.max(3, Math.round((q / max) * 100));
      return '<div class="row ' + side + (mineKeys.has(p) ? " mine" : "") + '" data-price="' + p.toFixed(2) + '" style="background-size:' + pct + '% 100%">' +
        '<span class="px">' + inr(p) + "</span><span>" + int(q) + "</span><span>" + int(cum) + "</span></div>";
    });
    return (side === "ask" ? rows.reverse() : rows).join("");
  }
  return { css, inr, int, us, api, connect, trace, ladderRows };
})();
