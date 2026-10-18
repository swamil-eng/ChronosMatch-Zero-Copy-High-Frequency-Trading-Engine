"""Shared constants. Prices travel through the system as integer ticks (1 tick = Rs 0.01)."""

TICK = 0.01                 # rupees per tick
MAX_TICKS = 200_000         # price range the book supports: Rs 0.01 .. Rs 1999.99
SIM_ID_BASE = 1 << 40       # simulator order ids start here; API ids stay below it

SIDE_BUY, SIDE_SELL = 0, 1
ACT_NEW, ACT_CANCEL = 0, 1

# submit() status codes (>= 0 means "accepted, N fills")
REJECT_DUPLICATE = -1
REJECT_INVALID = -2
REJECT_POOL_FULL = -3
