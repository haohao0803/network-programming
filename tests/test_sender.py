#!/usr/bin/env python3
"""Sender contract tests. This peer is a fixture, NOT Clive's receiver."""
import hashlib
from pathlib import Path
import re
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest

EXECUTABLE = str(Path(sys.argv.pop(1)).resolve())
WIRE = struct.Struct("!4sBBHIIHH")


class Peer:
    def __init__(self, mode="normal"):
        self.mode = mode
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.settimeout(0.05)
        self.port = self.sock.getsockname()[1]
        self.stop = threading.Event()
        self.errors = []
        self.received = bytearray()
        self.seen = {}
        self.finished = False
        self.thread = threading.Thread(target=self.run)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.stop.set()
        self.thread.join(2)
        self.sock.close()
        if self.thread.is_alive():
            raise AssertionError("fixture did not stop")
        if self.errors:
            raise AssertionError(self.errors)

    def run(self):
        try:
            self.serve()
        except Exception as error:
            self.errors.append(repr(error))

    def serve(self):
        chunks = size = ident = address = None
        next_seq = 0
        while not self.stop.is_set():
            try:
                packet, source = self.sock.recvfrom(65535)
            except socket.timeout:
                continue
            magic, version, kind, reserved, transfer, seq, length, window = WIRE.unpack(packet[:20])
            assert (magic, version, reserved, window) == (b"RUDP", 1, 0, 0)
            assert len(packet) == 20 + length
            if ident is None:
                assert kind == 1 and seq == 0 and length == 12
                ident, address = transfer, source
                chunks, size = struct.unpack("!IQ", packet[20:])
                assert chunks == (size + 1023) // 1024
            assert (transfer, source) == (ident, address)
            key = kind, seq
            if key in self.seen:
                assert self.seen[key] == packet, "retries must preserve bytes and ID"
                repeated = True
            else:
                self.seen[key] = packet
                repeated = False
            if self.mode == "silent":
                continue
            if self.mode == "noise":
                # Keep delivering invalid packets past the retry deadline.
                end = time.monotonic() + 0.5
                while time.monotonic() < end and not self.stop.is_set():
                    self.sock.sendto(b"irrelevant", source)
                    time.sleep(0.002)
                continue
            if not repeated and (self.mode == "drop_request" or
                                 (self.mode == "drop_data" and kind == 3)):
                continue
            if kind == 1:
                reply_type, reply_seq = 2, 0
            elif kind == 3:
                assert length == min(1024, size - seq * 1024)
                if seq == next_seq:
                    self.received.extend(packet[20:])
                    next_seq += 1
                else:
                    assert seq == next_seq - 1
                reply_type, reply_seq = 4, next_seq
            elif kind == 5:
                assert length == 0 and seq == chunks
                assert next_seq == chunks and len(self.received) == size
                self.finished = True
                reply_type, reply_seq = 6, chunks
            else:
                raise AssertionError(f"unexpected kind {kind}")
            reply = WIRE.pack(b"RUDP", 1, reply_type, 0, transfer, reply_seq, 0, 1)
            if self.mode == "foreign_only":
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as stranger:
                    stranger.sendto(reply, source)
                continue
            if self.mode == "zero":
                reply = reply[:-2] + b"\0\0"
            if self.mode == "error":
                reply = WIRE.pack(b"RUDP", 1, 7, 0, transfer, reply_seq, 0, 0)
            if self.mode == "bad_then_good" or self.mode.startswith("invalid_"):
                invalid = [
                    b"x", reply + b"extra", reply[:4] + b"\x02" + reply[5:],
                    reply[:6] + b"\0\1" + reply[8:],
                    reply[:8] + struct.pack("!I", transfer ^ 1) + reply[12:],
                    reply[:12] + struct.pack("!I", reply_seq + 1) + reply[16:],
                    reply[:5] + b"\xff" + reply[6:],
                    reply[:16] + b"\0\1" + reply[18:],
                    reply + bytes(2000),
                ]
                if self.mode.startswith("invalid_"):
                    self.sock.sendto(invalid[int(self.mode.split("_")[1])], source)
                    continue
                for bad in invalid:
                    self.sock.sendto(bad, source)
                # A correct-looking reply from another port must not count.
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as stranger:
                    stranger.sendto(reply, source)
            if self.mode == "drop_ack" and not repeated:
                continue
            if self.mode == "no_fin_ack" and kind == 5:
                continue
            self.sock.sendto(reply, source)


