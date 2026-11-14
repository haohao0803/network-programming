#!/usr/bin/env python3
"""Adaptive sender checks against simulated peers, not the production receiver."""
import csv
from pathlib import Path
import subprocess
import tempfile
import unittest

from test_sender import EXECUTABLE
from test_sender_window import WindowPeer


class AdaptiveTests(unittest.TestCase):
    def transfer(self, mode, limit=16, advertised=8, size=65549, success=True):
        with tempfile.TemporaryDirectory() as directory:
            source, trace = Path(directory) / "input", Path(directory) / "trace.csv"
            data = bytes(range(256)) * (size // 256) + bytes(range(size % 256))
            source.write_bytes(data)
            with WindowPeer(mode, limit, advertised) as peer:
                result = subprocess.run(
                    [EXECUTABLE, "127.0.0.1", str(peer.port), str(source),
                     "1000", "2", str(limit), "--adaptive", "--trace", str(trace)],
                    capture_output=True, text=True, timeout=20)
                self.assertEqual(result.returncode, 0 if success else 1, result.stderr)
                if success:
                    self.assertEqual(data, bytes(peer.received))
                    self.assertTrue(peer.finished)
            with trace.open(newline="") as file:
                rows = list(csv.DictReader(file))
            self.assertEqual(rows[-1]["event"], "complete" if success else "failed")
            for row in rows:
                self.assertEqual(row["schema_version"], "2")
                self.assertNotIn(None, row)
                self.assertNotIn(None, row.values())
                self.assertTrue(1000 <= int(row["rto_ms"]) <= 60000)
                self.assertTrue(1 <= float(row["cwnd_packets"]) <= limit)
                self.assertLessEqual(int(row["effective_window_packets"]), int(row["receive_window"]))
            return rows

    def test_growth_and_flow_limit(self):
        for limit, advertised in ((16, 8), (8, 2), (1, 8)):
            with self.subTest(limit=limit, advertised=advertised):
                rows = self.transfer("normal", limit, advertised)
                sends = [r for r in rows if r["event"] == "send" and r["packet_type"] == "3"]
                self.assertEqual(float(sends[0]["cwnd_packets"]), 1)
                for row in sends:
                    self.assertLessEqual(int(row["next"]) - int(row["base"]),
                                         int(row["effective_window_packets"]))
                self.assertGreaterEqual(max(float(r["cwnd_packets"]) for r in rows), min(limit, 2))

    def test_loss_backoff_and_karn(self):
        rows = self.transfer("gap")
        timeouts = [r for r in rows if r["event"] == "timeout" and r["packet_type"] == "3"]
        self.assertTrue(timeouts)
        self.assertEqual(float(timeouts[0]["cwnd_packets"]), 1)
        self.assertEqual(int(timeouts[0]["rto_ms"]), 2000)
        after = rows[rows.index(timeouts[0]) + 1:]
        recovered = next(r for r in after if r["event"] == "ack")
        self.assertEqual(int(recovered["rtt_sample_ms"]), -1)
        self.assertEqual(int(recovered["rto_ms"]), 2000)

    def test_zero_window_resume_and_persist(self):
        for mode in ("zero_start", "zero_mid"):
            with self.subTest(mode=mode):
                rows = self.transfer(mode)
                timeouts = [r for r in rows if r["event"] == "timeout" and r["receive_window"] == "0"]
                self.assertTrue(timeouts)
                before = rows[rows.index(timeouts[0]) - 1]
                self.assertEqual(before["cwnd_packets"], timeouts[0]["cwnd_packets"])

    def test_bounded_no_progress(self):
        for mode in ("zero_forever", "duplicate_forever"):
            with self.subTest(mode=mode):
                self.transfer(mode, success=False)

    def test_boundaries(self):
        for size in (0, 1, 1024, 1025, 1048576):
            with self.subTest(size=size):
                self.transfer("normal", size=size)

    def test_reject_invalid_initial_rto(self):
        result = subprocess.run([EXECUTABLE, "127.0.0.1", "19090", "unused", "999", "2", "8", "--adaptive"],
                                capture_output=True, text=True, timeout=2)
        self.assertEqual(result.returncode, 2)
        self.assertIn("Adaptive initial RTO", result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
