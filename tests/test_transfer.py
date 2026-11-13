#!/usr/bin/env python3
"""Real C sender/receiver integration tests with a deterministic UDP relay.

Usage: python3 tests/test_transfer.py SENDER_BINARY RECEIVER_BINARY
The relay forwards actual datagrams and injects faults; it never saves a file
or creates protocol replies. SHA-256 checks read the receiver's published file.
"""

import hashlib
import json
import os
from pathlib import Path
import platform
import re
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import unittest


if len(sys.argv) < 3:
    raise SystemExit(__doc__.splitlines()[2])
SENDER = str(Path(sys.argv.pop(1)).resolve())
RECEIVER = str(Path(sys.argv.pop(1)).resolve())
WIRE = struct.Struct("!4sBBHIIHH")
ROOT = Path(__file__).resolve().parents[1]
TEMP_ROOT = Path(os.environ.get("TMPDIR", ROOT / "work" / "tmp"))
RECORDS = []


def digest(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def metric(output, name):
    found = re.search(r"\b" + re.escape(name) + r"=(\d+)\b", output)
    if not found:
        raise AssertionError(f"Missing {name} statistic: {output}")
    return int(found[1])


class ReceiverProcess:
    """Collect flushed receiver events while retaining full process output."""

    def __init__(self, output, window, idle_ms, linger_ms):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as picker:
            picker.bind(("127.0.0.1", 0))
            self.port = picker.getsockname()[1]
        self.lines = []
        self.condition = threading.Condition()
        self.finished = False
        self.command = [RECEIVER, "127.0.0.1", str(self.port), str(output),
                        str(window), str(idle_ms), str(linger_ms)]
        self.process = subprocess.Popen(self.command, stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, text=True,
                                        bufsize=1)
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self):
        try:
            for line in self.process.stdout:
                with self.condition:
                    self.lines.append(line)
                    self.condition.notify_all()
        finally:
            with self.condition:
                self.finished = True
                self.condition.notify_all()

    def wait_line(self, prefix, after=0, timeout=4):
        deadline = time.monotonic() + timeout
        with self.condition:
            while True:
                for line in self.lines[after:]:
                    if line.startswith(prefix):
                        return line
                if self.finished or time.monotonic() >= deadline:
                    raise AssertionError(f"Receiver event {prefix!r} missing:\n"
                                         + "".join(self.lines))
                self.condition.wait(deadline - time.monotonic())

    def control(self, paused):
        with self.condition:
            start = len(self.lines)
        self.process.send_signal(signal.SIGUSR1 if paused else signal.SIGUSR2)
        return self.wait_line(f"WINDOW paused={int(paused)} ", after=start)

    def output(self):
        with self.condition:
            return "".join(self.lines)

    def wait(self, timeout):
        status = self.process.wait(timeout=timeout)
        self.reader.join(timeout=2)
        if self.reader.is_alive():
            raise AssertionError("Receiver log collector did not stop")
        return status

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=2)
        self.reader.join(timeout=2)
        self.process.stdout.close()


