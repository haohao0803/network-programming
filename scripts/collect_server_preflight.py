#!/usr/bin/env python3
"""Collect Linux server resource/build/namespace evidence without installing tools."""
import argparse
import json
import os
from pathlib import Path
import platform
import subprocess
import sys


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    if platform.system() != 'Linux':
        p.error('Run this on the actual Linux server, not on Mac.')
    if os.geteuid() != 0:
        p.error('Use sudo python3 scripts/collect_server_preflight.py --output NEW_DIRECTORY')
    a.output.mkdir(parents=True, exist_ok=False)
    root = Path(__file__).resolve().parents[1]
    commands = [
        ('revision', ['git', '-c', 'safe.directory=' + str(root), 'rev-parse', 'HEAD'], 10),
        ('working-tree', ['git', '-c', 'safe.directory=' + str(root), 'status', '--short'], 10),
        ('system', ['cat', '/etc/os-release'], 10),
        ('memory', ['free', '-h'], 10), ('disk', ['df', '-h', '.'], 10),
        ('make', ['make'], 120), ('default-network-check', ['make', 'check'], 180),
        ('isolated-netem-check', [sys.executable, 'scripts/check_server.py'], 120),
        ('tc-version', ['tc', '-V'], 10), ('ping-version', ['ping', '-V'], 10),
    ]
    summary = {'platform': platform.platform(), 'checks': []}
    for name, command, timeout in commands:
        record = {'name': name, 'command': command}
        with (a.output / (name + '.log')).open('w') as log:
            try:
                r = subprocess.run(command, cwd=root, stdout=log, stderr=subprocess.STDOUT,
                                   timeout=timeout)
                record['exit'] = r.returncode
            except (OSError, subprocess.SubprocessError) as exc:
                print(str(exc), file=log)
                record['exit'] = None
                record['error'] = str(exc)
        summary['checks'].append(record)
        print('%s exit=%s' % (name, record['exit']), flush=True)
    passed = all(r['exit'] == 0 for r in summary['checks'])
    summary['success'] = passed
    (a.output / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
