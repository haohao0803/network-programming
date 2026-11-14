#!/usr/bin/env python3
"""Summarize measured JSONL runs as CSV and a standalone SVG; never invent data."""
import argparse
import csv
from html import escape
import json
from pathlib import Path
import statistics


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('inputs', type=Path, nargs='+')
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    groups = {}
    for path in a.inputs:
        for line in path.read_text(encoding='utf-8').splitlines():
            r = json.loads(line)
            # Keep sizes/scenarios separate; failure contributes to completion rate.
            key = r.get('mode', 'fixed'), r['scenario'], r['window'], r['source_bytes']
            groups.setdefault(key, []).append(r)
    if not groups:
        p.error('no records')
    a.output.mkdir(parents=True, exist_ok=False)
    rows = []
    for (mode, scenario, window, size), records in sorted(groups.items()):
        ok = [r for r in records if r['success']]
        rows.append({'mode': mode, 'scenario': scenario, 'window_packets': window, 'file_bytes': size,
                     'attempts': len(records), 'confirmed': len(ok),
                     'completion_rate': len(ok) / len(records),
                     'mean_sender_seconds': statistics.mean(r['sender_seconds'] for r in ok) if ok else None,
                     'mean_goodput_Bps': statistics.mean(r['goodput_Bps'] for r in ok) if ok else None,
                     'mean_retransmissions': statistics.mean(r['retransmissions'] for r in ok)
                     if ok and all(r.get('retransmissions') is not None for r in ok) else None})
    with (a.output / 'summary.csv').open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    # A compact static horizontal bar chart; failures are always labelled.
    maximum = max((r['mean_goodput_Bps'] or 0 for r in rows), default=1) or 1
    height = 125 + len(rows) * 60
    svg = ['<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="%d" viewBox="0 0 1200 %d">' % (height, height),
           '<rect width="100%" height="100%" fill="white"/>',
           '<g font-family="sans-serif" fill="#17212b">',
           '<text x="24" y="32" font-size="20">Measured application goodput (successful runs only)</text>',
           '<text x="24" y="56" font-size="13">Sender CLI elapsed time excludes receiver linger. See CSV and raw logs for conditions.</text>']
    for i, r in enumerate(rows):
        y = 90 + i * 60
        label = '%s / %s / w=%d / %d B' % (r['mode'], r['scenario'], r['window_packets'], r['file_bytes'])
        value = r['mean_goodput_Bps']
        width = 440 * (value or 0) / maximum
        text = ('%.0f B/s' % value) if value is not None else 'no confirmed transfer'
        text += ' (%d/%d confirmed)' % (r['confirmed'], r['attempts'])
        svg += ['<text x="24" y="%d" font-size="13">%s</text>' % (y, escape(label)),
                '<rect x="480" y="%d" width="%.2f" height="22" rx="3" fill="#2479ae"/>' % (y - 15, width),
                '<text x="940" y="%d" font-size="12">%s</text>' % (y, escape(text))]
    svg += ['</g></svg>']
    (a.output / 'goodput.svg').write_text('\n'.join(svg), encoding='utf-8')
    print(a.output / 'summary.csv')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
