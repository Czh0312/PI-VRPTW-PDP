"""Derive a separate one-box release from frozen formal inputs, preserving appointments."""
from __future__ import annotations

import argparse
from copy import deepcopy

from tools.audit.audit_formal_benchmark27 import audit_dataset
from tools.data.build_formal_benchmark27 import build_suite, read, digest, reference, write_json
from tools.configuration.loader import resolve_project_path
from tools.data.one_box_contract import CONTRACT, preserved_orders_hash, preserved_policy_hash


VERSION = 'formal_benchmark27_one_box_v1'
DEFAULT_OUTPUT = f'data/{VERSION}'
DEFAULT_SELECTION = 'config/experiments/benchmark27_one_box_v1/selection.json'
SOURCE = 'data/formal_benchmark27_v1'


def selection_from_frozen(source=SOURCE):
    root = resolve_project_path(source).resolve()
    audit_dataset(root)  # Validate the frozen bytes before deriving anything.
    design = deepcopy(read(root/'selection.json'))
    design.update(design_id='benchmark27_one_box_v1', dataset_version=VERSION,
        date='2026-10-19', status='one_box_release_derived_from_frozen_revision_3',
        source_dataset=reference(root), source_dataset_manifest_sha256=digest(root/'manifest.json'),
        source_manifest=reference(root/'manifest.json'), source_manifest_sha256=digest(root/'manifest.json'),
        demand_contract=CONTRACT)
    protocol = design['review_protocol']
    protocol.update(dataset_revision=4, scenario_label='一客一箱', demand_contract=CONTRACT,
        formal_run_status='New inputs; prior multi-box results do not validate this release.',
        legacy_scenario_label='多箱需求情景',
        batch_domain_change=False,
        rerun_gate='First validate all eight S01 models with exact and ALNS and independent replay; then S04/S07.',
        formal_rerun=dict(exact_seconds=300, alns_seconds=30, alns_seeds=[42,43,44,45,46],
                         exact_calls=216, alns_calls=1080))
    for record in design['cases']:
        common = root/'shared'/record['size']/record['id']
        files = {k: reference(common/name) for k, name in (
            ('data','network.csv'), ('orders','reference_orders.json'),
            ('routing_config','fleet.json'), ('operating_policy','reference_policy.json'),
            ('economic_config','economic.json'))}
        record.update(source_total_container_uses=record['total_container_uses'],
            total_container_uses=record['customer_count'], common_reference_files=files,
            reference_sha256={k:digest(resolve_project_path(p)) for k,p in files.items()},
            preserved_orders_sha256=preserved_orders_hash(read(common/'reference_orders.json')),
            preserved_policy_sha256=preserved_policy_hash(read(common/'reference_policy.json')),
            batch_count_certificate_sha256=digest(common/'batch_count_certificate.json'),
            adaptation_status='one_box_counts_only; original batch definition retained')
    return design


def build_one_box(output=DEFAULT_OUTPUT, selection=DEFAULT_SELECTION, source=SOURCE):
    out, selected = resolve_project_path(output), resolve_project_path(selection)
    if out.exists() and any(out.iterdir()):
        raise ValueError(f'Refusing to overwrite nonempty dataset: {out}')
    design = selection_from_frozen(source)
    if selected.exists():
        if read(selected) != design:
            raise ValueError(f'Refusing to overwrite changed selection: {selected}')
    else:
        write_json(selected, design)
    return build_suite(output, selected)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default=DEFAULT_OUTPUT)
    parser.add_argument('--selection', default=DEFAULT_SELECTION)
    parser.add_argument('--source', default=SOURCE)
    args = parser.parse_args()
    print(build_one_box(args.output, args.selection, args.source))
