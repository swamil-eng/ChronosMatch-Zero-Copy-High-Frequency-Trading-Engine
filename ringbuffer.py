"""Zero-copy IPC: a single-producer / single-consumer ring buffer on a memory-mapped file.

Two processes map the same file. The producer writes fixed 32-byte records straight into
the shared pages with struct.pack_into and the consumer reads them with struct.unpack_from.
There is no pickling and no JSON, and no intermediate bytes object is built for the payload.

Record layout (little endian, 32 bytes):
    order_id u64 | side u8 | action u8 | reserved u16 | quantity u32 | price i64 (ticks) | ts_ns u64

Header (256 bytes): consumer head index at 0, producer tail index at 64 (separate cache
lines), capacity at 128, magic at 136. Indices only ever grow; slot = index & (capacity-1).

Indices are native-endian machine words, so ring files are not portable between machines
of different endianness (they never need to be: they live in RAM for one run).

Safety rule: exactly ONE producer and ONE consumer per ring. Callers that have several
producer threads must serialise push() themselves (EngineRuntime does).
"""
import mmap
import os
import struct
from time import perf_counter_ns

REC = struct.Struct("<QBBHIqQ")
REC_SIZE = REC.size          # 32
HDR = 256
OFF_HEAD, OFF_TAIL, OFF_CAP, OFF_MAGIC = 0, 64, 128, 136
MAGIC = 0x4352_4E4D_4154_4348
# Native mode ("Q", not "<Q"): CPython then stores/loads the index with ONE aligned 8-byte
# memcpy. The "<Q" form writes it byte by byte, so a reader in another process could observe a
# half-updated index (0x0100 -> 0x0000 while 0x01FF becomes 0x0200).
U64 = struct.Struct("Q")


class RingBuffer:
    def __init__(self, mm, fileobj, path, capacity):
        self._mm = mm
        self._f = fileobj
        self.path = path
        self.capacity = capacity
        self._mask = capacity - 1
        self._tail = U64.unpack_from(mm, OFF_TAIL)[0]   # producer-local copy
        self._head = U64.unpack_from(mm, OFF_HEAD)[0]   # consumer-local copy
        self._head_seen = self._head                    # producer's cached view of head

    @classmethod
    def create(cls, path, capacity=1 << 16):
        if capacity < 2 or capacity & (capacity - 1):
            raise ValueError("capacity must be a power of two")
        size = HDR + capacity * REC_SIZE
        f = open(path, "w+b")
        f.truncate(size)                                 # zero filled
        mm = mmap.mmap(f.fileno(), size)
        U64.pack_into(mm, OFF_CAP, capacity)
        U64.pack_into(mm, OFF_MAGIC, MAGIC)
        return cls(mm, f, path, capacity)

    @classmethod
    def attach(cls, path):
        f = open(path, "r+b")
        mm = mmap.mmap(f.fileno(), 0)
        if U64.unpack_from(mm, OFF_MAGIC)[0] != MAGIC:
            raise ValueError("not a ChronosMatch ring buffer: %s" % path)
        return cls(mm, f, path, U64.unpack_from(mm, OFF_CAP)[0])

    # ---- producer side -------------------------------------------------
    def push(self, order_id, side, action, qty, price, ts_ns=None):
        """Write one record. Returns False if the ring is full (caller decides what to do)."""
        tail = self._tail
        if tail - self._head_seen >= self.capacity:
            self._head_seen = U64.unpack_from(self._mm, OFF_HEAD)[0]
            if tail - self._head_seen >= self.capacity:
                return False
        if ts_ns is None:
            ts_ns = perf_counter_ns()
        REC.pack_into(self._mm, HDR + (tail & self._mask) * REC_SIZE,
                      order_id, side, action, 0, qty, price, ts_ns)
        self._tail = tail + 1
        U64.pack_into(self._mm, OFF_TAIL, tail + 1)      # publish after the record is written
        return True

    # ---- consumer side -------------------------------------------------
    def pop_batch(self, max_n=512):
        """Read up to max_n records as tuples straight out of the mapped pages."""
        mm = self._mm
        head = self._head
        n = U64.unpack_from(mm, OFF_TAIL)[0] - head
        if n <= 0:
            return ()
        if n > max_n:
            n = max_n
        mask, unpack = self._mask, REC.unpack_from
        out = [unpack(mm, HDR + ((head + i) & mask) * REC_SIZE) for i in range(n)]
        self._head = head + n
        U64.pack_into(mm, OFF_HEAD, head + n)
        return out

    def discard_pending(self):
        """Consumer-side: drop everything currently queued."""
        self._head = U64.unpack_from(self._mm, OFF_TAIL)[0]
        U64.pack_into(self._mm, OFF_HEAD, self._head)

    def __len__(self):
        return max(0, U64.unpack_from(self._mm, OFF_TAIL)[0] - U64.unpack_from(self._mm, OFF_HEAD)[0])

    def close(self, unlink=False):
        try:
            self._mm.close()
        finally:
            self._f.close()
        if unlink:
            try:
                os.remove(self.path)
            except OSError:
                pass   # Windows refuses while another process still maps the file
