#!/usr/bin/env python3
"""Archive a stopped campaign with all raw files and a per-file SHA-256 sidecar."""
import argparse
import json
from pathlib import Path
import tarfile

from file_tools import sha256


def archive(root, output):
    root, output = root.resolve(strict=True), output.resolve()
    if root in output.parents:
        raise ValueError('archive must be outside the raw campaign directory')
    manifest = json.loads((root / 'campaign.json').read_text())
    if any(t['status'] == 'running' for t in manifest['tasks']):
        raise ValueError('stop running campaign before taking an archival snapshot')
    files = sorted(root.rglob('*'))
    if any(p.is_symlink() for p in files):
        raise ValueError('raw archive must not contain symlinks to outside data')
    hashes = {str(p.relative_to(root)): sha256(p) for p in files if p.is_file()}
    sidecar = Path(str(output) + '.sha256.json')
    if output.exists() or sidecar.exists():
        raise ValueError('archive and checksum destinations must be new')
    with output.open('xb') as stream, tarfile.open(fileobj=stream, mode='w:gz') as package:
        package.add(root, arcname=root.name)
    if any(sha256(root / name) != digest for name, digest in hashes.items()):
        raise ValueError('raw data changed during archive; do not use this snapshot')
    sidecar.write_text(json.dumps({'archive_sha256': sha256(output),
        'raw_root': root.name, 'files': hashes}, indent=2) + '\n')
    return sidecar


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--campaign-dir', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    try:
        print(archive(a.campaign_dir, a.output))
    except (OSError, ValueError) as exc:
        p.error(str(exc))


if __name__ == '__main__':
    main()
