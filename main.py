from chronosmatch.engine import MatchingEngine
from chronosmatch.models import Order

def main():
    engine = MatchingEngine()

    engine.submit_order(Order("B1", "BUY", 100, 10))
    engine.submit_order(Order("S1", "SELL", 99, 4))
    engine.submit_order(Order("S2", "SELL", 100, 6))

    print("TRADE RECORDS")
    for trade in engine.trades:
        print(
            f"Trade {trade.trade_id}: BUY={trade.buy_order_id}, "
            f"SELL={trade.sell_order_id}, price=Rs.{trade.price}, "
            f"quantity={trade.quantity}"
        )

    print("\nFINAL ORDER BOOK")
    print(engine.order_book_snapshot())

if __name__ == "__main__":
    main()
