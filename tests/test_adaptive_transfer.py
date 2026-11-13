#!/usr/bin/env python3
"""Adaptive mode against Clive's real receiver, using the existing fault relay."""
import csv
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from test_transfer import SENDER, RECEIVER, ReceiverProcess, Relay, digest


class AdaptiveTransferTests(unittest.TestCase):
    def transfer(self, name, size=65549, limit=16, receive=8, drop=None, zero=None):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            source, target, trace = [directory / n for n in ("source", "received", "trace.csv")]
            source.write_bytes(hashlib.shake_256(b"adaptive file").digest(size))
            receiver = ReceiverProcess(target, receive, 20000, 3500)
            try:
                receiver.wait_line("READY ")
                if zero == "initial":
                    receiver.control(True)
                with Relay(receiver, (size + 1023) // 1024, drop_type=drop, zero=zero) as relay:
                    command = [SENDER, "127.0.0.1", str(relay.port), str(source),
                               "1000", "2", str(limit), "--adaptive", "--trace", str(trace)]
                    result = subprocess.run(command, capture_output=True, text=True, timeout=25)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr + receiver.output())
                    self.assertEqual(receiver.wait(10), 0, receiver.output())
                    self.assertFalse(relay.errors, relay.errors)
                    self.assertEqual(digest(source), digest(target))
                    if drop:
                        self.assertEqual(relay.dropped, 1)
                    with trace.open(newline="") as file:
                        rows = list(csv.DictReader(file))
                    self.assertEqual(rows[-1]["event"], "complete")
                    record = {"scenario": name, "bytes": size, "sha256": digest(target),
                              "sender_stdout": result.stdout, "sender_stderr": result.stderr,
                              "receiver_output": receiver.output(), "dropped": relay.dropped,
                              "sender_exit": result.returncode, "source_sha256": digest(source)}
                    evidence = os.environ.get("RUDP_ADAPTIVE_EVIDENCE")
                    if evidence:
                        destination = Path(evidence)
                        destination.mkdir(parents=True, exist_ok=True)
                        (destination / (name + ".json")).write_text(json.dumps(record, indent=2) + "\n")
                        (destination / (name + ".csv")).write_bytes(trace.read_bytes())
                    print(f"PASS adaptive {name}: bytes={size} sha256={digest(target)}", flush=True)
            finally:
                receiver.close()

    def test_real_files(self):
        for size in (0, 1, 1024, 1025, 1048576):
            with self.subTest(size=size):
                self.transfer(f"file-{size}", size=size)

    def test_real_loss(self):
        for kind in (3, 4, 6):
            with self.subTest(kind=kind):
                self.transfer(f"drop-type-{kind}", limit=1, drop=kind)

    def test_real_flow_control(self):
        self.transfer("receive-window-2", receive=2)
        self.transfer("zero-window-resume", zero="initial")


if __name__ == "__main__":
    unittest.main(verbosity=2)
