"""ChronosMatch web backend (Flask).  Run:  python app.py   then open http://127.0.0.1:8000"""
import json
import os
import time

from flask import Flask, Response, jsonify, request, send_from_directory

from chronosmatch.constants import MAX_TICKS, SIDE_BUY, SIDE_SELL, TICK
from chronosmatch.runtime import EngineRuntime

HERE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(HERE, "web")
MAX_RATE = 150_000


def create_app(runtime=None):
    app = Flask(__name__, static_folder=os.path.join(WEB, "static"), static_url_path="/static")
    rt = runtime or EngineRuntime(ledger_path=os.path.join(HERE, "chronosmatch_ledger.db")).start()
    app.config["RT"] = rt

    def err(msg, code=400):
        return jsonify({"error": msg}), code

    @app.get("/")
    def home():
        return send_from_directory(WEB, "index.html")

    @app.get("/dashboard")
    def dashboard():
        return send_from_directory(WEB, "dashboard.html")

    @app.get("/api/health")
    def health():
        snap = rt.snapshot()
        return jsonify({"ok": True, "backend": rt.backend, "ready": snap.get("ready", False)})

    @app.get("/api/snapshot")
    def snapshot():
        return jsonify(rt.snapshot())

    @app.get("/api/book")
    def book():
        snap = rt.snapshot()
        depth = max(1, min(request.args.get("depth", 15, type=int), 15))
        b = dict(snap.get("book", {}))
        b["bids"], b["asks"] = b.get("bids", [])[:depth], b.get("asks", [])[:depth]
        return jsonify(b)

    @app.get("/api/metrics")
    def metrics():
        return jsonify(rt.snapshot().get("metrics", {}))

    @app.get("/api/trades")
    def trades():
        return jsonify(rt.snapshot().get("trades", [])[: request.args.get("limit", 40, type=int)])

    @app.get("/api/ledger")
    def ledger():
        return jsonify(rt.recent_ledger(max(1, min(request.args.get("limit", 50, type=int), 500))))

    @app.post("/api/orders")
    def new_order():
        body = request.get_json(silent=True) or {}
        side = str(body.get("side", "")).upper()
        if side not in ("BUY", "SELL"):
            return err("side must be BUY or SELL")
        try:
            price = float(body.get("price"))
            qty = int(body.get("qty"))
        except (TypeError, ValueError):
            return err("price must be a number and qty a whole number")
        ticks = round(price / TICK)
        if not 1 <= ticks < MAX_TICKS:
            return err("price must be between 0.01 and %.2f" % ((MAX_TICKS - 1) * TICK))
        if not 1 <= qty <= 1_000_000:
            return err("qty must be between 1 and 1,000,000")
        oid = rt.submit_order(SIDE_BUY if side == "BUY" else SIDE_SELL, ticks, qty)
        if oid is None:
            return err("ingest ring is full; retry shortly", 503)
        return jsonify({"order_id": oid, "status": "queued"}), 202

    @app.delete("/api/orders/<int:order_id>")
    def cancel(order_id):
        if not rt.cancel_order(order_id):
            return err("ingest ring is full; retry shortly", 503)
        return jsonify({"order_id": order_id, "status": "cancel queued"}), 202

    @app.get("/api/simulator")
    def sim_state():
        return jsonify(rt.snapshot().get("metrics", {}).get("sim", {}))

    @app.post("/api/simulator")
    def sim_control():
        body = request.get_json(silent=True) or {}
        action = body.get("action")
        if action == "start":
            try:
                rate = float(body.get("rate", 10_000))
            except (TypeError, ValueError):
                return err("rate must be a number")
            if not 1 <= rate <= MAX_RATE:
                return err("rate must be between 1 and %d orders/s" % MAX_RATE)
            rt.start_simulator(rate)
        elif action == "stop":
            rt.stop_simulator()
        else:
            return err("action must be start or stop")
        return jsonify({"running": rt.simulator_running, "rate": rt._rate.value})

    @app.post("/api/engine")
    def engine_options():
        body = request.get_json(silent=True) or {}
        if "busy_poll" not in body or not isinstance(body["busy_poll"], bool):
            return err("busy_poll must be true or false")
        rt.busy_poll = body["busy_poll"]
        return jsonify({"busy_poll": rt.busy_poll})

    @app.post("/api/reset")
    def reset():
        rt.reset()
        return jsonify({"status": "reset"})

    @app.get("/api/stream")
    def stream():
        def events():
            while True:
                yield "data: %s\n\n" % json.dumps(rt.snapshot())
                time.sleep(0.1)
        return Response(events(), mimetype="text/event-stream",
                        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    return app


if __name__ == "__main__":      # guard is required: the simulator is started with "spawn"
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()
    from chronosmatch import core
    print("order book backend: %s" % core.BACKEND)
    print("open http://%s:%d" % (args.host, args.port))
    create_app().run(host=args.host, port=args.port, threaded=True, use_reloader=False)
