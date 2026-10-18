from .models import Order, Trade

class MatchingEngine:
    """Educational in-memory matching engine; not for real trading."""

    def __init__(self):
        self.bids = []   # resting BUY orders
        self.asks = []   # resting SELL orders
        self.trades = []
        self._sequence = 0
        self._trade_sequence = 0

    def submit_order(self, order: Order):
        side = order.side.upper()
        if side not in {"BUY", "SELL"}:
            raise ValueError("side must be BUY or SELL")
        if not order.order_id.strip():
            raise ValueError("order_id cannot be empty")
        if order.price <= 0:
            raise ValueError("price must be greater than zero")
        if not isinstance(order.quantity, int) or isinstance(order.quantity, bool) or order.quantity <= 0:
            raise ValueError("quantity must be a positive integer")

        order.side = side
        self._sequence += 1
        order.sequence = self._sequence

        if side == "BUY":
            self._match_buy(order)
            if order.quantity > 0:
                self.bids.append(order)
                self._sort_book()
        else:
            self._match_sell(order)
            if order.quantity > 0:
                self.asks.append(order)
                self._sort_book()

        return [trade for trade in self.trades]

    def _match_buy(self, buy_order):
        self._sort_book()
        while buy_order.quantity > 0 and self.asks and self.asks[0].price <= buy_order.price:
            resting_sell = self.asks[0]
            quantity = min(buy_order.quantity, resting_sell.quantity)
            self._record_trade(buy_order, resting_sell, resting_sell.price, quantity)
            buy_order.quantity -= quantity
            resting_sell.quantity -= quantity
            if resting_sell.quantity == 0:
                self.asks.pop(0)

    def _match_sell(self, sell_order):
        self._sort_book()
        while sell_order.quantity > 0 and self.bids and self.bids[0].price >= sell_order.price:
            resting_buy = self.bids[0]
            quantity = min(sell_order.quantity, resting_buy.quantity)
            self._record_trade(resting_buy, sell_order, resting_buy.price, quantity)
            sell_order.quantity -= quantity
            resting_buy.quantity -= quantity
            if resting_buy.quantity == 0:
                self.bids.pop(0)

    def _record_trade(self, buy_order, sell_order, price, quantity):
        self._trade_sequence += 1
        self.trades.append(Trade(
            trade_id=self._trade_sequence,
            buy_order_id=buy_order.order_id,
            sell_order_id=sell_order.order_id,
            price=price,
            quantity=quantity,
        ))

    def _sort_book(self):
        # Best bid first: highest price, then earliest order.
        self.bids.sort(key=lambda order: (-order.price, order.sequence))
        # Best ask first: lowest price, then earliest order.
        self.asks.sort(key=lambda order: (order.price, order.sequence))

    def order_book_snapshot(self):
        return {
            "bids": [(o.order_id, o.price, o.quantity) for o in self.bids],
            "asks": [(o.order_id, o.price, o.quantity) for o in self.asks],
        }
