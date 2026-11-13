#!/usr/bin/env python3
"""Check sender evidence against real subprocess outcomes and peer behavior."""
import csv
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

from test_sender import EXECUTABLE, Peer
from test_sender_window import WindowPeer


class TraceTests(unittest.TestCase):
    def command(self, peer, source, trace):
        return [EXECUTABLE, "127.0.0.1", str(peer.port), str(source),
                "60", "4", "4", "--trace", str(trace)]

    def rows(self, path, result, complete):
        with path.open(newline="") as file:
            rows = list(csv.DictReader(file))
        self.assertGreater(len(rows), 2)
        self.assertEqual(rows[0]["event"], "begin")
        self.assertEqual(rows[-1]["event"], "complete" if complete else "failed")
        self.assertEqual([int(row["event_index"]) for row in rows], list(range(len(rows))))
        self.assertEqual(len({row["transfer_id"] for row in rows}), 1)
        times = [int(row["elapsed_ms"]) for row in rows]
        self.assertGreaterEqual(times[0], 0)
        self.assertEqual(times, sorted(times))
        bases = [int(row["base"]) for row in rows]
        self.assertEqual(bases, sorted(bases))
        for row in rows:
            self.assertNotIn(None, row)
            self.assertNotIn(None, row.values())
            self.assertEqual(row["schema_version"], "1")
            self.assertEqual(row["rto_ms"], "60")
            self.assertEqual(row["configured_window"], "4")
            base, next_seq = int(row["base"]), int(row["next"])
            self.assertLessEqual(base, next_seq)
            self.assertLessEqual(next_seq - base, 4)
            if row["event"] == "ack":
                self.assertEqual(int(row["sequence"]), base)
        if complete:
            self.assertEqual(rows[-2]["event"], "control_ack")
            self.assertEqual(rows[-2]["packet_type"], "6")
            for key in ("retransmissions", "probes"):
                count = int(re.search(rf"{key}=(\d+)", result.stdout)[1])
                self.assertEqual(int(rows[-1][key]), count)
            self.assertIn("elapsed_ms=", result.stdout)
        else:
            self.assertNotIn("complete", [row["event"] for row in rows])
            self.assertNotIn("PASS", result.stdout)
        return rows

    def test_success_and_zero_window_trace(self):
        for mode in ("gap", "zero_mid"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                source, trace = Path(directory) / "source.bin", Path(directory) / "sender trace.csv"
                source.write_bytes(bytes(range(256)) * 129)
                with WindowPeer(mode, 4) as peer:
                    result = subprocess.run(self.command(peer, source, trace), capture_output=True,
                                            text=True, timeout=10)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(source.read_bytes(), bytes(peer.received))
                rows = self.rows(trace, result, complete=True)
                events = {row["event"] for row in rows}
                self.assertIn("ack", events)
                self.assertIn("retransmit" if mode == "gap" else "probe", events)
                self.assertEqual(int(rows[-1]["base"]), 33)
                self.assertEqual(sum(row["event"] == "send" and row["packet_type"] == "3"
                                     for row in rows), 33)

    def test_lost_control_ack_trace(self):
        with tempfile.TemporaryDirectory() as directory, Peer("drop_ack") as peer:
            source, trace = Path(directory) / "source", Path(directory) / "trace.csv"
            source.write_bytes(b"single packet")
            # Peer advertises one packet even though the configured limit is four.
            result = subprocess.run(self.command(peer, source, trace), capture_output=True,
                                    text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            rows = self.rows(trace, result, complete=True)
            retried = {row["packet_type"] for row in rows if row["event"] == "retransmit"}
            self.assertEqual(retried, {"1", "3", "5"})
            self.assertEqual(sum(row["event"] == "timeout" for row in rows), 3)

    def test_unconfirmed_finish_is_failed_in_trace(self):
        with tempfile.TemporaryDirectory() as directory, Peer("no_fin_ack") as peer:
            source, trace = Path(directory) / "source", Path(directory) / "trace.csv"
            source.write_bytes(b"a")
            result = subprocess.run(self.command(peer, source, trace), capture_output=True,
                                    text=True, timeout=10)
            self.assertEqual(result.returncode, 1)
            self.assertTrue(peer.finished)
            self.rows(trace, result, complete=False)

    def test_existing_input_and_log_are_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory, Peer() as peer:
            source, trace = Path(directory) / "source", Path(directory) / "trace.csv"
            source.write_bytes(b"original input")
            trace.write_bytes(b"old evidence")
            for target in (source, trace, Path(directory) / "missing" / "trace.csv"):
                with self.subTest(target=target):
                    result = subprocess.run(self.command(peer, source, target), capture_output=True,
                                            text=True, timeout=2)
                    self.assertEqual(result.returncode, 2)
            self.assertEqual(source.read_bytes(), b"original input")
            self.assertEqual(trace.read_bytes(), b"old evidence")
            self.assertFalse(peer.seen, "network traffic started before trace creation succeeded")

    def test_trace_write_failure_is_reported(self):
        with tempfile.TemporaryDirectory() as directory, Peer() as peer:
            source, trace = Path(directory) / "source", Path(directory) / "trace.csv"
            source.write_bytes(b"a")
            # A fresh single-threaded launcher sets a child-only file size limit.
            # Do not use preexec_fn in this threaded fixture process.
            launcher = [sys.executable, "-c",
                        "import os,resource,signal,sys; "
                        "resource.setrlimit(resource.RLIMIT_FSIZE,(128,128)); "
                        "signal.signal(signal.SIGXFSZ,signal.SIG_IGN); "
                        "os.execv(sys.argv[1],sys.argv[1:])"]
            result = subprocess.run(launcher + self.command(peer, source, trace),
                                    capture_output=True, text=True, timeout=10)
            self.assertTrue(peer.finished)
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertNotIn("PASS", result.stdout)
            self.assertIn("Trace could not be saved; receiver completion confirmed", result.stderr)
            self.assertLessEqual(trace.stat().st_size, 128)

    def test_trace_with_default_network_parameters(self):
        with tempfile.TemporaryDirectory() as directory, Peer() as peer:
            source, trace = Path(directory) / "source", Path(directory) / "trace.csv"
            source.write_bytes(b"")
            result = subprocess.run([EXECUTABLE, "127.0.0.1", str(peer.port), str(source),
                                     "--trace", str(trace)], capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            with trace.open(newline="") as file:
                rows = list(csv.DictReader(file))
            self.assertEqual(rows[-1]["event"], "complete")
            self.assertEqual(rows[-1]["base"], "0")
            self.assertTrue(all(row["rto_ms"] == "1000" for row in rows))
            self.assertTrue(all(row["configured_window"] == "1" for row in rows))


if __name__ == "__main__":
    unittest.main(verbosity=2)