class SenderTests(unittest.TestCase):
    def invoke(self, file, peer, timeout=60, retries=2):
        return subprocess.run([EXECUTABLE, "127.0.0.1", str(peer.port), str(file),
                               str(timeout), str(retries)], capture_output=True,
                              text=True, timeout=20)

    def transfer(self, size, mode="normal", success=True):
        data = (bytes(range(256)) * ((size + 255) // 256))[:size]
        with tempfile.TemporaryDirectory() as directory, Peer(mode) as peer:
            path = Path(directory) / "input with spaces.bin"
            path.write_bytes(data)
            started = time.monotonic()
            result = self.invoke(path, peer)
            elapsed = time.monotonic() - started
            self.assertEqual(result.returncode, 0 if success else 1, result.stderr)
            if success:
                self.assertTrue(peer.finished)
                self.assertEqual(hashlib.sha256(data).digest(), hashlib.sha256(peer.received).digest())
                if mode in ("drop_ack", "drop_data", "drop_request"):
                    count = int(re.search(r"retransmissions=(\d+)", result.stdout)[1])
                    self.assertGreater(count, 0)
            else:
                self.assertNotIn("PASS", result.stdout)
                self.assertLess(elapsed, 2, result.stderr)
                if mode.startswith("invalid_") or mode in ("silent", "noise", "foreign_only"):
                    self.assertTrue(all(kind == 1 for kind, _ in peer.seen),
                                    "invalid confirmation advanced sender state")
            return result

    def test_boundary_hashes(self):
        for size in (0, 1, 1024, 1025, 1024 * 1024, 10 * 1024 * 1024):
            with self.subTest(size=size):
                self.transfer(size)

    def test_lost_confirmations_and_data(self):
        for mode in ("drop_ack", "drop_data", "drop_request"):
            with self.subTest(mode=mode):
                self.transfer(2049, mode)

    def test_malformed_stale_and_foreign_packets(self):
        self.transfer(2049, "bad_then_good")

    def test_invalid_confirmations_do_not_advance_state(self):
        for index in range(9):
            with self.subTest(index=index):
                self.transfer(1, f"invalid_{index}", success=False)

    def test_bounded_failures(self):
        for mode in ("silent", "noise", "zero", "error", "no_fin_ack", "foreign_only"):
            with self.subTest(mode=mode):
                self.transfer(1, mode, success=False)

    def test_empty_file_with_zero_window(self):
        self.transfer(0, "zero")

    def test_invalid_cli_and_files(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "a.bin"
            path.write_bytes(b"a")
            commands = [[], ["localhost", "19090", str(path)],
                        ["127.0.0.1", "0", str(path)],
                        ["127.0.0.1", "65536", str(path)],
                        ["127.0.0.1", "19090", str(path), "0", "2"],
                        ["127.0.0.1", "19090", str(path), "10", "21"],
                        ["127.0.0.1", "19090", directory],
                        ["127.0.0.1", "19090", str(path) + ".missing"]]
            for args in commands:
                with self.subTest(args=args):
                    result = subprocess.run([EXECUTABLE] + args, capture_output=True, timeout=2)
                    self.assertEqual(result.returncode, 2)
            fifo = Path(directory) / "fifo"
            import os
            os.mkfifo(fifo)
            result = subprocess.run([EXECUTABLE, "127.0.0.1", "19090", str(fifo)],
                                    capture_output=True, timeout=2)
            self.assertEqual(result.returncode, 2)
            with path.open("wb") as file:
                file.truncate(1024**3 + 1)
            result = subprocess.run([EXECUTABLE, "127.0.0.1", "19090", str(path)],
                                    capture_output=True, timeout=2)
            self.assertEqual(result.returncode, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
