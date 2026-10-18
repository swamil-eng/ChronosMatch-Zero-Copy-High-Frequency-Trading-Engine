import unittest
from chronosmatch.engine import MatchingEngine
from chronosmatch.models import Order

class MatchingEngineTests(unittest.TestCase):
    def test_example_generates_two_trades(self):
        engine = MatchingEngine()
        engine.submit_order(Order("B1", "BUY", 100, 10))
        engine.submit_order(Order("S1", "SELL", 99, 4))
        engine.submit_order(Order("S2", "SELL", 100, 6))

        self.assertEqual(len(engine.trades), 2)
        self.assertEqual([(t.price, t.quantity) for t in engine.trades],
                         [(100, 4), (100, 6)])
        self.assertEqual(engine.order_book_snapshot(), {"bids": [], "asks": []})

    def test_non_crossing_orders_remain_in_book(self):
        engine = MatchingEngine()
        engine.submit_order(Order("B1", "BUY", 95, 2))
        engine.submit_order(Order("S1", "SELL", 105, 3))
        self.assertEqual(len(engine.trades), 0)
        self.assertEqual(engine.order_book_snapshot()["bids"], [("B1", 95, 2)])
        self.assertEqual(engine.order_book_snapshot()["asks"], [("S1", 105, 3)])

    def test_invalid_price_rejected(self):
        engine = MatchingEngine()
        with self.assertRaises(ValueError):
            engine.submit_order(Order("B1", "BUY", 0, 2))

if __name__ == "__main__":
    unittest.main()
