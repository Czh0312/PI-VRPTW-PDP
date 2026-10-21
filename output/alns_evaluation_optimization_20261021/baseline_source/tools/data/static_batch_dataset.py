"""Select generated dataset cases and check pairing without running a solver."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import argparse
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.configuration.loader import load_routing_case, resolve_project_path


def load_manifest(path, *, groups=None, sizes=None, batch_counts=None, family_ids=None, source_kinds=None):
    path = resolve_project_path(path)
    manifest = json.loads(path.read_text(encoding='utf-8'))
    if manifest.get('dataset_version') not in ('static_batch_suite_v1', 'static_batch_suite_v2') or not isinstance(manifest.get('cases'), list):
        raise ValueError('Expected a supported static batch dataset manifest')
    if (manifest.get('information_mode') != 'static_all_known'
            or manifest.get('assignment_mode') != 'fixed_input'):
        raise ValueError('Static studies require all-known information and fixed input assignments')
    verify_artifacts(manifest, path.parent)
    cases = manifest['cases']
    if len({r['case_id'] for r in cases}) != len(cases):
        raise ValueError('Duplicate dataset case_id')
    selected = [r for r in cases if (not groups or r['group'] in groups)
                and (not sizes or r['size'] in sizes)
                and (not batch_counts or r['batch_count'] in batch_counts)
                and (not source_kinds or r['source_kind'] in source_kinds)
                and (not family_ids or r['family_id'] in family_ids)]
    if not selected:
        raise ValueError('Dataset filters matched no cases')
    return manifest, selected


def check_pairing(records):
    """Compare actual file contents, not a claimed fingerprint in metadata."""
    families = {}
    for record in records:
        cfg, fleet = load_routing_case(record['case_path'], use_pool_config=False)
        orders = json.loads(resolve_project_path(cfg['orders']).read_text(encoding='utf-8'))
        normalized = sorted(({k: v for k, v in row.items() if k != 'delivery_batch_id'} for row in orders),
                            key=lambda row: row['order_id'])
        p = fleet['operating_policy']
        policy = {k: v for k, v in p.items() if k != 'batches'}
        last_cutoff = p['cycle_start']+sum(b['delivery_duration']+b['return_duration'] for b in p['batches'])-p['batches'][-1]['return_duration']
        signature = dict(orders=normalized, policy=policy, last_cutoff=last_cutoff,
                         fleet={k: v for k, v in fleet.items() if k != 'operating_policy'},
                         network=resolve_project_path(cfg['data']).read_bytes(),
                         matrices={k: resolve_project_path(cfg[k]).read_bytes() for k in ('distance_matrix', 'duration_matrix') if cfg.get(k)})
        previous = families.setdefault(record['family_id'], signature)
        if previous != signature:
            raise ValueError(f'PAIRING_MISMATCH: {record["family_id"]}; orders/windows/resources differ')
        batch_ids = {b['id'] for b in p['batches']}
        if len(batch_ids) != record['batch_count']:
            raise ValueError(f'BATCH_COUNT_MISMATCH: {record["case_id"]}')
        demand = {bid: sum(o['container_count'] for o in orders if o['delivery_batch_id'] == bid) for bid in batch_ids}
        if demand != record['batch_demands'] or sum(demand.values()) != record['total_container_uses']:
            raise ValueError(f'DEMAND_MISMATCH: {record["case_id"]}')
        if len(orders) != record['order_count'] or p['container_pool_size'] != record['default_pool']:
            raise ValueError(f'METADATA_MISMATCH: {record["case_id"]}')
    return len(families)


def verify_artifacts(manifest, directory):
    directory = Path(directory).resolve()
    hashes = manifest.get('artifact_sha256')
    if not isinstance(hashes, dict) or not hashes:
        raise ValueError('Dataset manifest requires nonempty artifact_sha256')
    for relative, expected in hashes.items():
        target = (directory/relative).resolve()
        if directory not in target.parents:
            raise ValueError('Dataset artifact path leaves its directory')
        if hashlib.sha256(target.read_bytes()).hexdigest() != expected:
            raise ValueError(f'DATASET_HASH_MISMATCH: {relative}')


def suggested_pool_sizes(record):
    """Use declared per-case pools; never infer feasibility from this selection."""
    values = record.get('suggested_pool_sizes')
    if not isinstance(values, list) or not values:
        raise ValueError(f'Missing suggested_pool_sizes: {record["case_id"]}')
    if any(isinstance(n, bool) or not isinstance(n, int) or n < 1 for n in values):
        raise ValueError(f'Invalid suggested_pool_sizes: {record["case_id"]}')
    return sorted(set(values))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', default='data/manifest.json')
    parser.add_argument('--groups', nargs='+', choices=['single_batch', 'multi_batch'])
    parser.add_argument('--source-kinds', nargs='+', choices=['synthetic_mechanism', 'zhou_derived_scenario', 'lade_derived_scenario'])
    parser.add_argument('--sizes', nargs='+', choices=['small', 'medium', 'large'])
    parser.add_argument('--batch-counts', nargs='+', type=int, choices=[1, 2, 3])
    parser.add_argument('--families', nargs='+')
    parser.add_argument('--paths-only', action='store_true')
    args = parser.parse_args(argv)
    _, selected = load_manifest(args.manifest, groups=args.groups, sizes=args.sizes,
                                batch_counts=args.batch_counts, family_ids=args.families, source_kinds=args.source_kinds)
    check_pairing(selected)
    for record in selected:
        print(record['case_path'] if args.paths_only else json.dumps(record, ensure_ascii=False))
    return selected


if __name__ == '__main__':
    main()
