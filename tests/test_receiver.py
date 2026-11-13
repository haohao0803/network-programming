#!/usr/bin/env python3
"""Black-box checks of the real receiver using an independent wire client.

Run: python3 tests/test_receiver.py /absolute/path/to/rudp-recv
Short linger values here bound direct protocol tests. Real sender integration
must separately match the sender's timeout/retry budget.
"""

import hashlib
import os
from pathlib import Path
import re
import resource
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "work"
WORK.mkdir(exist_ok=True)
os.environ.setdefault("TMPDIR", str(WORK))
tempfile.tempdir = os.environ["TMPDIR"]
EXECUTABLE = str(Path(sys.argv.pop(1)).resolve())
WIRE = struct.Struct("!4sBBHIIHH")
IDENT = 0x10203040
START, START_ACK, DATA, ACK, FIN, FIN_ACK, ERROR = range(1, 8)


def packet(kind, seq=0, payload=b"", ident=IDENT, window=0):
    return WIRE.pack(b"RUDP", 1, kind, 0, ident, seq, len(payload), window) + payload


def start_packet(size, ident=IDENT):
    return packet(START, payload=struct.pack("!IQ", (size + 1023) // 1024, size), ident=ident)


def contents(size):
    # Distinct chunk contents make incorrect reordering visible in the hash.
    return hashlib.shake_256(b"rudp receiver contract payload").digest(size)


def write_limit():
    signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
    resource.setrlimit(resource.RLIMIT_FSIZE, (512, 512))


class Receiver:
    """Own one receiver, its UDP client, and all process/log resources."""

    def __init__(self, directory, window=4, idle=800, linger=40, preexec=None):
        self.directory = Path(directory)
        self.output = self.directory / "received with spaces.bin"
        self.log = self.directory / "receiver.log"
        self.window, self.idle, self.linger = window, idle, linger
        self.preexec = preexec
        self.process = None
        self.log_handle = None
        self.client = None
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as picker:
            picker.bind(("127.0.0.1", 0))
            self.port = picker.getsockname()[1]

    def __enter__(self):
        try:
            self.log_handle = self.log.open("wb")
            self.process = subprocess.Popen(
                [EXECUTABLE, "127.0.0.1", str(self.port), str(self.output),
                 str(self.window), str(self.idle), str(self.linger)],
                stdout=self.log_handle, stderr=subprocess.STDOUT, preexec_fn=self.preexec)
            self.client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.client.bind(("127.0.0.1", 0))
            self.client.settimeout(0.3)
            self.await_log("READY ")
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *_):
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(2)
        if self.client is not None:
            self.client.close()
        if self.log_handle is not None:
            self.log_handle.close()

    def text(self):
        return self.log.read_text(errors="replace")

    def await_log(self, needle, timeout=2):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            text = self.text()
            if needle in text:
                return text
            if self.process.poll() is not None:
                raise AssertionError(f"receiver exited before {needle!r}: {text}")
            time.sleep(0.005)
        raise AssertionError(f"missing {needle!r}: {self.text()}")

    def send(self, data, client=None):
        (client or self.client).sendto(data, ("127.0.0.1", self.port))

    def receive(self, kind, seq, window=None, timeout=0.5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.client.settimeout(max(0.001, deadline - time.monotonic()))
            try:
                data, address = self.client.recvfrom(65535)
            except socket.timeout:
                break
            if len(data) < WIRE.size:
                raise AssertionError(f"short receiver reply: {data!r}")
            magic, version, got_kind, reserved, ident, got_seq, length, got_window = WIRE.unpack(data[:20])
            if (magic, version, reserved, ident) != (b"RUDP", 1, 0, IDENT):
                raise AssertionError(f"invalid reply: {data!r}")
            if address != ("127.0.0.1", self.port) or len(data) != 20 + length:
                raise AssertionError(f"reply framing/address mismatch: {data!r} {address}")
            if got_kind == kind and got_seq == seq and (window is None or got_window == window):
                if kind != ERROR and length:
                    raise AssertionError("confirmation contains unexpected payload")
                return got_window, data[20:]
        raise AssertionError(f"missing kind={kind} seq={seq} window={window}: {self.text()}")

    def exchange(self, data, kind, seq, window=None):
        self.send(data)
        return self.receive(kind, seq, window)

    def begin(self, size, window=None):
        return self.exchange(start_packet(size), START_ACK, 0, window)

    def part(self, data, seq, ack=None, window=None):
        return self.exchange(packet(DATA, seq, data[seq * 1024:(seq + 1) * 1024]),
                             ACK, seq + 1 if ack is None else ack, window)

    def finish(self, data):
        chunks = (len(data) + 1023) // 1024
        self.exchange(packet(FIN, chunks), FIN_ACK, chunks)
        self.wait(0)
        actual = self.output.read_bytes()
        if hashlib.sha256(actual).digest() != hashlib.sha256(data).digest():
            raise AssertionError("saved-file SHA-256 differs from source")
        if len(actual) != len(data):
            raise AssertionError("saved-file length differs from source")
        self.no_temporary_files()

    def wait(self, expected, timeout=2):
        result = self.process.wait(timeout)
        if result != expected:
            raise AssertionError(f"exit {result}, expected {expected}: {self.text()}")
        return self.text()

    def no_temporary_files(self):
        unexpected = set(self.directory.iterdir()) - {self.output, self.log}
        if unexpected:
            raise AssertionError(f"temporary files remain: {unexpected}")

    def pause(self, base):
        self.process.send_signal(signal.SIGUSR1)
        self.await_log(f"WINDOW paused=1 base={base} window=0")

    def resume(self, base, window):
        self.process.send_signal(signal.SIGUSR2)
        self.await_log(f"WINDOW paused=0 base={base} window={window}")


class ReceiverTests(unittest.TestCase):
    def test_saved_file_hashes_and_boundary_lengths(self):
        for size in (0, 1, 1024, 1025, 64 * 1024 + 13):
            with self.subTest(size=size), tempfile.TemporaryDirectory() as directory, Receiver(directory) as peer:
                data = contents(size)
                peer.begin(size, min(4, (size + 1023) // 1024))
                for seq in range((size + 1023) // 1024):
                    peer.part(data, seq)
                self.assertFalse(peer.output.exists(), "final file published before FIN")
                peer.finish(data)

    def test_reordering_duplicates_and_window_span(self):
        data = contents(6 * 1024 + 13)
        with tempfile.TemporaryDirectory() as directory, Receiver(directory, window=4) as peer:
            peer.begin(len(data), 4)
            peer.part(data, 4, ack=0, window=4)  # right edge excluded
            peer.part(data, 3, ack=0, window=4)
            peer.part(data, 2, ack=0, window=4)
            peer.part(data, 3, ack=0, window=4)  # duplicate still in cache
            peer.part(data, 1, ack=0, window=4)
            peer.part(data, 0, ack=4, window=3)  # must not count cached slot as free span
            peer.part(data, 0, ack=4, window=3)  # already written duplicate
            peer.part(data, 5, ack=4, window=3)
            peer.part(data, 4, ack=6, window=1)
            peer.part(data, 6, ack=7, window=0)
            peer.finish(data)
            peak = re.search(r"peak_buffered=(\d+)", peer.text())
            self.assertIsNotNone(peak, peer.text())
            self.assertLessEqual(int(peak[1]), 4)
            self.assertGreaterEqual(int(peak[1]), 3)

    def test_duplicate_start_and_conflicting_metadata_preserve_progress(self):
        data = contents(2049)
        with tempfile.TemporaryDirectory() as directory, Receiver(directory) as peer:
            peer.begin(len(data))
            peer.part(data, 0)
            peer.exchange(start_packet(len(data)), START_ACK, 0)
            peer.send(start_packet(len(data) + 1024))
            peer.send(packet(START, payload=struct.pack("!IQ", 3, 1)))
            peer.part(data, 0, ack=1)
            peer.part(data, 2, ack=1)
            peer.part(data, 1, ack=3)
            peer.finish(data)

    def test_malformed_and_foreign_packets_preserve_session(self):
        data = contents(1025)
        valid = packet(DATA, 0, data[:1024])
        invalid = [
            b"", b"RUDP", b"NOPE" + valid[4:], valid[:4] + b"\x02" + valid[5:],
            valid[:5] + b"\xff" + valid[6:], valid[:6] + b"\0\1" + valid[8:],
            valid[:8] + struct.pack("!I", IDENT + 1) + valid[12:],
            valid[:16] + b"\x04\x01" + valid[18:],
            valid[:18] + b"\0\1" + valid[20:], valid + b"tail", valid[:-1],
            valid + b"x" * 4000, packet(DATA, 2, b"x"),
            packet(DATA, 1, b"too long"), packet(START_ACK), packet(ACK),
        ]
        with tempfile.TemporaryDirectory() as directory, Receiver(directory) as peer:
            # Invalid handshakes must not reserve the active session.
            peer.send(packet(START, payload=struct.pack("!IQ", 1, 2**30 + 1)))
            peer.send(packet(START, 1, struct.pack("!IQ", 2, len(data))))
            peer.send(packet(START, payload=struct.pack("!IQ", 99, len(data))))
            peer.begin(len(data))
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as stranger:
                stranger.bind(("127.0.0.1", 0))
                peer.send(valid, stranger)
                peer.send(start_packet(0), stranger)
            for bad in invalid:
                peer.send(bad)
            peer.part(data, 1, ack=0)
            peer.part(data, 0, ack=2)
            peer.finish(data)

    def test_early_and_invalid_fin_do_not_publish(self):
        data = contents(1025)
        with tempfile.TemporaryDirectory() as directory, Receiver(directory) as peer:
            peer.begin(len(data))
            peer.exchange(packet(FIN, 2), ACK, 0)
            self.assertFalse(peer.output.exists())
            peer.send(packet(FIN, 1))
            peer.send(packet(FIN, 2, b"x"))
            peer.part(data, 0)
            peer.exchange(packet(FIN, 2), ACK, 1)
            self.assertFalse(peer.output.exists())
            peer.part(data, 1)
            peer.finish(data)

    def test_repeated_fin_is_confirmed_without_extending_linger(self):
        with tempfile.TemporaryDirectory() as directory, Receiver(directory, linger=250) as peer:
            peer.begin(0)
            started = time.monotonic()
            peer.exchange(packet(FIN, 0), FIN_ACK, 0)
            replies = 1
            while peer.process.poll() is None and time.monotonic() - started < 0.2:
                peer.exchange(packet(FIN, 0), FIN_ACK, 0)
                replies += 1
                time.sleep(0.02)
            peer.wait(0, timeout=0.4)
            self.assertGreater(replies, 2)
            self.assertLess(time.monotonic() - started, 0.6)
            self.assertEqual(peer.output.read_bytes(), b"")
            peer.no_temporary_files()

    def test_initial_zero_window_probes_and_recovery(self):
        data = contents(1025)
        with tempfile.TemporaryDirectory() as directory, Receiver(directory) as peer:
            peer.pause(0)
            peer.begin(len(data), 0)
            peer.exchange(start_packet(len(data)), START_ACK, 0, 0)
            peer.part(data, 0, ack=0, window=0)
            peer.resume(0, 2)
            peer.exchange(start_packet(len(data)), START_ACK, 0, 2)
            peer.part(data, 0)
            peer.part(data, 1)
            peer.finish(data)

    def test_mid_transfer_zero_window_retains_cache_and_deduplicates_probes(self):
        data = contents(3 * 1024)
        with tempfile.TemporaryDirectory() as directory, Receiver(directory) as peer:
            peer.begin(len(data))
            peer.part(data, 0)
            peer.part(data, 2, ack=1)
            peer.pause(1)
            peer.part(data, 0, ack=1, window=0)
            peer.part(data, 1, ack=1, window=0)
            peer.exchange(start_packet(len(data)), START_ACK, 0, 0)
            peer.resume(1, 2)
            peer.part(data, 0, ack=1, window=2)
            peer.part(data, 1, ack=3, window=0)
            peer.finish(data)

    def test_paused_empty_and_complete_files_still_finish(self):
        for size in (0, 1):
            with self.subTest(size=size), tempfile.TemporaryDirectory() as directory, Receiver(directory) as peer:
                data = contents(size)
                if size == 0:
                    peer.pause(0)
                peer.begin(size)
                if size:
                    peer.part(data, 0)
                    peer.pause(1)
                peer.finish(data)

    def test_noise_duplicate_requests_and_signals_do_not_extend_idle_deadline(self):
        for mode in ("no_session", "repeated_start", "duplicate_data", "paused_probes"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory, Receiver(directory, idle=250) as peer:
                data = contents(2048)
                if mode != "no_session":
                    peer.begin(len(data))
                if mode == "duplicate_data":
                    peer.part(data, 0)
                if mode == "paused_probes":
                    peer.pause(0)
                started = time.monotonic()
                while peer.process.poll() is None and time.monotonic() - started < 0.8:
                    peer.send(b"noise" if mode == "no_session" else
                              packet(DATA, 0, data[:1024]) if mode == "duplicate_data" else
                              start_packet(len(data)))
                    if mode == "paused_probes":
                        peer.process.send_signal(signal.SIGUSR1)
                    time.sleep(0.015)
                peer.wait(1, timeout=0.2)
                self.assertLess(time.monotonic() - started, 0.65)
                self.assertFalse(peer.output.exists())
                peer.no_temporary_files()

    def test_disappearing_peer_cleans_partial_file(self):
        with tempfile.TemporaryDirectory() as directory, Receiver(directory, idle=120) as peer:
            peer.begin(2048)
            peer.part(contents(2048), 0)
            peer.wait(1)
            self.assertFalse(peer.output.exists())
            peer.no_temporary_files()

    def test_termination_cleans_partial_file(self):
        with tempfile.TemporaryDirectory() as directory, Receiver(directory) as peer:
            peer.begin(2048)
            peer.part(contents(2048), 0)
            peer.process.terminate()
            peer.wait(1)
            self.assertFalse(peer.output.exists())
            peer.no_temporary_files()

    def test_existing_target_and_invalid_parent_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            existing = Path(directory) / "existing.bin"
            existing.write_bytes(b"keep existing")
            targets = (existing, Path(directory) / "missing" / "file.bin", Path(directory))
            for target in targets:
                with self.subTest(target=target):
                    result = subprocess.run([EXECUTABLE, "127.0.0.1", "19090", str(target), "4", "100", "40"],
                                            capture_output=True, text=True, timeout=2)
                    self.assertEqual(result.returncode, 1, result.stderr)
            self.assertEqual(existing.read_bytes(), b"keep existing")
            self.assertEqual(list(Path(directory).iterdir()), [existing])

    def test_target_created_during_transfer_is_preserved(self):
        data = contents(1025)
        with tempfile.TemporaryDirectory() as directory, Receiver(directory) as peer:
            peer.begin(len(data))
            peer.part(data, 0)
            peer.output.write_bytes(b"created by another process")
            peer.part(data, 1)
            peer.send(packet(FIN, 2))
            peer.receive(ERROR, 2)
            peer.wait(1)
            self.assertEqual(peer.output.read_bytes(), b"created by another process")
            peer.no_temporary_files()

    def test_write_failure_reports_error_and_cleans_partial_file(self):
        with tempfile.TemporaryDirectory() as directory, Receiver(directory, preexec=write_limit) as peer:
            peer.begin(1024)
            peer.send(packet(DATA, 0, contents(1024)))
            peer.receive(ERROR, 1)
            peer.wait(1)
            self.assertFalse(peer.output.exists())
            peer.no_temporary_files()

    def test_invalid_cli_and_occupied_port(self):
        with tempfile.TemporaryDirectory() as directory:
            target = str(Path(directory) / "output.bin")
            base = ["127.0.0.1", "19090", target]
            bad_commands = [[], base[:2], ["localhost", "19090", target],
                            ["127.0.0.1", "0", target], ["127.0.0.1", "65536", target],
                            base + ["0"], base + ["257"], base + ["-1"], base + ["4x"],
                            base + ["4", "0"], base + ["4", "600001"],
                            base + ["4", "100", "0"], base + ["4", "100", "600001"],
                            base + ["4", "100", "40", "extra"]]
            for command in bad_commands:
                with self.subTest(command=command):
                    result = subprocess.run([EXECUTABLE] + command, capture_output=True, timeout=2)
                    self.assertEqual(result.returncode, 2, result.stderr)
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as occupied:
                occupied.bind(("127.0.0.1", 0))
                result = subprocess.run([EXECUTABLE, "127.0.0.1", str(occupied.getsockname()[1]), target],
                                        capture_output=True, timeout=2)
                self.assertEqual(result.returncode, 1, result.stderr)
            self.assertEqual(list(Path(directory).iterdir()), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
