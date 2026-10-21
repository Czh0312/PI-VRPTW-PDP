"""Read-only replay of saved single-batch results against hash-verified inputs."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from models.common.cycle import load_instance
from models.common.cycle.audit import audit_solution
from tools.configuration.loader import apply_container_pool_size


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--history', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args(argv)
    if args.output.exists():
        raise ValueError('Refusing to overwrite history audit')
    rows = []
    for path in sorted(args.history.glob('*/result.json')):
        result = json.loads(path.read_text())
        row = dict(run=path.parent.name, model=result['model'], status=result['info']['status'],
                   claimed_valid=result.get('audit', {}).get('valid', False),
                   claimed_optimal=result['info'].get('proven_optimal', False),
                   objective=result['info'].get('objective'))
        try:
            manifest = json.loads((path.parent/'manifest.json').read_text())
            config = dict(manifest['config'])
            local = {hashlib.sha256(p.read_bytes()).hexdigest(): p.resolve()
                     for p in (path.parent/'inputs').glob('*') if p.is_file()}
            verified_paths = {}
            for original, expected in manifest['input_sha256'].items():
                if expected in local:
                    verified_paths[original] = str(local[expected])
                elif Path(original).exists() and hashlib.sha256(Path(original).read_bytes()).hexdigest() == expected:
                    verified_paths[original] = original
                else:
                    raise ValueError('Missing or altered frozen input: '+original)
            for field in ('data', 'orders', 'routing_config', 'operating_policy', 'distance_matrix', 'duration_matrix'):
                if isinstance(config.get(field), str):
                    config[field] = verified_paths[config[field]]
            fleet = json.loads(Path(config['routing_config']).read_text())
            policy = config.get('operating_policy', fleet.get('operating_policy'))
            fleet['operating_policy'] = json.loads(Path(policy).read_text()) if isinstance(policy, str) else policy
            if 'container_pool_size' in config:
                config, fleet = apply_container_pool_size(config, fleet, config['container_pool_size'])
            if fleet != manifest['fleet']:
                raise ValueError('Recorded fleet differs from reconstructed frozen inputs')
            problem = load_instance(config, fleet)
            if len(problem.operating_policy.batches) != 1:
                raise ValueError('Expected B=1')
            row['input_hashes_verified'] = True
            row['fleet_reconstructed_matches_manifest'] = True
            if row['objective'] is None:
                row.update(replayed=False, reason='NO_INCUMBENT', valid=False)
            else:
                audited = audit_solution(problem, result)
                row.update(replayed=True, valid=audited['valid'], errors=audited['errors'],
                           periodic=audited.get('periodic_replay', {}).get('valid', False),
                           recomputed_objective=audited.get('objective'))
        except Exception as exc:
            row.update(replayed=False, valid=False, error=f'{type(exc).__name__}: {exc}')
        rows.append(row)
    args.output.write_text(json.dumps(rows, ensure_ascii=False, indent=2))
    print('saved', len(rows), 'replayed', sum(r.get('replayed', False) for r in rows),
          'valid', sum(r['valid'] for r in rows), 'errors', sum('error' in r for r in rows))


if __name__ == '__main__':
    main()
