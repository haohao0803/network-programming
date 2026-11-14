#!/usr/bin/env python3
"""Integration of haohao's CSV with Clive's real receiver and saved output."""
import csv
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

# The existing integration helper consumes SENDER and RECEIVER CLI arguments.
from test_transfer import SENDER, RECEIVER, ReceiverProcess, digest


class TraceTransferTests(unittest.TestCase):
    def test_real_output_and_trace_agree(self):
        records = []
        for window in (1, 4):
            with self.subTest(window=window), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                source, target, trace = root / "source.bin", root / "saved.bin", root / "sender.csv"
                source.write_bytes(hashlib.shake_256(b"sender trace real integration").digest(4097))
                peer = ReceiverProcess(target, 4, 3000, 1200)
                try:
                    peer.wait_line("READY ")
                    result = subprocess.run([SENDER, "127.0.0.1", str(peer.port), str(source),
                                             "60", "2", str(window), "--trace", str(trace)],
                                            capture_output=True, text=True, timeout=5)
                    receiver_exit = peer.wait(4)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(receiver_exit, 0, peer.output())
                    self.assertEqual(digest(source), digest(target))
                    with trace.open(newline="") as file:
                        rows = list(csv.DictReader(file))
                    self.assertEqual(rows[-1]["event"], "complete")
                    self.assertEqual(rows[-1]["base"], "5")
                    self.assertEqual(rows[-1]["next"], "5")
                    self.assertEqual(rows[-2]["event"], "control_ack")
                    self.assertEqual(rows[-2]["packet_type"], "6")
                    record = {
                        "sender_window": window, "bytes": source.stat().st_size,
                        "source_sha256": digest(source), "output_sha256": digest(target),
                        "sender_exit": result.returncode, "receiver_exit": receiver_exit,
                        "trace_rows": len(rows), "trace_sha256": digest(trace),
                        "sender_stdout": result.stdout, "receiver_stdout": peer.output(),
                    }
                    destination = os.environ.get("RUDP_TRACE_RESULTS_DIR")
                    if destination:
                        folder = Path(destination)
                        folder.mkdir(parents=True, exist_ok=True)
                        (folder / f"trace-window-{window}.csv").write_bytes(trace.read_bytes())
                    records.append(record)
                    print(f"PASS traced-transfer window={window} bytes=4097 "
                          f"sha256={record['output_sha256']} rows={len(rows)}", flush=True)
                finally:
                    peer.close()
        destination = os.environ.get("RUDP_TRACE_RESULTS_DIR")
        if destination:
            report = {"sender_binary_sha256": digest(SENDER),
                      "receiver_binary_sha256": digest(RECEIVER), "records": records}
            (Path(destination) / "trace-results.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    unittest.main(verbosity=2)
