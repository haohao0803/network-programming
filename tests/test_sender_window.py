#!/usr/bin/env python3
"""Windowed sender tests; simulated peers are not production receiver code.

test_sender consumes the executable CLI argument and supplies socket lifecycle.
"""
import hashlib
from pathlib import Path
import re
import socket
import struct
import subprocess
import tempfile
import time
import unittest

from test_sender import EXECUTABLE, WIRE, Peer


class WindowPeer(Peer):
    def __init__(self, mode, sender_limit, advertised=4):
        super().__init__(mode)
        self.sender_limit = sender_limit
        self.advertised = advertised
        self.peak_burst = 0
        self.probe_count = 0
        self.repeated_data = 0

    def serve(self):
        ident = address = chunks = size = None
        base = last_ack = 0
        window = self.advertised
        allowed_edge = window
        buffered = {}
        held = None
        zero = self.mode in ("zero_start", "zero_forever")
        batch_target = min(window, self.sender_limit)
        counts = {}

        def ack(seq, advertised=None, kind=4):
            nonlocal last_ack, window, allowed_edge
            if advertised is None:
                advertised = self.advertised
            reply = WIRE.pack(b"RUDP", 1, kind, 0, ident, seq, 0, advertised)
            self.sock.sendto(reply, address)
            last_ack, window = seq, advertised
            allowed_edge = max(allowed_edge, seq + advertised)

        while not self.stop.is_set():
            try:
                packet, source = self.sock.recvfrom(65535)
            except socket.timeout:
                continue
            magic, version, kind, reserved, transfer, seq, length, unused = WIRE.unpack(packet[:20])
            assert (magic, version, reserved, unused) == (b"RUDP", 1, 0, 0)
            assert len(packet) == length + 20
            if ident is None:
                assert kind == 1 and seq == 0 and length == 12
                ident, address = transfer, source
                chunks, size = struct.unpack("!IQ", packet[20:])
                assert chunks == (size + 1023) // 1024
                batch_target = min(batch_target, chunks)
            assert (transfer, source) == (ident, address)
            key = kind, seq
            counts[key] = counts.get(key, 0) + 1
            repeated = key in self.seen
            if repeated:
                assert self.seen[key] == packet, "cached retransmission changed"
            else:
                self.seen[key] = packet
            if kind == 1:
                if counts[key] > 1:
                    self.probe_count += 1
                    if self.mode != "zero_forever" and self.probe_count >= 2:
                        zero = False
                ack(0, 0 if zero else self.advertised, kind=2)
                continue
            if kind == 5:
                assert seq == chunks and not length
                assert base == chunks and len(self.received) == size
                self.finished = True
                ack(chunks, kind=6)
                continue
            assert kind == 3
            assert length == min(1024, size - seq * 1024)
            if seq == 0 and not repeated and self.mode in ("grow_input", "shrink_input"):
                if self.mode == "grow_input":
                    with self.input_path.open("ab") as file:
                        file.write(b"changed")
                else:
                    with self.input_path.open("wb"):
                        pass
            if self.mode == "duplicate_forever":
                end = time.monotonic() + 1
                while time.monotonic() < end and not self.stop.is_set():
                    ack(0)
                    time.sleep(0.002)
                continue
            if self.mode == "data_error":
                self.sock.sendto(WIRE.pack(b"RUDP", 1, 7, 0, ident, seq + 1, 0, 0), address)
                continue
            if zero:
                if seq < base:
                    self.probe_count += 1
                    if self.probe_count >= 2:
                        zero = False
                    ack(base, 0 if zero else self.advertised)
                else:
                    # Only previously in-flight packets may arrive after shrink.
                    assert seq < allowed_edge
                    if repeated:
                        raise AssertionError("unacknowledged DATA resent in zero window")
                continue
            if repeated:
                self.repeated_data += 1
                assert seq < base or seq < last_ack + window, "retry outside reduced window"
            else:
                assert seq < allowed_edge, "new DATA outside receive window"
                assert seq - last_ack < self.sender_limit, "new DATA outside sender limit"
            self.peak_burst = max(self.peak_burst, seq - last_ack + 1)
            if self.mode in ("gap", "shrink") and seq == 1 and counts[key] == 1:
                continue
            if self.mode == "reorder_duplicate" and seq == 0 and held is None and not repeated:
                held = packet[20:]
                ack(0)
                continue
            if seq >= base:
                buffered.setdefault(seq, packet[20:])
            if held is not None and seq == 1:
                buffered[0] = held
                held = None
            assert len(buffered) <= self.advertised, "test receiver buffer overflow"
            while base in buffered:
                self.received.extend(buffered.pop(base))
                base += 1
            if self.mode == "batch":
                if base >= batch_target:
                    ack(base)
                    batch_target = min(chunks, base + min(window, self.sender_limit))
            elif self.mode == "zero_mid" and base == 1:
                zero = True
                ack(base, 0)
            elif self.mode == "shrink":
                ack(base, 1)
            else:
                # Impossible future ACKs must not skip unsent or missing data.
                self.sock.sendto(WIRE.pack(b"RUDP", 1, 4, 0, ident, chunks + 1, 0, 0), address)
                ack(base)
                if self.mode in ("reorder_duplicate", "stale_window"):
                    ack(base)
                    if base:
                        self.sock.sendto(WIRE.pack(b"RUDP", 1, 4, 0, ident, base - 1, 0, 0), address)


