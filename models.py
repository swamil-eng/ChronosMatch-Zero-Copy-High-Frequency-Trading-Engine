from dataclasses import dataclass

@dataclass
class Order:
    order_id: str
    side: str          # "BUY" or "SELL"
    price: float
    quantity: int
    sequence: int = 0

@dataclass
class Trade:
    trade_id: int
    buy_order_id: str
    sell_order_id: str
    price: float
    quantity: int
