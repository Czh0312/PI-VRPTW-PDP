"""Check legacy seals' nested full runtime/input snapshots without solving."""
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json
import time

OUT = Path(__file__).resolve().parent
NIGHT = OUT.parent


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    assert not (OUT/'nested_summary.json').exists()
    begun = time.perf_counter()
    batches = ('label_bound01/full60', 'label_bound01/full60_v2',
               'certificate_ablation01/full30', 'pickup_route_relaxation01/strength15_30')
    rows = []
    for name in batches:
        folder = NIGHT/name
        manifest = json.loads((folder/'manifest.json').read_text())
        for path, expected in manifest['source_sha256'].items():
            file = folder/'source_snapshot'/path
            actual = sha(file) if file.is_file() else None
            rows.append(dict(batch=name, kind='complete_runtime_source', file=str(file),
                             expected_sha256=expected, actual_sha256=actual,
                             matched=expected == actual))
        for origin, record in manifest['input_sha256'].items():
            file = folder/record['snapshot']
            actual = sha(file) if file.is_file() else None
            rows.append(dict(batch=name, kind='frozen_business_input', origin=origin,
                             file=str(file), expected_sha256=record['sha256'],
                             actual_sha256=actual, matched=record['sha256'] == actual))
    summary = dict(observed_utc=datetime.now(timezone.utc).isoformat(), checked_entries=len(rows),
                   batches=list(batches), runtime_source_files=sum(x['kind']=='complete_runtime_source' for x in rows),
                   original_input_files=sum(x['kind']=='frozen_business_input' for x in rows),
                   all_matched=all(x['matched'] for x in rows),
                   mismatches=[x for x in rows if not x['matched']], no_solver_created=True,
                   seconds=time.perf_counter()-begun,
                   scope='Nested manifests supplement early result seals; immutable snapshots only, no shared live-source substitution.')
    (OUT/'nested_entries.json').write_text(json.dumps(rows, indent=2)+'\n')
    (OUT/'nested_summary.json').write_text(json.dumps(summary, indent=2)+'\n')
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
