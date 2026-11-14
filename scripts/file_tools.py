#!/usr/bin/env python3
"""Reproducible test files and streaming SHA-256, using only the standard library."""
import argparse
import hashlib
import json
from pathlib import Path

SIZES = (0, 1, 1023, 1024, 1025, 1024 * 1024, 10 * 1024 * 1024)


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def generate(directory):
    directory.mkdir(parents=True, exist_ok=True)
    records = []
    for size in SIZES:
        name = 'payload-%d.bin' % size
        path = directory / name
        # Different blocks and byte positions expose reordered/copy-pasted chunks.
        with path.open('xb') as output:
            for offset in range(0, size, 65536):
                seed = ('rudp-fixture-v1:%d:%d' % (size, offset)).encode('ascii')
                output.write(hashlib.shake_256(seed).digest(min(65536, size - offset)))
        records.append({'name': name, 'size_bytes': size, 'sha256': sha256(path)})
    manifest = directory / 'manifest.json'
    manifest.write_text(json.dumps({'generator': 'rudp-fixture-v1', 'files': records},
                                   indent=2) + '\n', encoding='utf-8')
    print(manifest)


def verify(source, received):
    result = {'source': str(source), 'received': str(received),
              'source_bytes': source.stat().st_size,
              'received_bytes': received.stat().st_size,
              'source_sha256': sha256(source), 'received_sha256': sha256(received)}
    result['match'] = (result['source_bytes'] == result['received_bytes'] and
                       result['source_sha256'] == result['received_sha256'])
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['match'] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='action', required=True)
    commands.add_parser('generate').add_argument('directory', type=Path)
    check = commands.add_parser('verify')
    check.add_argument('source', type=Path)
    check.add_argument('received', type=Path)
    args = parser.parse_args()
    try:
        if args.action == 'generate':
            generate(args.directory)
            return 0
        return verify(args.source, args.received)
    except OSError as error:
        parser.exit(2, 'ERROR: %s\n' % error)


if __name__ == '__main__':
    raise SystemExit(main())
