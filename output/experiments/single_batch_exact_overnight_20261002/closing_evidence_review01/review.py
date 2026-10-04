"""Read-only review of historical seals; classify live references separately."""
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json
import time

OUT = Path(__file__).resolve().parent
NIGHT = OUT.parent
ROOT = NIGHT.parents[2]


def read(p):
    return json.loads(p.read_text())


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    assert not (OUT/'summary.json').exists(), 'Preserve first review, no silent replacement'
    begun = time.perf_counter()
    manifests = {
        'label_bound01': 'final_result_manifest.json',
        'certificate_ablation01': 'final_manifest.json',
        'pickup_route_relaxation01': 'final_manifest.json',
        'pickup_column_supply01': 'final_manifest.json',
        'e1_route_cover01': 'final_manifest.json',
        'e1_component_kernel01': 'final_manifest.json',
        'e1_clocked_route_cover01': 'final_manifest.json',
        'e1_clocked_component_kernel01': 'final_manifest.json',
        'm4_dual_repair01': 'final_manifest.json',
        'warehouse_mask_diagnostic01': 'final_manifest.json',
    }
    stages = []
    entries = []
    for stage, name in manifests.items():
        path = NIGHT/stage/name
        manifest = read(path)
        files = manifest.get('files_sha256', manifest)
        assert isinstance(files, dict) and files
        counts = dict(immutable_matched=0, immutable_changed=0, immutable_missing=0,
                      mutable_matched=0, mutable_changed=0, mutable_missing=0)
        for relative, expected in files.items():
            assert isinstance(expected, str) and len(expected) == 64
            if 'files_sha256' in manifest:
                file_path = path.parent/relative
            else:
                file_path = ROOT/relative
            absolute = file_path.resolve()
            immutable = str(absolute).startswith(str((ROOT/'output/experiments').resolve())+'/')
            actual = sha(absolute) if absolute.is_file() else None
            status = 'missing' if actual is None else 'matched' if actual == expected else 'changed'
            counts[('immutable_' if immutable else 'mutable_')+status] += 1
            entries.append(dict(stage=stage, manifest=str(path.relative_to(ROOT)),
                                file=str(absolute), expected_sha256=expected,
                                actual_sha256=actual, status=status, immutable_experiment=immutable))
        stages.append(dict(stage=stage, manifest=str(path.relative_to(ROOT)),
                           manifest_sha256=sha(path), file_count=len(files), **counts))
    owned = []
    for relative, expected in read(NIGHT/'checkpoint.json')['owned_sha256'].items():
        actual = sha(ROOT/relative) if (ROOT/relative).is_file() else None
        owned.append(dict(path=relative, expected_sha256=expected, actual_sha256=actual,
                          unchanged=expected == actual))
    summary = dict(observed_utc=datetime.now(timezone.utc).isoformat(), stages=stages,
                   checked_entries=len(entries), unique_files=len({x['file'] for x in entries}),
                   immutable_changes=[x for x in entries if x['immutable_experiment'] and x['status'] != 'matched'],
                   mutable_reference_changes=[x for x in entries if not x['immutable_experiment'] and x['status'] != 'matched'],
                   owned=owned, all_eleven_owned_unchanged=all(x['unchanged'] for x in owned),
                   no_solver_created=True, seconds=time.perf_counter()-begun,
                   limitation='Historical live-reference changes retained; review is checksum evidence, not reexecution of tests or proof algorithms.')
    (OUT/'entries.json').write_text(json.dumps(entries, indent=2)+'\n')
    (OUT/'summary.json').write_text(json.dumps(summary, indent=2)+'\n')
    print(json.dumps({k: v for k, v in summary.items() if k not in ('owned', 'mutable_reference_changes', 'immutable_changes')}, indent=2))
    print('immutable_changes', len(summary['immutable_changes']), 'mutable_reference_changes', len(summary['mutable_reference_changes']))


if __name__ == '__main__':
    main()
