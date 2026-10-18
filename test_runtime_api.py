import os
import tempfile
import time
import unittest

from app import create_app
from chronosmatch.runtime import EngineRuntime


def wait_for(cond, timeout=8.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.rt = EngineRuntime(ledger_path=os.path.join(cls.tmp, "ledger.db")).start()
        cls.app = create_app(cls.rt)
        cls.c = cls.app.test_client()

    @classmethod
    def tearDownClass(cls):
        cls.rt.close()

    def setUp(self):
        self.c.post("/api/reset")

    def post(self, side, price, qty):
        return self.c.post("/api/orders", json={"side": side, "price": price, "qty": qty})

    def book(self):
        return self.c.get("/api/book").get_json()

    def test_pages_served(self):
        self.assertEqual(self.c.get("/").status_code, 200)
        self.assertEqual(self.c.get("/dashboard").status_code, 200)
        self.assertTrue(self.c.get("/api/health").get_json()["ok"])

    def test_busy_poll_toggle_and_reset_clears_trades(self):
        self.assertEqual(self.c.post("/api/engine", json={"busy_poll": "yes"}).status_code, 400)
        self.assertTrue(self.c.post("/api/engine", json={"busy_poll": True}).get_json()["busy_poll"])
        self.assertTrue(wait_for(lambda: self.c.get("/api/metrics").get_json().get("busy_poll") is True))
        self.post("BUY", 100.0, 5)
        self.post("SELL", 100.0, 5)
        self.assertTrue(wait_for(lambda: len(self.c.get("/api/trades").get_json()) == 1))
        self.c.post("/api/reset")
        self.assertTrue(wait_for(lambda: self.c.get("/api/trades").get_json() == []))
        self.assertFalse(self.c.post("/api/engine", json={"busy_poll": False}).get_json()["busy_poll"])

    def test_validation(self):
        for body in ({"side": "HOLD", "price": 1, "qty": 1}, {"side": "BUY", "price": "x", "qty": 1},
                     {"side": "BUY", "price": 0, "qty": 1}, {"side": "BUY", "price": 100, "qty": 0},
                     {"side": "BUY", "price": 5000, "qty": 1}):
            self.assertEqual(self.c.post("/api/orders", json=body).status_code, 400, body)

    def test_match_end_to_end_and_ledger(self):
        self.assertEqual(self.post("BUY", 100.00, 10).status_code, 202)
        self.assertEqual(self.post("SELL", 99.50, 4).status_code, 202)
        self.assertEqual(self.post("SELL", 100.00, 6).status_code, 202)
        self.assertTrue(wait_for(lambda: len(self.c.get("/api/trades").get_json()) == 2))
        trades = sorted(self.c.get("/api/trades").get_json(), key=lambda t: t["id"])
        self.assertEqual([(t["price"], t["qty"]) for t in trades], [(100.0, 4), (100.0, 6)])
        self.assertTrue(wait_for(lambda: self.book()["resting"] == 0))
        self.assertTrue(wait_for(lambda: self.c.get("/api/ledger").get_json()["total"] >= 2))

    def test_resting_order_visible_and_cancellable(self):
        oid = self.post("BUY", 98.25, 7).get_json()["order_id"]
        self.assertTrue(wait_for(lambda: self.book()["best_bid"] == 98.25))
        snap = self.c.get("/api/snapshot").get_json()
        self.assertTrue(wait_for(lambda: any(m["id"] == oid for m in self.c.get("/api/snapshot").get_json()["mine"])))
        self.assertEqual(self.c.delete("/api/orders/%d" % oid).status_code, 202)
        self.assertTrue(wait_for(lambda: self.book()["best_bid"] is None))

    def test_simulator_feeds_engine_through_shared_memory(self):
        r = self.c.post("/api/simulator", json={"action": "start", "rate": 5000})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(wait_for(lambda: self.c.get("/api/metrics").get_json().get("processed", 0) > 500, 15))
        m = self.c.get("/api/metrics").get_json()
        self.assertTrue(m["sim"]["running"])
        self.assertEqual(m["gc"]["during_match"], 0)
        self.c.post("/api/simulator", json={"action": "stop"})
        self.assertFalse(self.rt.simulator_running)
        self.assertEqual(self.c.post("/api/simulator", json={"action": "start", "rate": 0}).status_code, 400)


if __name__ == "__main__":
    unittest.main()
