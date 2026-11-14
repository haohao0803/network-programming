#!/usr/bin/env python3
"""haohao0803 sender integration contracts; peers are fixtures, not receivers."""
import csv
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time
import unittest

from test_sender import EXECUTABLE, Peer, WIRE
from test_sender_window import WindowPeer


class NoisePeer(Peer):
    def serve(self):
        self.sock.settimeout(2)
        packet, address = self.sock.recvfrom(65535)
        ident = WIRE.unpack(packet[:20])[4]
        window = 0 if self.mode == "persist" else 8
        self.sock.sendto(WIRE.pack(b"RUDP", 1, 2, 0, ident, 0, 0, window), address)
        if self.mode != "persist":
            packet, source = self.sock.recvfrom(65535)
            assert source == address and packet[5] == 3
        count = 0
        while not self.stop.is_set():
            # Valid duplicate notifications, future ACKs, foreign IDs and bad
            # framing must neither grant progress nor renew the DATA deadline.
            kind = 2 if self.mode == "persist" else 4
            advertised = 0 if self.mode == "persist" else 1 + count % 2
            for reply in (
                WIRE.pack(b"RUDP", 1, kind, 0, ident, 0, 0, advertised),
                WIRE.pack(b"RUDP", 1, 4, 0, ident, 999, 0, 8),
                WIRE.pack(b"RUDP", 1, 4, 0, ident ^ 1, 1, 0, 8),
                b"irrelevant",
            ):
                self.sock.sendto(reply, address)
            count += 1
            # Drain requests so the fixture itself does not create queue loss.
            self.sock.setblocking(False)
            try:
                while True:
                    packet, source = self.sock.recvfrom(65535)
                    assert source == address and packet[5] != 5
            except BlockingIOError:
                pass
            finally:
                self.sock.settimeout(0.05)
            time.sleep(0.005)


class ControlPeer(Peer):
    def serve(self):
        counts = {}
        while not self.stop.is_set():
            try:
                packet, address = self.sock.recvfrom(65535)
            except socket.timeout:
                continue
            _, _, kind, _, ident, seq, length, _ = WIRE.unpack(packet[:20])
            key = kind, seq
            counts[key] = counts.get(key, 0) + 1
            if kind == 1:
                reply_kind, ack = 2, 0
            elif kind == 3:
                assert seq == 0 and length == 1
                if counts[key] == 1:
                    continue
                self.received = bytearray(packet[20:])
                reply_kind, ack = 4, 1
            else:
                assert kind == 5 and seq == 1
                if counts[key] == 1:
                    continue
                self.finished = True
                reply_kind, ack = 6, 1
            self.sock.sendto(WIRE.pack(b"RUDP", 1, reply_kind, 0, ident, ack, 0, 8), address)


class AdaptiveSenderContracts(unittest.TestCase):
    def invoke(self, peer, name, success, size=1):
        with tempfile.TemporaryDirectory() as directory:
            source, trace = Path(directory) / "input", Path(directory) / "trace.csv"
            source.write_bytes(bytes(range(256)) * (size // 256) + bytes(range(size % 256)))
            started = time.monotonic()
            result = subprocess.run(
                [EXECUTABLE, "127.0.0.1", str(peer.port), str(source),
                 "1000", "1", "8", "--adaptive", "--trace", str(trace)],
                capture_output=True, text=True, timeout=9)
            seconds = time.monotonic() - started
            with trace.open(newline="") as stream:
                rows = list(csv.DictReader(stream))
            evidence = os.environ.get("RUDP_SENDER_CONTRACT_EVIDENCE")
            if evidence:
                root = Path(evidence)
                root.mkdir(parents=True, exist_ok=True)
                (root / (name + ".csv")).write_bytes(trace.read_bytes())
                (root / (name + ".json")).write_text(json.dumps({
                    "exit": result.returncode, "seconds": seconds,
                    "stdout": result.stdout, "stderr": result.stderr,
                    "source_hex": source.read_bytes().hex(),
                    "fixture_received_hex": bytes(peer.received).hex(),
                    "peer_is_fixture": True,
                }, indent=2) + "\n")
            self.assertEqual(result.returncode, 0 if success else 1, result.stderr)
            self.assertEqual(rows[-1]["event"], "complete" if success else "failed")
            if success:
                self.assertEqual(bytes(peer.received), source.read_bytes())
                self.assertTrue(peer.finished)
            return rows, seconds

    def test_noise_and_window_notifications_do_not_extend_deadline(self):
        for mode in ("data", "persist"):
            with self.subTest(mode=mode), NoisePeer(mode) as peer:
                rows, seconds = self.invoke(peer, "noise-" + mode, False)
                # One initial 1 s wait plus one backed-off 2 s wait. Allow
                # scheduling slack; continuous valid notifications keep arriving.
                self.assertGreaterEqual(seconds, 2.8)
                self.assertLess(seconds, 5.5)
                self.assertFalse(any(r["event"] == "ack" for r in rows))
                self.assertTrue(all(float(r["cwnd_packets"]) == 1 for r in rows))
                self.assertTrue(all(float(r["srtt_ms"]) == 0 for r in rows))
                timeouts = [r for r in rows if r["event"] == "timeout"]
                self.assertEqual(len(timeouts), 1)
                self.assertEqual(int(timeouts[0]["rto_ms"]), 2000)

    def test_recovery_obeys_reduced_receive_window(self):
        with WindowPeer("shrink", 8, 8) as peer:
            rows, _ = self.invoke(peer, "shrink", True, size=32781)
            retries = [r for r in rows if r["event"] == "retransmit" and r["packet_type"] == "3"]
            self.assertTrue(retries)
            for row in retries:
                self.assertLess(int(row["sequence"]) - int(row["base"]),
                                int(row["effective_window_packets"]))
            self.assertGreater(peer.repeated_data, 0)

    def test_fin_uses_fixed_timer_after_data_backoff(self):
        with ControlPeer() as peer:
            rows, _ = self.invoke(peer, "control-timer", True)
            recovered = next(r for r in rows if r["event"] == "ack")
            self.assertEqual(int(recovered["rto_ms"]), 2000)
            self.assertEqual(int(recovered["rtt_sample_ms"]), -1)
            fin = [r for r in rows if r["packet_type"] == "5"]
            self.assertEqual([r["event"] for r in fin], ["send", "timeout", "retransmit"])
            self.assertTrue(all(int(r["rto_ms"]) == 1000 for r in fin))


if __name__ == "__main__":
    unittest.main(verbosity=2)