class WindowSenderTests(unittest.TestCase):
    def transfer(self, mode, limit=4, advertised=4, size=32 * 1024 + 13, success=True):
        data = bytes(range(256)) * (size // 256) + bytes(range(size % 256))
        with tempfile.TemporaryDirectory() as directory, WindowPeer(mode, limit, advertised) as peer:
            path = Path(directory) / "window input.bin"
            path.write_bytes(data)
            peer.input_path = path
            started = time.monotonic()
            result = subprocess.run([EXECUTABLE, "127.0.0.1", str(peer.port), str(path),
                                     "60", "4", str(limit)], capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0 if success else 1, result.stderr)
            if success:
                self.assertTrue(peer.finished)
                self.assertEqual(hashlib.sha256(data).digest(), hashlib.sha256(peer.received).digest())
                if mode == "batch" and size > 1024:
                    self.assertEqual(peer.peak_burst, min(limit, advertised, (size + 1023) // 1024))
                if mode in ("gap", "shrink"):
                    self.assertGreater(peer.repeated_data, 0)
                    self.assertGreater(int(re.search(r"retransmissions=(\d+)", result.stdout)[1]), 0)
                if mode in ("zero_start", "zero_mid") and size:
                    self.assertGreaterEqual(peer.probe_count, 2)
                    self.assertGreaterEqual(int(re.search(r"probes=(\d+)", result.stdout)[1]), 2)
                if mode == "stale_window":
                    self.assertIn("probes=0", result.stdout)
            else:
                self.assertLess(time.monotonic() - started, 2)
                self.assertNotIn("PASS", result.stdout)
                self.assertFalse(peer.finished)
            return result

    def test_multiple_inflight_and_both_window_limits(self):
        for limit, advertised in ((4, 4), (8, 2), (2, 8), (256, 4), (1, 4)):
            with self.subTest(limit=limit, advertised=advertised):
                self.transfer("batch", limit, advertised)

    def test_gap_retransmission_and_window_shrink(self):
        for mode in ("gap", "shrink"):
            with self.subTest(mode=mode):
                self.transfer(mode)

    def test_reordering_duplicates_and_stale_window(self):
        for mode in ("reorder_duplicate", "stale_window"):
            with self.subTest(mode=mode):
                self.transfer(mode)

    def test_zero_window_probe_and_recovery(self):
        for mode in ("zero_start", "zero_mid"):
            with self.subTest(mode=mode):
                self.transfer(mode)

    def test_no_progress_is_bounded(self):
        for mode in ("zero_forever", "duplicate_forever", "data_error"):
            with self.subTest(mode=mode):
                self.transfer(mode, success=False)

    def test_empty_and_boundary_files(self):
        for size in (0, 1, 1024, 1025, 1024 * 1024, 10 * 1024 * 1024):
            with self.subTest(size=size):
                self.transfer("batch", size=size)

    def test_invalid_window_arguments(self):
        for window in ("0", "257", "-1", "4x"):
            with self.subTest(window=window):
                result = subprocess.run([EXECUTABLE, "127.0.0.1", "19090", "missing",
                                         "60", "2", window], capture_output=True, timeout=2)
                self.assertEqual(result.returncode, 2)

    def test_changed_input_does_not_send_completion(self):
        for mode in ("grow_input", "shrink_input"):
            with self.subTest(mode=mode):
                self.transfer(mode, success=False)


if __name__ == "__main__":
    unittest.main(verbosity=2)
