# cython: language_level=3
"""Cython limit order book on C structs and raw pointers (no Python objects in the match loop).

Design (classic array-based book):
  * one slot per price tick on each side: head/tail order index + total quantity
  * orders live in a preallocated pool of C structs linked into per-level FIFO lists
  * an open-addressing hash table maps order_id -> pool index for O(1) cancel
  * best bid / best ask are tracked as tick indices and re-scanned when a level empties
Same API and semantics as book_py.PyBook; tests/test_book.py checks both against each other.
"""
from libc.stdlib cimport malloc, calloc, free, realloc


cdef struct Order:
    long long id
    long long qty
    int price
    int next
    int prev
    int side


cdef struct Fill:
    long long taker_id
    long long maker_id
    long long price
    long long qty
    int taker_side


cdef unsigned long long _GOLD = 0x9E3779B97F4A7C15


cdef inline unsigned int _hash(long long key, unsigned int mask):
    cdef unsigned long long h = (<unsigned long long>key) * _GOLD
    return (<unsigned int>(h >> 32)) & mask


cdef class CBook:
    cdef int max_ticks
    cdef int pool_size
    cdef Order* pool
    cdef int free_head
    cdef int count
    cdef int* bid_head
    cdef int* bid_tail
    cdef long long* bid_qty
    cdef int* ask_head
    cdef int* ask_tail
    cdef long long* ask_qty
    cdef int best_bid_t
    cdef int best_ask_t
    cdef long long* hkeys
    cdef int* hvals
    cdef unsigned int hmask
    cdef Fill* fills
    cdef int fills_n
    cdef int fills_cap
    cdef readonly int last_levels_cleared

    def __cinit__(self, int max_ticks=200000, int pool_size=262144):
        cdef int i
        cdef unsigned int hsize = 1
        self.max_ticks = max_ticks
        self.pool_size = pool_size
        self.count = 0
        self.last_levels_cleared = 0
        self.pool = <Order*>malloc(pool_size * sizeof(Order))
        self.bid_head = <int*>malloc(max_ticks * sizeof(int))
        self.bid_tail = <int*>malloc(max_ticks * sizeof(int))
        self.ask_head = <int*>malloc(max_ticks * sizeof(int))
        self.ask_tail = <int*>malloc(max_ticks * sizeof(int))
        self.bid_qty = <long long*>calloc(max_ticks, sizeof(long long))
        self.ask_qty = <long long*>calloc(max_ticks, sizeof(long long))
        while hsize < <unsigned int>(2 * pool_size):
            hsize <<= 1
        self.hmask = hsize - 1
        self.hkeys = <long long*>calloc(hsize, sizeof(long long))
        self.hvals = <int*>calloc(hsize, sizeof(int))
        self.fills_cap = 4096
        self.fills_n = 0
        self.fills = <Fill*>malloc(self.fills_cap * sizeof(Fill))
        if (self.pool == NULL or self.bid_head == NULL or self.bid_tail == NULL
                or self.ask_head == NULL or self.ask_tail == NULL or self.bid_qty == NULL
                or self.ask_qty == NULL or self.hkeys == NULL or self.hvals == NULL
                or self.fills == NULL):
            raise MemoryError()
        for i in range(max_ticks):
            self.bid_head[i] = -1
            self.bid_tail[i] = -1
            self.ask_head[i] = -1
            self.ask_tail[i] = -1
        for i in range(pool_size):
            self.pool[i].next = i + 1
        self.pool[pool_size - 1].next = -1
        self.free_head = 0
        self.best_bid_t = 0
        self.best_ask_t = max_ticks

    def __dealloc__(self):
        free(self.pool)
        free(self.bid_head)
        free(self.bid_tail)
        free(self.ask_head)
        free(self.ask_tail)
        free(self.bid_qty)
        free(self.ask_qty)
        free(self.hkeys)
        free(self.hvals)
        free(self.fills)

    # ---- hash table (order id -> pool index) --------------------------------
    cdef int _hfind(self, long long key):
        cdef unsigned int i = _hash(key, self.hmask)
        while self.hkeys[i] != 0:
            if self.hkeys[i] == key:
                return <int>i
            i = (i + 1) & self.hmask
        return -1

    cdef void _hput(self, long long key, int val):
        cdef unsigned int i = _hash(key, self.hmask)
        while self.hkeys[i] != 0:
            i = (i + 1) & self.hmask
        self.hkeys[i] = key
        self.hvals[i] = val

    cdef void _hdel(self, int slot):
        # backward-shift deletion keeps probe chains intact without tombstones
        cdef unsigned int mask = self.hmask
        cdef unsigned int i = <unsigned int>slot
        cdef unsigned int j = i
        cdef unsigned int k
        while True:
            j = (j + 1) & mask
            if self.hkeys[j] == 0:
                break
            k = _hash(self.hkeys[j], mask)
            if i <= j:
                if i < k and k <= j:
                    continue
            else:
                if i < k or k <= j:
                    continue
            self.hkeys[i] = self.hkeys[j]
            self.hvals[i] = self.hvals[j]
            i = j
        self.hkeys[i] = 0

    # ---- pool / level lists ---------------------------------------------------
    cdef void _unlink(self, int idx, int* head, int* tail):
        cdef Order* o = &self.pool[idx]
        if o.prev != -1:
            self.pool[o.prev].next = o.next
        else:
            head[o.price] = o.next
        if o.next != -1:
            self.pool[o.next].prev = o.prev
        else:
            tail[o.price] = o.prev

    cdef void _append(self, int idx, int* head, int* tail):
        cdef Order* o = &self.pool[idx]
        cdef int t = tail[o.price]
        o.next = -1
        o.prev = t
        if t != -1:
            self.pool[t].next = idx
        else:
            head[o.price] = idx
        tail[o.price] = idx

    cdef void _free(self, int idx):
        self.pool[idx].next = self.free_head
        self.free_head = idx
        self.count -= 1

    cdef void _fix_bid(self):
        cdef int p = self.best_bid_t
        while p > 0 and self.bid_head[p] == -1:
            p -= 1
        self.best_bid_t = p

    cdef void _fix_ask(self):
        cdef int p = self.best_ask_t
        while p < self.max_ticks and self.ask_head[p] == -1:
            p += 1
        self.best_ask_t = p

    cdef int _record(self, long long taker, long long maker, int taker_side,
                     long long price, long long qty) except -1:
        cdef Fill* tmp
        cdef Fill* f
        if self.fills_n == self.fills_cap:
            tmp = <Fill*>realloc(self.fills, 2 * self.fills_cap * sizeof(Fill))
            if tmp == NULL:
                raise MemoryError()
            self.fills = tmp
            self.fills_cap *= 2
        f = &self.fills[self.fills_n]
        f.taker_id = taker
        f.maker_id = maker
        f.taker_side = taker_side
        f.price = price
        f.qty = qty
        self.fills_n += 1
        return 0

    # ---- public API -------------------------------------------------------------
    def submit(self, long long order_id, int side, long long price, long long qty):
        cdef int p, idx
        cdef int nfills = 0
        cdef int cleared = 0
        cdef long long f
        cdef Order* o

        if (order_id <= 0 or price <= 0 or price >= self.max_ticks or qty <= 0
                or (side != 0 and side != 1)):
            return -2
        if self._hfind(order_id) != -1:
            return -1
        if self.free_head == -1:
            return -3

        if side == 0:
            while qty > 0 and self.best_ask_t < self.max_ticks and self.best_ask_t <= price:
                p = self.best_ask_t
                while qty > 0 and self.ask_head[p] != -1:
                    idx = self.ask_head[p]
                    o = &self.pool[idx]
                    f = qty if qty < o.qty else o.qty
                    self._record(order_id, o.id, 0, p, f)
                    nfills += 1
                    qty -= f
                    o.qty -= f
                    self.ask_qty[p] -= f
                    if o.qty == 0:
                        self._hdel(self._hfind(o.id))
                        self._unlink(idx, self.ask_head, self.ask_tail)
                        self._free(idx)
                if self.ask_head[p] == -1:
                    cleared += 1
                    self._fix_ask()
        else:
            while qty > 0 and self.best_bid_t > 0 and self.best_bid_t >= price:
                p = self.best_bid_t
                while qty > 0 and self.bid_head[p] != -1:
                    idx = self.bid_head[p]
                    o = &self.pool[idx]
                    f = qty if qty < o.qty else o.qty
                    self._record(order_id, o.id, 1, p, f)
                    nfills += 1
                    qty -= f
                    o.qty -= f
                    self.bid_qty[p] -= f
                    if o.qty == 0:
                        self._hdel(self._hfind(o.id))
                        self._unlink(idx, self.bid_head, self.bid_tail)
                        self._free(idx)
                if self.bid_head[p] == -1:
                    cleared += 1
                    self._fix_bid()
        self.last_levels_cleared = cleared

        if qty > 0:
            idx = self.free_head
            o = &self.pool[idx]
            self.free_head = o.next
            self.count += 1
            o.id = order_id
            o.qty = qty
            o.price = <int>price
            o.side = side
            self._hput(order_id, idx)
            if side == 0:
                self._append(idx, self.bid_head, self.bid_tail)
                self.bid_qty[price] += qty
                if price > self.best_bid_t:
                    self.best_bid_t = <int>price
            else:
                self._append(idx, self.ask_head, self.ask_tail)
                self.ask_qty[price] += qty
                if price < self.best_ask_t:
                    self.best_ask_t = <int>price
        return nfills

    def cancel(self, long long order_id):
        cdef int slot = self._hfind(order_id)
        cdef int idx, p
        cdef Order* o
        if slot == -1:
            return False
        idx = self.hvals[slot]
        o = &self.pool[idx]
        p = o.price
        self._hdel(slot)
        if o.side == 0:
            self._unlink(idx, self.bid_head, self.bid_tail)
            self.bid_qty[p] -= o.qty
            if self.bid_head[p] == -1 and p == self.best_bid_t:
                self._fix_bid()
        else:
            self._unlink(idx, self.ask_head, self.ask_tail)
            self.ask_qty[p] -= o.qty
            if self.ask_head[p] == -1 and p == self.best_ask_t:
                self._fix_ask()
        self._free(idx)
        return True

    def lookup(self, long long order_id):
        cdef int slot = self._hfind(order_id)
        cdef Order* o
        if slot == -1:
            return None
        o = &self.pool[self.hvals[slot]]
        return (o.side, o.price, o.qty)

    def depth(self, int n=10):
        cdef int p, found
        bids = []
        asks = []
        p = self.best_bid_t
        found = 0
        while p > 0 and found < n:
            if self.bid_head[p] != -1:
                bids.append((p, self.bid_qty[p]))
                found += 1
            p -= 1
        p = self.best_ask_t
        found = 0
        while p < self.max_ticks and found < n:
            if self.ask_head[p] != -1:
                asks.append((p, self.ask_qty[p]))
                found += 1
            p += 1
        return bids, asks

    def drain_fills(self):
        cdef int i
        cdef Fill* f
        out = []
        for i in range(self.fills_n):
            f = &self.fills[i]
            out.append((f.taker_id, f.maker_id, f.taker_side, f.price, f.qty))
        self.fills_n = 0
        return out

    @property
    def best_bid(self):
        return self.best_bid_t

    @property
    def best_ask(self):
        return 0 if self.best_ask_t >= self.max_ticks else self.best_ask_t

    @property
    def resting(self):
        return self.count
