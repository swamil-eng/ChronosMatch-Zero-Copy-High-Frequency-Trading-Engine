"""Fixed-size log-bucket latency histogram (about 12% resolution, O(1) record)."""


class LatencyHistogram:
    SIZE = 64 * 8

    def __init__(self):
        self.reset()

    def reset(self):
        self.counts = [0] * self.SIZE
        self.n = 0
        self.total = 0
        self.max = 0

    def add(self, ns):
        if ns < 0:
            ns = 0
        bl = ns.bit_length()
        idx = ns if bl <= 4 else ((bl - 3) << 3) | ((ns >> (bl - 4)) & 7)
        if idx >= self.SIZE:
            idx = self.SIZE - 1
        self.counts[idx] += 1
        self.n += 1
        self.total += ns
        if ns > self.max:
            self.max = ns

    @staticmethod
    def _lower(idx):
        if idx < 16:
            return idx
        g, m = idx >> 3, idx & 7
        return (8 + m) << (g + 3 - 4)

    def percentile(self, q):
        """Lower bound of the bucket holding the q-th percentile, in ns."""
        if not self.n:
            return 0
        target = self.n * q / 100.0
        seen = 0
        for idx, c in enumerate(self.counts):
            seen += c
            if c and seen >= target:
                return self._lower(idx)
        return self.max

    def summary_us(self):
        if not self.n:
            return {"p50": 0, "p95": 0, "p99": 0, "max": 0, "avg": 0, "n": 0}
        return {"p50": self.percentile(50) / 1000, "p95": self.percentile(95) / 1000,
                "p99": self.percentile(99) / 1000, "max": self.max / 1000,
                "avg": self.total / self.n / 1000, "n": self.n}
