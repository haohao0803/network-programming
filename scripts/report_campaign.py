#!/usr/bin/env python3
"""Auditable repeated-experiment report. Pilot data never acquires formal status."""
import argparse
import csv
import json
import math
from pathlib import Path
import random
import statistics

from file_tools import sha256


def interval(values, seed=42):
    """Percentile bootstrap 95% interval for the successful-run mean, n>=5."""
    if len(values) < 5:
        return None, None
    rng = random.Random(seed)
    samples = sorted(statistics.mean(rng.choices(values, k=len(values))) for _ in range(2000))
    return samples[49], samples[1949]


def load_campaign(path):
    campaign = json.loads(path.read_text())
    records = []
    missing = []
    for task in campaign['tasks']:
        count = 0
        # Resolve relative directories against the manifest, allowing archived campaigns.
        directory = Path(task['directory'])
        if not directory.is_absolute():
            directory = path.parent / directory
        for source in sorted(directory.rglob('runs.jsonl')):
            metadata = json.loads((source.parent / 'metadata.json').read_text())
            for line in source.read_text().splitlines():
                r = json.loads(line)
                count += 1
                r.update(task_id=task['id'], campaign_repeat=task['repeat'],
                         seed=task['seed'], raw_jsonl=str(source),
                         sender_binary_sha256=metadata['sender_sha256'],
                         receiver_binary_sha256=metadata['receiver_sha256'])
                if (directory / 'generator.json').exists():
                    r['competition_offered'] = json.loads((directory / 'generator.json').read_text())
                    r['competition_delivered'] = json.loads((directory / 'sink.json').read_text())
                if r['success']:
                    if r['source_sha256'] != r['output_sha256']:
                        raise ValueError('success has mismatched hashes: ' + str(source))
                    if not (math.isfinite(r['goodput_Bps']) and r['goodput_Bps'] >= 0):
                        raise ValueError('invalid successful-run goodput')
                    trace = source.parent / r['parameter_log']
                    if sha256(trace) != r['trace_sha256']:
                        raise ValueError('parameter trace hash mismatch: ' + str(trace))
                records.append(r)
        if (count != task['expected_runs'] or task.get('status') not in ('finished', 'imported')
                or task.get('exit', 0) not in (0, 1)):
            missing.append({'task': task['id'], 'expected': task['expected_runs'], 'recorded': count,
                            'status': task.get('status')})
    return campaign, records, missing


def summarize(records):
    groups = {}
    for r in records:
        key = (r['mode'], r['scenario'], r['window'], r['source_bytes'],
               r['source_sha256'], r['sender_binary_sha256'], r['receiver_binary_sha256'])
        groups.setdefault(key, []).append(r)
    summary = []
    for key, runs in sorted(groups.items()):
        unique = {(r['campaign_repeat'], r['seed']) for r in runs}
        if len(unique) != len(runs):
            raise ValueError('duplicate repetition within a comparison cell')
        ok = [r for r in runs if r['success']]
        values = [r['goodput_Bps'] for r in ok]
        # Independent seed repetitions, not duplicate measurements of one seed.
        enough = len({r['seed'] for r in ok}) >= 5
        low, high = interval(values) if enough else (None, None)
        summary.append({'mode': key[0], 'scenario': key[1], 'window': key[2], 'file_bytes': key[3],
            'source_sha256': key[4], 'sender_binary_sha256': key[5], 'receiver_binary_sha256': key[6],
            'attempts': len(runs), 'confirmed': len(ok), 'completion_rate': len(ok) / len(runs),
            'mean_goodput_Bps': statistics.mean(values) if values else None,
            'median_goodput_Bps': statistics.median(values) if values else None,
            'ci95_low_Bps': low, 'ci95_high_Bps': high,
            'enough_independent_successes': enough,
            'mean_sender_seconds': statistics.mean(r['sender_seconds'] for r in ok) if ok else None,
            'mean_retransmissions': statistics.mean(r['retransmissions'] for r in ok)
             if ok and all(r.get('retransmissions') is not None for r in ok) else None,
            'timeouts': sum(bool(r.get('timed_out')) for r in runs),
            '_runs': runs})
    return summary


