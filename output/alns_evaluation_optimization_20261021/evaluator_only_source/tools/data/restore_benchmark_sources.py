"""Verify shared frozen sources; optionally restore redundant per-run copies."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil


def relative_source(value):
    path = Path(value.replace('\\', '/'))
    if path.is_absolute() or '..' in path.parts:
        raise ValueError('Unsafe source path: '+value)
    return path


def process_phase(phase, restore=False):
    phase = Path(phase)
    manifest = json.loads((phase/'manifest.json').read_text(encoding='utf-8'))
    sources = {relative_source(p): digest for p, digest in manifest['source_sha256'].items()}
    for relative, digest in sources.items():
        payload = (phase/'source_snapshot'/relative).read_bytes()
        if hashlib.sha256(payload).hexdigest() != digest:
            raise ValueError('Shared frozen source changed: '+str(relative))
    runs, references, restored = 0, 0, 0
    # One level only: do not scan the redundant local source trees.
    for path in sorted(phase.glob('*/manifest.json')):
        saved = json.loads(path.read_text(encoding='utf-8'))
        hashes = saved.get('source_sha256')
        if not hashes:
            continue
        runs += 1
        for raw, digest in hashes.items():
            relative = relative_source(raw)
            if sources.get(relative) != digest:
                raise ValueError(f'Run source differs from phase snapshot: {path.parent.name}/{relative}')
            references += 1
            if restore:
                target = path.parent/'source_snapshot'/relative
                if target.exists():
                    if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
                        raise ValueError('Refusing to replace changed run source: '+str(target))
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(phase/'source_snapshot'/relative, target)
                restored += 1
    return dict(phase=str(phase), runs=runs, source_references=references, restored_files=restored)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--study', required=True, help='Benchmark root or a single phase directory')
    parser.add_argument('--restore', action='store_true', help='Create missing per-run source copies after hash verification')
    args = parser.parse_args(argv)
    study = Path(args.study).resolve()
    phases = ([study] if (study/'manifest.json').is_file() else
              [p for p in sorted(study.iterdir()) if p.is_dir() and (p/'manifest.json').is_file()])
    if not phases:
        raise ValueError('No benchmark phases found: '+str(study))
    results = [process_phase(phase, args.restore) for phase in phases]
    print(json.dumps(dict(phases=results, total_runs=sum(r['runs'] for r in results),
                         total_source_references=sum(r['source_references'] for r in results)),
                     ensure_ascii=False, indent=2))


if __name__=='__main__':
    main()
