import random
import unittest

from chronosmatch import core
from chronosmatch.book_py import PyBook
from chronosmatch.engine import MatchingEngine
from chronosmatch.models import Order

BOOKS = [PyBook]
try:
    from chronosmatch.book_cy import CBook
    BOOKS.append(CBook)
except ImportError:
    CBook = None


def fills(b):
    return b.drain_fills()


class BookTests(unittest.TestCase):
    def each(self):
        for cls in BOOKS:
            with self.subTest(book=cls.__name__):
                yield cls()

    def test_readme_example(self):
        for b in self.each():
            b.submit(1, 0, 100, 10)
            b.submit(2, 1, 99, 4)
            b.submit(3, 1, 100, 6)
            self.assertEqual(fills(b), [(2, 1, 1, 100, 4), (3, 1, 1, 100, 6)])
            self.assertEqual(b.depth(5), ([], []))
            self.assertEqual(b.resting, 0)

    def test_price_time_priority(self):
        for b in self.each():
            b.submit(1, 1, 101, 5)
            b.submit(2, 1, 100, 5)
            b.submit(3, 1, 100, 5)           # later than 2 at the same price
            b.submit(4, 0, 101, 8)
            self.assertEqual([(f[1], f[3], f[4]) for f in fills(b)],
                             [(2, 100, 5), (3, 100, 3)])
            self.assertEqual(b.lookup(3), (1, 100, 2))
            self.assertEqual(b.lookup(1), (1, 101, 5))

    def test_partial_fill_rests_remainder(self):
        for b in self.each():
            b.submit(1, 1, 100, 3)
            self.assertEqual(b.submit(2, 0, 100, 10), 1)
            self.assertEqual(b.lookup(2), (0, 100, 7))
            self.assertEqual((b.best_bid, b.best_ask), (100, 0))

    def test_cancel(self):
        for b in self.each():
            b.submit(1, 0, 100, 5)
            b.submit(2, 0, 100, 5)
            b.submit(3, 0, 99, 5)
            self.assertTrue(b.cancel(1))
            self.assertFalse(b.cancel(1))
            self.assertTrue(b.cancel(2))
            self.assertEqual(b.best_bid, 99)
            self.assertEqual(b.submit(4, 1, 99, 5), 1)
            self.assertEqual(fills(b), [(4, 3, 1, 99, 5)])
            self.assertEqual(b.best_bid, 0)

    def test_rejects(self):
        for b in self.each():
            self.assertEqual(b.submit(0, 0, 100, 1), -2)
            self.assertEqual(b.submit(1, 2, 100, 1), -2)
            self.assertEqual(b.submit(1, 0, 0, 1), -2)
            self.assertEqual(b.submit(1, 0, 100, 0), -2)
            self.assertEqual(b.submit(1, 0, b.max_ticks if hasattr(b, "max_ticks") else 200_000, 1), -2)
            self.assertEqual(b.submit(1, 0, 100, 1), 0)
            self.assertEqual(b.submit(1, 0, 100, 1), -1)

    def test_pool_full(self):
        for cls in BOOKS:
            b = cls(max_ticks=1000, pool_size=4)
            for i in range(1, 5):
                self.assertEqual(b.submit(i, 0, 100 + i, 1), 0)
            self.assertEqual(b.submit(5, 0, 90, 1), -3)
            b.cancel(1)
            self.assertEqual(b.submit(5, 0, 90, 1), 0)

    def test_whale_clears_many_levels(self):
        for b in self.each():
            for i, p in enumerate((100, 101, 102, 103), start=1):
                b.submit(i, 1, p, 10)
            b.submit(9, 0, 110, 35)
            self.assertEqual(b.last_levels_cleared, 3)
            self.assertEqual(b.lookup(4), (1, 103, 5))
            b.submit(10, 0, 50, 1)
            self.assertEqual(b.last_levels_cleared, 0)

    def test_fuzz_against_reference_engine(self):
        for cls in BOOKS:
            rng = random.Random(7)
            b, ref = cls(max_ticks=2000, pool_size=5000), MatchingEngine()
            live = []
            for oid in range(1, 4001):
                if live and rng.random() < 0.2:
                    victim = live.pop(rng.randrange(len(live)))
                    expected = any(o.order_id == str(victim) for o in ref.bids + ref.asks)
                    self.assertEqual(b.cancel(victim), expected)
                    ref.bids = [o for o in ref.bids if o.order_id != str(victim)]
                    ref.asks = [o for o in ref.asks if o.order_id != str(victim)]
                    continue
                side = rng.randrange(2)
                price = 1000 + rng.randint(-15, 15)
                qty = rng.choice((1, 2, 5, 10, 50, 300)) if rng.random() < 0.97 else 2000
                b.submit(oid, side, price, qty)
                ref.submit_order(Order(str(oid), "BUY" if side == 0 else "SELL", price, qty))
                live.append(oid)
            got = [(f[0] if f[2] == 0 else f[1], f[1] if f[2] == 0 else f[0], f[3], f[4])
                   for f in b.drain_fills()]
            want = [(int(t.buy_order_id), int(t.sell_order_id), t.price, t.quantity) for t in ref.trades]
            self.assertEqual(got, want, cls.__name__)
            snap = ref.order_book_snapshot()
            agg = lambda rows: sorted({p: sum(q for _, pp, q in rows if pp == p) for _, p, _ in rows}.items())
            bids, asks = b.depth(100)
            self.assertEqual(sorted(bids), agg(snap["bids"]))
            self.assertEqual(sorted(asks), agg(snap["asks"]))
            self.assertEqual(b.resting, len(snap["bids"]) + len(snap["asks"]))

    @unittest.skipUnless(CBook, "Cython extension not built")
    def test_cython_matches_python_exactly(self):
        rng = random.Random(11)
        a, c = PyBook(max_ticks=5000, pool_size=20_000), CBook(max_ticks=5000, pool_size=20_000)
        for oid in range(1, 30_001):
            if rng.random() < 0.15:
                v = rng.randint(1, oid)
                self.assertEqual(a.cancel(v), c.cancel(v))
            else:
                args = (oid, rng.randrange(2), 2500 + rng.randint(-40, 40), rng.randint(1, 400))
                self.assertEqual(a.submit(*args), c.submit(*args))
                self.assertEqual(a.last_levels_cleared, c.last_levels_cleared)
            if oid % 500 == 0:
                self.assertEqual(a.drain_fills(), c.drain_fills())
                self.assertEqual(a.depth(50), c.depth(50))
                self.assertEqual((a.best_bid, a.best_ask, a.resting), (c.best_bid, c.best_ask, c.resting))


if __name__ == "__main__":
    unittest.main()
