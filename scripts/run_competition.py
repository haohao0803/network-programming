#!/usr/bin/env python3
"""One isolated veth/HTB experiment with optional shared-link UDP interference."""
import argparse
import json
import os
from pathlib import Path
import platform
import shutil
import signal
import subprocess
import sys
import time

from run_experiments import ready

ROOT = Path(__file__).resolve().parents[1]


def stop(process, log):
    if process is None:
        return
    if process.poll() is None:
        process.terminate()
    try:
        tail, _ = process.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        tail, _ = process.communicate(timeout=5)
    if tail:
        log.write(tail)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--file', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--mode', choices=['fixed', 'adaptive'], required=True)
    p.add_argument('--load-mbps', type=float, choices=[0, 7, 12], required=True)
    p.add_argument('--seed', type=int, required=True)
    p.add_argument('--inside', action='store_true', help=argparse.SUPPRESS)
    a = p.parse_args()
    if platform.system() != 'Linux' or os.geteuid() != 0:
        p.error('requires Linux root; network changes run only inside unshare --net')
    if not all(shutil.which(t) for t in ('unshare', 'nsenter', 'ip', 'tc', 'ping')):
        p.error('missing unshare/nsenter/ip/tc/ping')
    source = a.file.resolve(strict=True)
    output = a.output.resolve()
    if not 1 <= a.seed <= 4294967295 or output.exists():
        p.error('invalid seed or output already exists')
    if not a.inside:
        return subprocess.call(['unshare', '--net', sys.executable, str(Path(__file__).resolve()),
            '--inside', '--file', str(source), '--output', str(output), '--mode', a.mode,
            '--load-mbps', str(a.load_mbps), '--seed', str(a.seed)])
    if os.readlink('/proc/self/ns/net') == os.readlink('/proc/1/ns/net'):
        p.error('refusing changes in PID 1 network namespace')
    def interrupted(signum, _):
        raise SystemExit(128 + signum)
    signal.signal(signal.SIGTERM, interrupted)
    output.mkdir(parents=True)
    holder = sink = generator = None
    commands = []
    def run(command, name):
        commands.append(command)
        with (output / (name + '.log')).open('ab') as f:
            subprocess.run(command, stdout=f, stderr=subprocess.STDOUT, check=True, timeout=30)
    try:
        with (output / 'holder.log').open('wb') as holder_log, \
                (output / 'sink.log').open('wb') as sink_log, \
                (output / 'generator.log').open('wb') as generator_log:
            try:
                holder = subprocess.Popen(['unshare', '--net', sys.executable, '-c',
                    'import sys; print("READY namespace",flush=True); sys.stdin.read()'],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
                ready(holder, holder_log)
                peer = ['nsenter', '-t', str(holder.pid), '-n', '--']
                run(['ip', 'link', 'set', 'lo', 'up'], 'topology')
                run(['ip', 'link', 'add', 'tx', 'type', 'veth', 'peer', 'name', 'rx'], 'topology')
                run(['ip', 'link', 'set', 'rx', 'netns', str(holder.pid)], 'topology')
                for prefix, device, address in (([], 'tx', '10.231.0.1/30'),
                                                (peer, 'rx', '10.231.0.2/30')):
                    run(prefix + ['ip', 'addr', 'add', address, 'dev', device], 'topology')
                    run(prefix + ['ip', 'link', 'set', device, 'up'], 'topology')
                    run(prefix + ['tc', 'qdisc', 'add', 'dev', device, 'root', 'handle', '1:',
                                  'htb', 'default', '10'], 'qdisc')
                    run(prefix + ['tc', 'class', 'add', 'dev', device, 'parent', '1:',
                                  'classid', '1:10', 'htb', 'rate', '10mbit', 'ceil', '10mbit'], 'qdisc')
                    run(prefix + ['tc', 'qdisc', 'add', 'dev', device, 'parent', '1:10',
                                  'handle', '10:', 'netem', 'limit', '100', 'delay', '10ms',
                                  'seed', str(a.seed)], 'qdisc')
                run(['ping', '-n', '-c', '5', '-i', '.1', '10.231.0.2'], 'ping')
                for prefix, dev in (([], 'tx'), (peer, 'rx')):
                    run(prefix + ['tc', '-j', '-s', 'qdisc', 'show', 'dev', dev], dev + '-before')
                if a.load_mbps:
                    traffic = str(ROOT / 'scripts/competition_traffic.py')
                    sink = subprocess.Popen(peer + [sys.executable, traffic, 'sink', '--host',
                        '10.231.0.2', '--output', str(output / 'sink.json')],
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
                    ready(sink, sink_log)
                    generator = subprocess.Popen([sys.executable, traffic, 'send', '--host',
                        '10.231.0.2', '--mbps', str(a.load_mbps), '--output', str(output / 'generator.json')],
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
                    ready(generator, generator_log)
                    time.sleep(1)  # bounded warmup, independently logged by both peers
                with (output / 'runner.log').open('w') as log:
                    experiment = subprocess.run([sys.executable, str(ROOT / 'scripts/run_experiments.py'),
                        '--file', str(source), '--output', str(output / 'runs'), '--modes', a.mode,
                        '--windows', '64', '--repeats', '1', '--max-seconds', '1800',
                        '--keep-files', '--host', '10.231.0.2',
                        '--receiver-bind', '10.231.0.2', '--receiver-netns-pid', str(holder.pid),
                        '--scenario', 'competition-%gmbps' % a.load_mbps,
                        '--network-note', 'veth HTB shared FIFO 10Mbit/s each way, netem 10ms, 100 packets'],
                        stdout=log, stderr=subprocess.STDOUT)
                if a.load_mbps and (generator.poll() is not None or sink.poll() is not None):
                    raise RuntimeError('competition process stopped before transfer completion')
                stop(generator, generator_log)
                stop(sink, sink_log)
                generator = sink = None
                if a.load_mbps:
                    offered = json.loads((output / 'generator.json').read_text())
                    delivered = json.loads((output / 'sink.json').read_text())
                    if (not .5 * a.load_mbps <= offered['payload_mbps'] <= 1.5 * a.load_mbps
                            or delivered['payload_bytes'] <= 0):
                        raise RuntimeError('competition traffic was absent or offered rate outside 50–150% target')
                for prefix, dev in (([], 'tx'), (peer, 'rx')):
                    run(prefix + ['tc', '-j', '-s', 'qdisc', 'show', 'dev', dev], dev + '-after')
                return experiment.returncode
            finally:
                stop(generator, generator_log)
                stop(sink, sink_log)
                stop(holder, holder_log)
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        (output / 'ERROR.txt').write_text(str(exc) + '\n')
        return 2
    finally:
        (output / 'configuration.json').write_text(json.dumps({
            'commands': commands, 'seed': a.seed, 'mode': a.mode, 'load_payload_mbps': a.load_mbps,
            'kernel': platform.release(), 'topology': 'isolated sender netns -> veth -> receiver netns',
            'rate_mbps': 10, 'delay_ms_each_way': 10, 'queue_packets': 100,
            'limitation': 'constant UDP interference, not TCP fairness; ping/warmup/linger included in counters'
        }, indent=2) + '\n')


if __name__ == '__main__':
    raise SystemExit(main())
