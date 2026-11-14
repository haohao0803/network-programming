#!/usr/bin/env python3
"""Plan or run repeatable experiments; never label smoke data as formal results."""
import argparse
import datetime
import json
import os
from pathlib import Path
import platform
import random
import shutil
import signal
import subprocess
import sys
import time

from file_tools import sha256
from run_experiments import git_value

ROOT = Path(__file__).resolve().parents[1]


def schedule(output, file, competition_file, repeats, seed, suite):
    jobs = []
    for repeat in range(1, repeats + 1):
        modes = ['fixed', 'adaptive'] if repeat % 2 else ['adaptive', 'fixed']
        run_seed = seed + repeat - 1
        if suite in ('all', 'netem'):
            destination = output / ('netem-r%02d' % repeat)
            jobs.append({'id': destination.name, 'repeat': repeat, 'seed': run_seed,
                'expected_runs': 16, 'directory': destination.name,
                'command': [sys.executable, str(ROOT / 'scripts/run_netem.py'),
                    '--file', str(file), '--output', str(destination), '--modes', *modes,
                    '--repeats', '1', '--seed', str(run_seed), '--max-seconds', '1800', '--keep-files']})
        if suite in ('all', 'competition'):
            conditions = [(mode, load) for load in (0, 7, 12) for mode in modes]
            random.Random(run_seed).shuffle(conditions)
            for mode, load in conditions:
                destination = output / ('competition-r%02d-%s-%dmbps' % (repeat, mode, load))
                jobs.append({'id': destination.name, 'repeat': repeat, 'seed': run_seed,
                    'expected_runs': 1, 'directory': destination.name,
                    'command': [sys.executable, str(ROOT / 'scripts/run_competition.py'),
                        '--file', str(competition_file), '--output', str(destination),
                        '--mode', mode, '--load-mbps', str(load), '--seed', str(run_seed)]})
    return jobs


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--file', type=Path, required=True, help='Netem input (suggested 1 MiB)')
    p.add_argument('--competition-file', type=Path, help='Suggested 10 MiB')
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--repeats', type=int, default=5)
    p.add_argument('--seed', type=int, default=20261120)
    p.add_argument('--suite', choices=['all', 'netem', 'competition'], default='all')
    p.add_argument('--phase', choices=['formal', 'pilot'], default='formal')
    p.add_argument('--plan-only', action='store_true')
    a = p.parse_args()
    if a.repeats < (5 if a.phase == 'formal' else 1):
        p.error('formal campaigns require at least 5 independent repetitions per cell')
    if not 1 <= a.seed <= 4294967295 - a.repeats:
        p.error('seed range invalid')
    if not a.plan_only and platform.system() != 'Linux':
        p.error('execution requires Linux; --plan-only works on Mac')
    if not a.plan_only and os.geteuid() != 0:
        p.error('execution requires sudo for isolated Linux namespaces')
    source = a.file.resolve(strict=True)
    competition_source = (a.competition_file or a.file).resolve(strict=True)
    output = a.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    if not a.plan_only:
        inputs = output / 'inputs'
        inputs.mkdir()
        shutil.copyfile(source, inputs / 'netem-input.bin')
        shutil.copyfile(competition_source, inputs / 'competition-input.bin')
        source, competition_source = inputs / 'netem-input.bin', inputs / 'competition-input.bin'
    jobs = schedule(output, source, competition_source, a.repeats, a.seed, a.suite)
    manifest = {'schema_version': 1, 'phase': a.phase, 'plan_only': a.plan_only,
        'created_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'platform': platform.platform(), 'revision': git_value('rev-parse', 'HEAD'),
        'git_status': git_value('status', '--porcelain'), 'repeats': a.repeats,
        'source': str(source), 'source_sha256': sha256(source),
        'competition_source': str(competition_source), 'competition_source_sha256': sha256(competition_source),
        'expected_runs': sum(j['expected_runs'] for j in jobs), 'tasks': jobs}
    def save():
        tmp = output / 'campaign.json.tmp'
        tmp.write_text(json.dumps(manifest, indent=2) + '\n')
        tmp.replace(output / 'campaign.json')
    for job in jobs:
        job['status'] = 'planned'
    save()
    if a.plan_only:
        print('Plan only: %d tasks, %d scheduled runs; no measurements' % (len(jobs), manifest['expected_runs']))
        return 0
    def interrupted(signum, _):
        raise KeyboardInterrupt('signal %d' % signum)
    signal.signal(signal.SIGTERM, interrupted)
    failed = False
    try:
        for job in jobs:
            if sha256(source) != manifest['source_sha256'] or sha256(competition_source) != manifest['competition_source_sha256']:
                raise RuntimeError('input file changed during campaign')
            job['status'] = 'running'
            job['started_utc'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
            start = time.monotonic()
            save()
            print('RUN ' + job['id'], flush=True)
            with (output / (job['id'] + '.log')).open('x') as log:
                # Child scripts bound every transfer and clean their own namespaces.
                child = subprocess.Popen(job['command'], stdout=log, stderr=subprocess.STDOUT,
                                         start_new_session=True)
                try:
                    code = child.wait()
                except BaseException:
                    try:
                        os.killpg(child.pid, signal.SIGTERM)
                        child.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        os.killpg(child.pid, signal.SIGKILL)
                        child.wait(timeout=5)
                    except ProcessLookupError:
                        pass
                    job.update(status='interrupted', seconds=time.monotonic() - start)
                    raise
            job.update(status='finished', exit=code, seconds=time.monotonic() - start)
            failed = failed or code != 0
            save()
    finally:
        save()  # interruptions leave running/planned states and all completed raw data
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