def plots(rows, destination, label):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import platform
    (destination / 'analysis.json').write_text(json.dumps({
        'python': platform.python_version(), 'matplotlib': matplotlib.__version__,
        'bootstrap_resamples': 2000, 'bootstrap_seed': 42,
        'interval': '95% percentile bootstrap of successful-run mean; >=5 distinct seeds',
        'label': label}, indent=2) + '\n')
    labels = ['%s / %s / w%d / %d B' % (r['mode'], r['scenario'], r['window'], r['file_bytes'])
              for r in rows]
    fig, axes = plt.subplots(1, 2, figsize=(16, max(4, .45 * len(rows))))
    for i, row in enumerate(rows):
        values = [r['goodput_Bps'] / 1e6 for r in row['_runs'] if r['success']]
        axes[0].scatter(values, [i + (j % 5 - 2) * .035 for j in range(len(values))], alpha=.65)
        if row['ci95_low_Bps'] is not None:
            mean = row['mean_goodput_Bps'] / 1e6
            axes[0].errorbar(mean, i, xerr=[[max(0, mean-row['ci95_low_Bps']/1e6)],
                                           [max(0, row['ci95_high_Bps']/1e6-mean)]],
                             fmt='D', color='black', capsize=4)
        axes[1].barh(i, row['completion_rate'], color='#2479ae')
        axes[1].text(.02, i, '%d/%d' % (row['confirmed'], row['attempts']), va='center')
    axes[0].set_yticks(range(len(labels)), labels)
    axes[1].set_yticks(range(len(labels)), [''] * len(labels))
    axes[0].set_xlabel('Successful-run goodput (MB/s); dots=all successful runs')
    axes[0].set_title('Diamond/95% bootstrap CI only with >=5 independent successes')
    axes[1].set_xlabel('Completion rate (failures retained)')
    axes[1].set_xlim(0, 1.05)
    fig.suptitle(label)
    fig.tight_layout()
    for suffix in ('png', 'pdf'):
        fig.savefig(destination / ('comparison.' + suffix), dpi=160)
    plt.close(fig)
    # First successful repetition, never the fastest selected run. Raw CSVs remain untouched.
    for i, row in enumerate(rows):
        ok = [r for r in row['_runs'] if r['success']]
        if not ok:
            continue
        r = sorted(ok, key=lambda x: (x['campaign_repeat'], x['task_id']))[0]
        path = Path(r['raw_jsonl']).parent / r['parameter_log']
        with path.open() as stream:
            points = list(csv.DictReader(stream))
        points = points[::max(1, len(points) // 2000)]
        x = [float(p['elapsed_ms']) / 1000 for p in points]
        fig, ax = plt.subplots(2, 1, figsize=(10, 6), sharex=True)
        ax[0].plot(x, [float(p['rto_ms']) for p in points], label='RTO')
        if 'srtt_ms' in points[0]:
            ax[0].plot(x, [float(p['srtt_ms']) for p in points], label='SRTT')
            ax[1].plot(x, [float(p['cwnd_packets']) for p in points], label='cwnd')
        ax[1].plot(x, [float(p['receive_window']) for p in points], label='receive window')
        ax[0].set_ylabel('ms')
        ax[1].set_ylabel('packets')
        ax[1].set_xlabel('Sender elapsed seconds (monotonic)')
        for a in ax:
            a.legend()
        fig.suptitle(label + '\n' + labels[i] + '; first successful repetition (not an average curve)')
        fig.tight_layout()
        fig.savefig(destination / ('parameters-%02d.png' % i), dpi=140)
        plt.close(fig)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--campaign', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--allow-partial', action='store_true')
    a = p.parse_args()
    campaign, records, missing = load_campaign(a.campaign)
    if campaign.get('plan_only') or not records:
        p.error('no measured campaign data; a plan is not a performance result')
    if missing and not a.allow_partial:
        p.error('campaign incomplete; use --allow-partial to label a partial report')
    rows = summarize(records)
    if campaign['phase'] == 'formal' and any(
            r['attempts'] < 5 or len({run['seed'] for run in r['_runs']}) < 5 for r in rows):
        missing.append({'error': 'formal comparison cells require at least five recorded repetitions'})
        if not a.allow_partial:
            p.error('insufficient formal repetitions')
    a.output.mkdir(parents=True, exist_ok=False)
    phase = 'FORMAL' if campaign['phase'] == 'formal' and not missing else 'PARTIAL'
    if campaign['phase'] == 'pilot':
        phase = 'PILOT — no performance conclusion'
    (a.output / 'all_runs.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in records))
    (a.output / 'completeness.json').write_text(json.dumps({'phase': phase,
        'expected_runs': campaign['expected_runs'], 'recorded_runs': len(records),
        'missing': missing, 'campaign': str(a.campaign.resolve()),
        'campaign_sha256': sha256(a.campaign)}, indent=2) + '\n')
    with (a.output / 'summary.csv').open('w', newline='') as stream:
        fields = [k for k in rows[0] if k != '_runs']
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)
    plots(rows, a.output, phase)
    print('%s: %d raw records; %d incomplete tasks' % (phase, len(records), len(missing)))


if __name__ == '__main__':
    main()