class Relay:
    """Single relay endpoint preserves both programs' session source checks."""

    def __init__(self, receiver, chunks, drop_type=None, reorder=False,
                 zero=None, lose_resume=False, drop_all_fin_ack=False):
        self.receiver = receiver
        self.receiver_address = ("127.0.0.1", receiver.port)
        self.sender_address = None
        self.chunks = chunks
        self.drop_type = drop_type
        self.reorder = reorder
        self.zero = zero
        self.lose_resume = lose_resume
        self.drop_all_fin_ack = drop_all_fin_ack
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.bind(("127.0.0.1", 0))
        self.socket.settimeout(0.02)
        self.port = self.socket.getsockname()[1]
        self.stop = threading.Event()
        self.errors = []
        self.counts = {}
        self.dropped = 0
        self.reordered = 0
        self.duplicated = 0
        self.held = None
        self.pause_triggered = zero in ("initial", "permanent", "empty")
        self.resumed = False
        self.zero_replies = 0
        self.resume_dropped = 0
        self.probes_after_resume = 0
        self.forwarded_data = set()
        self.thread = threading.Thread(target=self.run, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.stop.set()
        self.thread.join(timeout=3)
        self.socket.close()
        if self.thread.is_alive():
            raise AssertionError("UDP relay did not stop")

    def run(self):
        try:
            while not self.stop.is_set():
                try:
                    packet, source = self.socket.recvfrom(65535)
                except socket.timeout:
                    continue
                self.forward(packet, source)
        except Exception:
            self.errors.append(traceback.format_exc())
            self.stop.set()

    def forward(self, packet, source):
        if len(packet) < WIRE.size:
            raise AssertionError("A production program emitted a short header")
        magic, version, kind, reserved, _, seq, length, window = WIRE.unpack_from(packet)
        if (magic, version, reserved) != (b"RUDP", 1, 0) or len(packet) != 20 + length:
            raise AssertionError("A production program emitted an invalid frame")
        from_receiver = source == self.receiver_address
        if from_receiver:
            if self.sender_address is None:
                raise AssertionError("Receiver replied before START")
            destination = self.sender_address
        else:
            if self.sender_address is None:
                self.sender_address = source
            if source != self.sender_address:
                raise AssertionError("Unexpected relay source endpoint")
            destination = self.receiver_address
        key = (from_receiver, kind, seq)
        self.counts[key] = self.counts.get(key, 0) + 1

        if from_receiver and kind == 6 and self.drop_all_fin_ack:
            self.dropped += 1
            return

        if kind == self.drop_type and not self.dropped:
            self.dropped += 1
            return

        # With sender window=1 the first DATA ACK is a deterministic barrier:
        # pause before releasing it, and use the receiver's actual zero ACK.
        if (from_receiver and kind == 4 and seq == 1 and self.zero == "middle"
                and not self.pause_triggered):
            event = self.receiver.control(True)
            if "base=1 " not in event:
                raise AssertionError(f"Middle pause passed the barrier: {event}")
            self.pause_triggered = True
            return

        if (from_receiver and kind == 4 and seq == self.chunks
                and self.zero == "before_fin" and not self.pause_triggered):
            self.receiver.control(True)
            self.pause_triggered = True

        if (from_receiver and self.resumed and self.lose_resume and kind == 4
                and window > 0 and not self.resume_dropped):
            self.resume_dropped += 1
            return

        if not from_receiver and self.resumed:
            if kind == 1 or (kind == 3 and seq in self.forwarded_data):
                self.probes_after_resume += 1

        # Delay DATA 0 until DATA 1 arrives; duplicate the released originals.
        if self.reorder and not from_receiver and kind == 3:
            if seq == 0 and self.held is None and not self.reordered:
                self.held = packet
                return
            if seq == 1 and self.held is not None:
                self.socket.sendto(packet, destination)
                self.socket.sendto(packet, destination)
                self.socket.sendto(self.held, destination)
                self.socket.sendto(self.held, destination)
                self.forwarded_data.update((0, 1))
                self.held = None
                self.reordered += 1
                self.duplicated += 2
                return

        self.socket.sendto(packet, destination)
        if not from_receiver and kind == 3:
            self.forwarded_data.add(seq)
        if (from_receiver and kind in (2, 4) and not window and self.chunks
                and self.pause_triggered and not self.resumed):
            self.zero_replies += 1
            if self.zero in ("initial", "middle") and self.zero_replies >= 2:
                # The zero reply is on the wire before the resume event.
                self.receiver.control(False)
                self.resumed = True


class TransferTests(unittest.TestCase):
    def transfer(self, scenario, size=32 * 1024 + 13, send_window=8,
                 receive_window=8, drop_type=None, reorder=False, zero=None,
                 lose_resume=False, success=True, drop_all_fin_ack=False):
        TEMP_ROOT.mkdir(parents=True, exist_ok=True)
        timeout_ms, retries = 120, 5
        linger_ms = (retries + 1) * timeout_ms + 1000
        idle_ms = 15000 if success else 1400
        expected_sender_exit = 0 if success and not drop_all_fin_ack else 1
        chunks = (size + 1023) // 1024
        with tempfile.TemporaryDirectory(prefix="transfer-", dir=TEMP_ROOT) as directory:
            directory = Path(directory)
            source = directory / "source with spaces.bin"
            target = directory / "received with spaces.bin"
            # Each chunk differs, so a swapped chunk changes the saved hash.
            source.write_bytes(hashlib.shake_256(b"rudp integration payload").digest(size))
            receiver = ReceiverProcess(target, receive_window, idle_ms, linger_ms)
            try:
                receiver.wait_line("READY ")
                if zero in ("initial", "permanent", "empty"):
                    receiver.control(True)
                with Relay(receiver, chunks, drop_type, reorder, zero, lose_resume,
                           drop_all_fin_ack) as relay:
                    command = [SENDER, "127.0.0.1", str(relay.port), str(source),
                               str(timeout_ms), str(retries), str(send_window)]
                    started = time.monotonic()
                    sender = subprocess.run(command, capture_output=True,
                                            text=True, timeout=90)
                    receiver_status = receiver.wait(timeout=20)
                    elapsed = time.monotonic() - started
                    diagnostics = (f"sender:\n{sender.stdout}{sender.stderr}\n"
                                   f"receiver:\n{receiver.output()}\n"
                                   f"relay:\n{''.join(relay.errors)}")
                    self.assertFalse(relay.errors, diagnostics)
                    self.assertEqual(sender.returncode, expected_sender_exit, diagnostics)
                    self.assertEqual(receiver_status, 0 if success else 1, diagnostics)
                    record = {
                        "scenario": scenario, "size": size,
                        "sender_command": command, "receiver_command": receiver.command,
                        "sender_window": send_window, "receiver_window": receive_window,
                        "sender_exit": sender.returncode, "receiver_exit": receiver_status,
                        "outcome": ("receiver-saved-sender-unconfirmed" if drop_all_fin_ack
                                    else "confirmed" if success else "bounded-failure"),
                        "seconds": round(elapsed, 4), "source_sha256": digest(source),
                        "output_sha256": digest(target) if target.exists() else None,
                        "sender_stdout": sender.stdout, "sender_stderr": sender.stderr,
                        "receiver_output": receiver.output(), "dropped": relay.dropped,
                        "reordered": relay.reordered, "duplicated": relay.duplicated,
                        "zero_replies": relay.zero_replies,
                        "resume_ack_dropped": relay.resume_dropped,
                        "probes_after_resume": relay.probes_after_resume,
                    }
                    if success:
                        self.assertTrue(target.is_file(), diagnostics)
                        self.assertEqual(target.stat().st_size, size, diagnostics)
                        self.assertEqual(record["source_sha256"], record["output_sha256"], diagnostics)
                        self.assertIn("PASS receiver ", receiver.output(), diagnostics)
                        self.assertLessEqual(metric(receiver.output(), "peak_buffered"), receive_window)
                        if drop_all_fin_ack:
                            self.assertNotIn("PASS sender", sender.stdout, diagnostics)
                            self.assertEqual(relay.dropped, retries + 1, diagnostics)
                            self.assertEqual(relay.counts.get((False, 5, chunks)), retries + 1)
                            self.assertGreaterEqual(metric(receiver.output(), "duplicates"), retries)
                            self.assertLess(elapsed, 4, diagnostics)
                        else:
                            self.assertIn("PASS sender ", sender.stdout, diagnostics)
                            self.assertEqual(metric(sender.stdout, "peak_inflight"),
                                             min(send_window, receive_window, chunks))
                            if chunks > 1 and min(send_window, receive_window) > 1:
                                self.assertGreater(metric(sender.stdout, "peak_inflight"), 1)
                        if drop_type is not None:
                            self.assertEqual(relay.dropped, 1)
                            self.assertGreater(metric(sender.stdout, "retransmissions"), 0)
                        if reorder:
                            self.assertEqual(relay.reordered, 1)
                            self.assertGreater(metric(receiver.output(), "duplicates"), 0)
                        if zero in ("initial", "middle"):
                            self.assertTrue(relay.pause_triggered and relay.resumed, diagnostics)
                            self.assertGreaterEqual(relay.zero_replies, 2, diagnostics)
                            self.assertGreater(metric(sender.stdout, "probes"), 0, diagnostics)
                        if lose_resume:
                            self.assertEqual(relay.resume_dropped, 1, diagnostics)
                            self.assertGreater(relay.probes_after_resume, 0, diagnostics)
                        if zero in ("before_fin", "empty"):
                            self.assertTrue(relay.pause_triggered, diagnostics)
                            self.assertFalse(relay.resumed, diagnostics)
                    else:
                        self.assertLess(elapsed, 4, diagnostics)
                        self.assertFalse(target.exists(), diagnostics)
                        self.assertNotIn("PASS sender", sender.stdout, diagnostics)
                        self.assertNotIn("PASS receiver", receiver.output(), diagnostics)
                    self.assertEqual(sorted(p.name for p in directory.iterdir()),
                                     sorted([source.name] + ([target.name] if success else [])))
                    RECORDS.append(record)
                    print(f"PASS transfer scenario={scenario} bytes={size} "
                          f"sha256={record['output_sha256']} sender={sender.returncode} "
                          f"receiver={receiver_status} outcome={record['outcome']}", flush=True)
            finally:
                receiver.close()

    def test_file_hashes_stop_and_wait_and_windowed(self):
        for window in (1, 8):
            for size in (0, 1, 1024, 1025, 1024 * 1024, 10 * 1024 * 1024):
                with self.subTest(size=size, window=window):
                    self.transfer(f"hash-window-{window}-{size}", size=size, send_window=window)

    def test_both_window_limits(self):
        for sender_window, receiver_window in ((8, 2), (2, 8), (256, 256), (4, 1)):
            with self.subTest(sender=sender_window, receiver=receiver_window):
                self.transfer(f"window-{sender_window}-{receiver_window}",
                              size=300 * 1024 + 13,
                              send_window=sender_window, receive_window=receiver_window)

    def test_loss_of_each_request_and_confirmation(self):
        for kind, label in ((1, "START"), (2, "START_ACK"), (3, "DATA"),
                            (4, "ACK"), (5, "FIN"), (6, "FIN_ACK")):
            with self.subTest(kind=label):
                # Window=1 forces the missing ACK to exercise retransmission.
                self.transfer(f"drop-first-{label}", send_window=1, drop_type=kind)

    def test_reordering_and_duplicates(self):
        self.transfer("reorder-duplicate", reorder=True)

    def test_all_fin_ack_loss_preserves_saved_file(self):
        self.transfer("drop-all-FIN_ACK", drop_all_fin_ack=True)

    def test_zero_window_and_lost_resume_notifications(self):
        for phase in ("initial", "middle"):
            for drop_resume in (False, True):
                with self.subTest(phase=phase, drop_resume=drop_resume):
                    self.transfer(f"zero-{phase}-drop-resume-{int(drop_resume)}",
                                  send_window=1, zero=phase, lose_resume=drop_resume)

    def test_permanent_pause_fails_within_budget(self):
        self.transfer("zero-permanent", size=1025, send_window=1,
                      zero="permanent", success=False)

    def test_fin_works_while_paused(self):
        self.transfer("paused-empty-file", size=0, send_window=1, zero="empty")
        self.transfer("paused-before-fin", size=1025, send_window=1, zero="before_fin")


if __name__ == "__main__":
    program = unittest.main(verbosity=2, exit=False)
    report = {
        "success": program.result.wasSuccessful(), "tests_run": program.result.testsRun,
        "scenarios_completed": len(RECORDS), "platform": platform.platform(),
        "python": platform.python_version(),
        "sender_binary": SENDER, "sender_binary_sha256": digest(SENDER),
        "receiver_binary": RECEIVER, "receiver_binary_sha256": digest(RECEIVER),
        "records": RECORDS,
    }
    destination = os.environ.get("RUDP_RESULTS_JSON")
    if destination:
        path = Path(destination)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Integration scenarios completed: {len(RECORDS)}; "
          f"success={program.result.wasSuccessful()}", flush=True)
    raise SystemExit(0 if program.result.wasSuccessful() else 1)
