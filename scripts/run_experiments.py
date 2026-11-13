#!/usr/bin/env python3
"""Run real local C endpoints; collect JSONL evidence, without simulating a peer.

Network impairments, when desired, must be applied by the external Linux harness.
Sender elapsed time excludes receiver FIN linger. Failed transfers are retained.
"""
import argparse
import datetime
import json
import os
from pathlib import Path
import platform
import re
import selectors
import socket
import subprocess
import time

from file_tools import sha256


def git_value(*args):
    result = subprocess.run(['git', *args], capture_output=True, text=True, timeout=10)
    return result.stdout.strip() if result.returncode == 0 else None


def ready(process, log, timeout=5):
    # READY is flushed and is the first line. Read one byte at a time to avoid
    # blocking on an unterminated line or losing data in a buffered text reader.
    deadline = time.monotonic() + timeout
    line = bytearray()
    with selectors.DefaultSelector() as watcher:
        watcher.register(process.stdout, selectors.EVENT_READ)
        while time.monotonic() < deadline:
            if not watcher.select(max(0, deadline - time.monotonic())):
                break
            byte = os.read(process.stdout.fileno(), 1)
            if not byte:
                raise RuntimeError('receiver exited before READY')
            log.write(byte)
            line.extend(byte)
            if byte == b'\n':
                if line.startswith(b'READY '):
                    return
                line.clear()
    raise RuntimeError('receiver READY timeout')


def metric(text, name):
    found = re.search(r'\b' + re.escape(name) + r'=(\d+)\b', text)
    return int(found[1]) if found else None


def run(args):
    args.output.mkdir(parents=True, exist_ok=False)
    source = args.file.resolve()
    expected_hash = sha256(source)
    metadata = {
        'started_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'platform': platform.platform(), 'python': platform.python_version(),
        'git_commit': git_value('rev-parse', 'HEAD'),
        'git_status': git_value('status', '--porcelain'),
        'sender_sha256': sha256(args.sender), 'receiver_sha256': sha256(args.receiver),
        'source_sha256': expected_hash, 'source_bytes': source.stat().st_size,
        'scenario': args.scenario, 'network_note': args.network_note,
        'parameters': {k: str(v) if isinstance(v, Path) else v
                       for k, v in vars(args).items()},
    }
    (args.output / 'metadata.json').write_text(json.dumps(metadata, indent=2) + '\n')
    all_passed = True
    # Retain one saved file at a time to bound storage use on the small server.
    with (args.output / 'runs.jsonl').open('x', encoding='utf-8') as results:
        for window in args.windows:
            for repeat in range(1, args.repeats + 1):
                name = 'w%d-r%d' % (window, repeat)
                target = (args.output / (name + '.received')).resolve()
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as picker:
                    picker.bind(('127.0.0.1', 0))
                    port = picker.getsockname()[1]
                linger = (args.retries + 1) * args.timeout_ms + 1000
                receiver_cmd = [str(args.receiver.resolve()), '127.0.0.1', str(port),
                                str(target), str(args.receive_window),
                                str(min(600000, max(15000, linger * 2))), str(linger)]
                sender_cmd = [str(args.sender.resolve()), '127.0.0.1', str(port),
                              str(source), str(args.timeout_ms), str(args.retries), str(window)]
                record = {'run_id': name, 'scenario': args.scenario, 'window': window,
                          'repeat': repeat, 'source_bytes': source.stat().st_size,
                          'source_sha256': expected_hash, 'sender_command': sender_cmd,
                          'receiver_command': receiver_cmd, 'sender_exit': None,
                          'receiver_exit': None, 'sender_seconds': None,
                          'output_sha256': None, 'goodput_Bps': None, 'success': False}
                receiver = None
                output = ''
                error = ''
                with (args.output / (name + '.receiver.log')).open('wb') as recv_log:
                    try:
                        receiver = subprocess.Popen(receiver_cmd, stdout=subprocess.PIPE,
                                                    stderr=subprocess.STDOUT)
                        ready(receiver, recv_log)
                        start = time.monotonic()
                        sender = subprocess.run(sender_cmd, capture_output=True, text=True,
                                                timeout=args.max_seconds)
                        record['sender_seconds'] = time.monotonic() - start
                        record['sender_exit'] = sender.returncode
                        output, error = sender.stdout, sender.stderr
                        tail, _ = receiver.communicate(timeout=linger / 1000 + 20)
                        recv_log.write(tail)
                        record['receiver_exit'] = receiver.returncode
                        if target.is_file():
                            record['output_sha256'] = sha256(target)
                        record['success'] = (sender.returncode == receiver.returncode == 0 and
                                             record['output_sha256'] == expected_hash and
                                             sha256(source) == expected_hash)
                        record['retransmissions'] = metric(output, 'retransmissions')
                        record['peak_inflight'] = metric(output, 'peak_inflight')
                        if record['success']:
                            record['goodput_Bps'] = record['source_bytes'] / record['sender_seconds']
                    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
                        record['error'] = str(exc)
                        if isinstance(exc, subprocess.TimeoutExpired):
                            output = exc.output or b''
                            error = exc.stderr or b''
                            output = output.decode(errors='replace') if isinstance(output, bytes) else output
                            error = error.decode(errors='replace') if isinstance(error, bytes) else error
                    finally:
                        if receiver is not None:
                            if receiver.poll() is None:
                                receiver.terminate()
                                try:
                                    tail, _ = receiver.communicate(timeout=3)
                                except subprocess.TimeoutExpired:
                                    receiver.kill()
                                    tail, _ = receiver.communicate(timeout=3)
                                recv_log.write(tail)
                            record['receiver_exit'] = receiver.returncode
                            receiver.stdout.close()
                (args.output / (name + '.sender.log')).write_text(output + error, encoding='utf-8')
                results.write(json.dumps(record, ensure_ascii=False) + '\n')
                results.flush()
                print('%s success=%s sender_seconds=%s' %
                      (name, record['success'], record['sender_seconds']), flush=True)
                all_passed = all_passed and record['success']
                if record['success'] and not args.keep_files:
                    target.unlink()
    return 0 if all_passed else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sender', type=Path, default=Path('build/rudp-send'))
    parser.add_argument('--receiver', type=Path, default=Path('build/rudp-recv'))
    parser.add_argument('--file', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--windows', nargs='+', type=int, default=[1, 16])
    parser.add_argument('--receive-window', type=int, default=64)
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--timeout-ms', type=int, default=1000)
    parser.add_argument('--retries', type=int, default=5)
    parser.add_argument('--max-seconds', type=float, default=600)
    parser.add_argument('--scenario', default='normal')
    parser.add_argument('--network-note', default='Unmodified local loopback; not a WAN benchmark')
    parser.add_argument('--keep-files', action='store_true')
    args = parser.parse_args()
    if (not all(1 <= w <= 256 for w in args.windows) or
            not 1 <= args.receive_window <= 256 or args.repeats < 1 or
            not 10 <= args.timeout_ms <= 10000 or not 0 <= args.retries <= 20 or
            args.max_seconds <= 0):
        parser.error('invalid window, repeat, timeout or retry configuration')
    try:
        return run(args)
    except OSError as exc:
        parser.exit(2, 'ERROR: %s\n' % exc)


if __name__ == '__main__':
    raise SystemExit(main())
