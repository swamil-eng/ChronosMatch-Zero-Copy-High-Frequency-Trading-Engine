import multiprocessing as mp
import os
import tempfile
import unittest

from chronosmatch.ringbuffer import RingBuffer


def _producer(path, n):
    ring = RingBuffer.attach(path)
    i = 1
    while i <= n:
        if ring.push(i, i & 1, 0, i % 1000 + 1, 10_000 + i % 50):
            i += 1
    ring.close()


class RingBufferTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "r.ring")

    def tearDown(self):
        try:
            os.remove(self.path)
        except OSError:
            pass

    def test_roundtrip_and_order(self):
        r = RingBuffer.create(self.path, 8)
        for i in range(1, 6):
            self.assertTrue(r.push(i, 1, 0, 10 * i, 9900 + i, ts_ns=i))
        got = r.pop_batch()
        self.assertEqual([g[0] for g in got], [1, 2, 3, 4, 5])
        self.assertEqual(got[2], (3, 1, 0, 0, 30, 9903, 3))
        self.assertEqual(len(r), 0)
        r.close()

    def test_full_then_space_after_pop(self):
        r = RingBuffer.create(self.path, 4)
        self.assertTrue(all(r.push(i, 0, 0, 1, 1) for i in range(1, 5)))
        self.assertFalse(r.push(99, 0, 0, 1, 1))
        r.pop_batch(2)
        self.assertTrue(r.push(5, 0, 0, 1, 1))
        self.assertEqual([x[0] for x in r.pop_batch()], [3, 4, 5])
        r.close()

    def test_wraparound_many_times(self):
        r = RingBuffer.create(self.path, 8)
        seen = []
        for base in range(0, 100, 5):
            for i in range(5):
                self.assertTrue(r.push(base + i + 1, 0, 0, 1, 1))
            seen += [x[0] for x in r.pop_batch()]
        self.assertEqual(seen, list(range(1, 101)))
        r.close()

    def test_rejects_bad_capacity_and_file(self):
        with self.assertRaises(ValueError):
            RingBuffer.create(self.path, 100)
        with open(self.path, "wb") as f:
            f.write(b"\0" * 4096)
        with self.assertRaises(ValueError):
            RingBuffer.attach(self.path)

    def test_two_processes_share_memory(self):
        r = RingBuffer.create(self.path, 1 << 10)
        n = 20_000
        p = mp.get_context("spawn").Process(target=_producer, args=(self.path, n))
        p.start()
        expect, got = 1, 0
        while got < n:
            for rec in r.pop_batch(256):
                self.assertEqual(rec[0], expect)       # in order, none lost or torn
                self.assertEqual(rec[4], expect % 1000 + 1)
                expect += 1
                got += 1
        p.join(10)
        self.assertEqual(got, n)
        r.close()


if __name__ == "__main__":
    unittest.main()


def _hammer(path, n):
    ring = RingBuffer.attach(path)
    i = 1
    while i <= n:
        if ring.push(i, 0, 0, 1, 1):
            i += 1
    ring.close()


class TornIndexTests(unittest.TestCase):
    def test_len_never_negative_while_producer_runs(self):
        """Regression: indices must be written with one 8-byte store. A byte-by-byte write
        (struct '<Q') lets a reader see a half-updated tail, e.g. 0x0100 -> 0x0000."""
        d = tempfile.mkdtemp()
        path = os.path.join(d, "t.ring")
        r = RingBuffer.create(path, 1 << 10)
        n = 150_000
        p = mp.get_context("spawn").Process(target=_hammer, args=(path, n))
        p.start()
        got = bad = 0
        while got < n:
            if len(r) < 0:
                bad += 1
            got += len(r.pop_batch(64))
        p.join(10)
        r.close()
        self.assertEqual(bad, 0)
