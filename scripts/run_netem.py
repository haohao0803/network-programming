#!/usr/bin/env python3
"""Linux-only isolated loopback netem matrix (4 scenarios x 2 windows x 3 runs).

All network mutations occur after unshare --net. Never modifies host interfaces.
This is an initial loopback experiment, not a bandwidth-contention/cwnd benchmark.
"""
import argparse
import json
import math
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys

SCENARIOS = {
    'normal': [],
    'delay': ['delay', '50ms'],
    'loss': ['loss', 'random', '1%'],
    'reorder-duplicate': ['delay', '20ms', 'reorder', '10%', 'duplicate', '1%'],
}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--file', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--seed', type=int, default=20261113)
    p.add_argument('--repeats', type=int, default=3)
    p.add_argument('--max-seconds', type=float, default=None)
    p.add_argument('--modes', nargs='+', choices=['fixed', 'adaptive'], default=['fixed'])
    p.add_argument('--timeout-ms', type=int, default=1000)
    p.add_argument('--retries', type=int, default=5)
    p.add_argument('--inside', action='store_true', help=argparse.SUPPRESS)
    a = p.parse_args()
    if platform.system() != 'Linux' or os.geteuid() != 0:
        p.error('Run on Linux with sudo; Mac cannot validate Linux netem.')
    if not 1 <= a.seed <= 4294967295 or a.repeats < 1 or (a.max_seconds is not None and
            (not math.isfinite(a.max_seconds) or a.max_seconds <= 0)):
        p.error('seed/repeats/max-seconds out of range')
    if not all(shutil.which(tool) for tool in ('unshare', 'ip', 'tc', 'ping')):
        p.error('Need unshare, ip, tc and ping (iputils-ping on Debian/Ubuntu).')
    from run_experiments import timing
    try:
        for mode in a.modes:
            if mode == 'adaptive' and a.timeout_ms < 1000:
                raise ValueError('adaptive initial RTO must be >=1000ms')
            if not 10 <= a.timeout_ms <= 10000 or not 0 <= a.retries <= 20:
                raise ValueError('invalid initial RTO or retries')
            timing(mode, a.timeout_ms, a.retries, a.max_seconds)
    except ValueError as exc:
        p.error(str(exc))
    time_options = (['--max-seconds', str(a.max_seconds)] if a.max_seconds is not None else [])
    mode_options = ['--timeout-ms', str(a.timeout_ms), '--retries', str(a.retries),
                    '--modes', *a.modes, *time_options]
    source = a.file.resolve(strict=True)
    output = a.output.resolve()
    if not a.inside:
        if output.exists():
            p.error('output must be a new directory')
        # Only the child gets a different network namespace; host lo is untouched.
        return subprocess.call(['unshare', '--net', sys.executable, str(Path(__file__).resolve()),
                                '--inside', '--file', str(source), '--output', str(output),
                                '--seed', str(a.seed), '--repeats', str(a.repeats),
                                *mode_options])
    # Even an accidental direct --inside invocation cannot change the host netns.
    if os.readlink('/proc/self/ns/net') == os.readlink('/proc/1/ns/net'):
        p.error('Refusing network changes in PID 1 network namespace')
    subprocess.run(['ip', 'link', 'set', 'lo', 'up'], check=True)
    output.mkdir(parents=True, exist_ok=False)
    root = Path(__file__).resolve().parents[1]
    failed = False
    for label, options in SCENARIOS.items():
        command = ['tc', 'qdisc', 'replace', 'dev', 'lo', 'root', 'netem', *options]
        # Fixed kernel random seed where faults use random choices. Unsupported
        # kernels/iproute2 fail explicitly instead of silently ignoring the seed.
        if label != 'normal':
            command += ['seed', str(a.seed)]
        scenario = output / label
        scenario.mkdir()
        (scenario / 'configuration.json').write_text(json.dumps({
            'topology': 'Both C endpoints in one isolated Linux network namespace on lo',
            'qdisc_command': command, 'seed': a.seed, 'modes': a.modes,
            'kernel': platform.release(),
            'note': 'qdisc applies to requests and replies; 50ms per packet gives about 100ms RTT. '
                    'Seed does not make process scheduling or packet order identical.'}, indent=2) + '\n')
        try:
            with (scenario / 'netem-setup.log').open('w') as log:
                subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True)
            with (scenario / 'ping.log').open('w') as log:
                ping = subprocess.run(['ping', '-n', '-c', '10', '-i', '0.1', '-W', '2',
                                       '127.0.0.1'], stdout=log, stderr=subprocess.STDOUT, timeout=30)
                print('exit=%d' % ping.returncode, file=log)
            # Reset qdisc after ping so counters cover the actual experiment only.
            subprocess.run(['tc', 'qdisc', 'del', 'dev', 'lo', 'root'], check=True)
            subprocess.run(command, check=True)
            with (scenario / 'tc-before.json').open('w') as log:
                subprocess.run(['tc', '-j', '-s', 'qdisc', 'show', 'dev', 'lo'],
                               stdout=log, check=True)
            with (scenario / 'runner.log').open('w') as log:
                r = subprocess.run([sys.executable, str(root / 'scripts/run_experiments.py'),
                                    '--sender', str(root / 'build/rudp-send'),
                                    '--receiver', str(root / 'build/rudp-recv'),
                                    '--file', str(source), '--output', str(scenario / 'runs'),
                                    '--scenario', label, '--repeats', str(a.repeats),
                                    *mode_options,
                                    '--network-note', 'Isolated Linux lo netem; see configuration.json'],
                                   stdout=log, stderr=subprocess.STDOUT)
                failed = failed or r.returncode != 0
            with (scenario / 'tc-after.json').open('w') as log:
                subprocess.run(['tc', '-j', '-s', 'qdisc', 'show', 'dev', 'lo'],
                               stdout=log, check=True)
        except (OSError, subprocess.SubprocessError) as exc:
            (scenario / 'ERROR.txt').write_text(str(exc) + '\n')
            print('FAIL %s: %s' % (label, exc), flush=True)
            failed = True
        finally:
            subprocess.run(['tc', 'qdisc', 'del', 'dev', 'lo', 'root'],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
