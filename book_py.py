"""Pure-Python limit order book with the same API as the Cython book (book_cy.pyx).

Used automatically when the Cython extension is not built, and as the oracle in the
parity tests. Price-time priority; trades execute at the resting (maker) order's price.
"""
import heapq
from collections import deque

from .constants import (MAX_TICKS, REJECT_DUPLICATE, REJECT_INVALID, REJECT_POOL_FULL)

_ID, _SIDE, _PRICE, _QTY = 0, 1, 2, 3


class PyBook:
    def __init__(self, max_ticks=MAX_TICKS, pool_size=262_144):
        self.max_ticks = max_ticks
        self.pool_size = pool_size
        self._levels = ({}, {})      # side -> price -> deque of [id, side, price, qty]
        self._lqty = ({}, {})        # side -> price -> total resting quantity
        self._heaps = ([], [])       # bids hold -price (max-heap), asks hold price
        self._orders = {}            # id -> [id, side, price, qty]
        self._fills = []
        self.last_levels_cleared = 0

    # -- queries -----------------------------------------------------------
    def _best(self, side):
        heap, lv = self._heaps[side], self._levels[side]
        while heap:
            p = -heap[0] if side == 0 else heap[0]
            if p in lv:
                return p
            heapq.heappop(heap)
        return 0

    @property
    def best_bid(self):
        return self._best(0)

    @property
    def best_ask(self):
        return self._best(1)

    @property
    def resting(self):
        return len(self._orders)

    def lookup(self, order_id):
        m = self._orders.get(order_id)
        return None if m is None else (m[_SIDE], m[_PRICE], m[_QTY])

    def depth(self, n=10):
        return (
            [(p, self._lqty[0][p]) for p in heapq.nlargest(n, self._lqty[0])],
            [(p, self._lqty[1][p]) for p in heapq.nsmallest(n, self._lqty[1])],
        )

    def drain_fills(self):
        """Fills since last call: (taker_id, maker_id, taker_side, price_ticks, qty)."""
        out, self._fills = self._fills, []
        return out

    # -- commands ----------------------------------------------------------
    def submit(self, order_id, side, price, qty):
        if (order_id <= 0 or price <= 0 or price >= self.max_ticks or qty <= 0
                or (side != 0 and side != 1)):
            return REJECT_INVALID
        if order_id in self._orders:
            return REJECT_DUPLICATE
        if len(self._orders) >= self.pool_size:
            return REJECT_POOL_FULL

        opp = 1 - side
        lv, lq, heap = self._levels[opp], self._lqty[opp], self._heaps[opp]
        fills, nfills, cleared = self._fills, 0, 0
        while qty > 0 and heap:
            key = heap[0]
            p = key if opp == 1 else -key
            level = lv.get(p)
            if level is None:                       # stale heap entry
                heapq.heappop(heap)
                continue
            if (p > price) if side == 0 else (p < price):
                break
            while qty > 0 and level:
                m = level[0]
                if m[_QTY] == 0:                    # cancelled earlier
                    level.popleft()
                    continue
                f = qty if qty < m[_QTY] else m[_QTY]
                fills.append((order_id, m[_ID], side, p, f))
                nfills += 1
                qty -= f
                m[_QTY] -= f
                lq[p] -= f
                if m[_QTY] == 0:
                    level.popleft()
                    del self._orders[m[_ID]]
            if lq[p] == 0:                          # level fully consumed
                del lv[p]
                del lq[p]
                heapq.heappop(heap)
                cleared += 1
        self.last_levels_cleared = cleared

        if qty > 0:
            m = [order_id, side, price, qty]
            self._orders[order_id] = m
            lv, lq = self._levels[side], self._lqty[side]
            level = lv.get(price)
            if level is None:
                level = lv[price] = deque()
                lq[price] = 0
                heapq.heappush(self._heaps[side], -price if side == 0 else price)
            level.append(m)
            lq[price] += qty
        return nfills

    def cancel(self, order_id):
        m = self._orders.pop(order_id, None)
        if m is None:
            return False
        side, price = m[_SIDE], m[_PRICE]
        self._lqty[side][price] -= m[_QTY]
        m[_QTY] = 0                                  # lazy removal from the deque
        if self._lqty[side][price] == 0:
            del self._levels[side][price]
            del self._lqty[side][price]
            heap = self._heaps[side]
            if len(heap) > 4 * len(self._levels[side]) + 1024:   # bound stale entries
                heap[:] = [-p if side == 0 else p for p in self._levels[side]]
                heapq.heapify(heap)
        return True
