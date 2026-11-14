#!/usr/bin/env python3
"""Paced UDP interference and sink; payload rate is not TCP fairness evidence."""
import argparse
import csv
import json
from pathlib import Path
import signal
import socket
import time


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('role', choices=['send', 'sink'])
    p.add_argument('--host', required=True)
    p.add_argument('--port', type=int, default=19091)
    p.add_argument('--mbps', type=float, default=7)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    if not 0 < a.mbps <= 1000 or not 1 <= a.port <= 65535:
        p.error('invalid rate or port')
    stop = False
    def finish(*_):
        nonlocal stop
        stop = True
    signal.signal(signal.SIGTERM, finish)
    signal.signal(signal.SIGINT, finish)
    count = total = errors = 0
    packet = bytes(1200)
    start = time.monotonic()
    last = start
    tokens = 0.0
    next_sample = 1
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock, \
            a.output.with_suffix('.csv').open('x', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(['elapsed_s', 'payload_bytes', 'packets', 'send_errors'])
        if a.role == 'sink':
            sock.bind((a.host, a.port))
            sock.settimeout(.1)
        else:
            sock.setblocking(False)
        print('READY competition-' + a.role, flush=True)
        while not stop:
            now = time.monotonic()
            if a.role == 'sink':
                try:
                    data, _ = sock.recvfrom(65535)
                    total += len(data)
                    count += 1
                except socket.timeout:
                    pass
            else:
                # Limit catch-up bursts to four datagrams after scheduling pauses.
                tokens = min(4800, tokens + (now - last) * a.mbps * 1e6 / 8)
                last = now
                if tokens < len(packet):
                    time.sleep(min(.005, (len(packet) - tokens) * 8 / (a.mbps * 1e6)))
                    continue
                tokens -= len(packet)
                try:
                    total += sock.sendto(packet, (a.host, a.port))
                    count += 1
                except (BlockingIOError, OSError):
                    errors += 1
            if now - start >= next_sample:
                writer.writerow([now - start, total, count, errors])
                stream.flush()
                next_sample = now - start + 1
        elapsed = time.monotonic() - start
        writer.writerow([elapsed, total, count, errors])
    a.output.write_text(json.dumps({'role': a.role, 'start_monotonic_s': start,
        'end_monotonic_s': time.monotonic(), 'elapsed_s': elapsed,
        'payload_bytes': total, 'packets': count, 'send_errors': errors,
        'payload_mbps': total * 8 / elapsed / 1e6,
        'requested_mbps': a.mbps if a.role == 'send' else None}, indent=2) + '\n')


if __name__ == '__main__':
    main()
