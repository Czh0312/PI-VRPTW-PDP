"""Rebuild all 30 E6 exact references; record historical results without reusing proofs."""
from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
from pathlib import Path

from experiments.runs.run_thesis_completion import read, write, sha
from models.common.cycle.audit import audit_solution
from models.common.cycle.instance import load_instance
from tools.configuration.loader import PROJECT_ROOT, load_routing_case
from tools.data.build_thesis_followups import base_design


def physical_signature(problem):
    """M2's complete loaded input, including integer travel and service times."""
    nodes = sorted(problem.network.nodes)
    payload = dict(model=problem.model_number, reusable=problem.reusable,
        free_assignment=problem.free_assignment, policy=asdict(problem.operating_policy),
        fleet=vars(problem.fleet), orders=[asdict(o) for o in problem.orders],
        depot=problem.depot, satellites=list(problem.satellites),
        nodes=[vars(problem.network.nodes[n]) for n in nodes],
        distances=[[problem.distance(a, b) for b in nodes] for a in nodes],
        travel_ticks=[[problem.travel(a, b) for b in nodes] for a in nodes])
    encoded = json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


def build(baseline_design, destination, history):
    design, old = read(baseline_design), read(history/'manifest.json')
    out = Path(destination).resolve()
    if out.exists() and any(out.iterdir()):
        raise ValueError('Refusing to overwrite reference registration')
    cases = {}
    for item in design['jobs']:
        if item['model'] != 2 or item['method'] != 'alns' or item['budget'] != 30:
            raise ValueError('E6 baseline must be M2 ALNS at 30 seconds')
        cases.setdefault(item['case'], []).append(item)
    expected = {c['case_id'] for c in old['selected_dataset_cases'] if c['size'] == 'small'}
    if set(cases) != expected or len(cases) != 30:
        raise ValueError('Reference must retain the entire original 30-case collection')
    out.mkdir(parents=True)
    jobs, records = [], []
    for case, members in sorted(cases.items()):
        if sorted(r['seed'] for r in members) != list(range(42, 47)):
            raise ValueError('Baseline requires each seed exactly once')
        if len({r['case_path'] for r in members}) != 1:
            raise ValueError('Seeds must share physical input')
        item = deepcopy(members[0])
        item.update(id=f'e6reference_{case}_m2_exact_baseline_t300_s42', method='exact',
                    budget=300, seed=42, diagnostics=False)
        jobs.append(item)
        problem = load_instance(*load_routing_case(item['case_path'], use_pool_config=False))
        historical = next(j for j in old['jobs'] if j['case_id'] == case and j['model'] == 2 and j['solver'] == 'exact')
        run = history/historical['run']
        result, manifest = read(run/'result.json'), read(run/'manifest.json')
        audit = audit_solution(problem, result)
        old_problem = load_instance(*load_routing_case(historical['case_path'], use_pool_config=False))
        source_entry = run/'source_snapshot/models/routing/variants/model_2/exact/solver.py'
        records.append(dict(case=case, historical_result=str((run/'result.json').relative_to(PROJECT_ROOT)),
            historical_result_sha256=sha(run/'result.json'), historical_manifest_sha256=sha(run/'manifest.json'),
            historical_claim=result['info']['status'], historical_objective=result['info'].get('objective'),
            historical_proof_scope=result['info'].get('proof_scope'),
            historical_inputs_intact=all(Path(p).is_file() and sha(p) == digest for p, digest in manifest['input_sha256'].items()),
            current_physical_signature=physical_signature(problem),
            historical_physical_signature=physical_signature(old_problem),
            same_loaded_physical_input=physical_signature(problem) == physical_signature(old_problem),
            current_audit_valid=audit['valid'], current_audit_errors=audit['errors'],
            old_entry_sha256=sha(source_entry) if source_entry.exists() else None,
            current_entry_sha256=sha(PROJECT_ROOT/'models/routing/model_2/exact/solver.py'),
            reused_as_optimal_reference=False,
            decision='Rebuild exact reference under the frozen baseline; historical proof used pre-reorganization solver modules.'))
    reference = base_design('e6_reference', jobs,
        'All original 30 small cases with the identical E6 baseline physical inputs and frozen M2 implementation; exact 300 s, one thread. Limited runs retain bounds and never become optimum references without a proof.',
        sources=deepcopy(design['sources']))
    reference['historical_reuse'] = 'No historical optimum reused; historical comparison is descriptive only.'
    write(out/'reference_design.json', reference)
    write(out/'historical_comparison.json', records)
    print(json.dumps(dict(cases=len(records), identical_physical_inputs=sum(r['same_loaded_physical_input'] for r in records),
        historical_inputs_intact=sum(r['historical_inputs_intact'] for r in records),
        historical_current_audit_valid=sum(r['current_audit_valid'] for r in records),
        historical_optima_reused=0, exact_call_budget_hours=sum(j['budget'] for j in jobs)/3600)))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline-design', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--history', default='output/experiments/m1_m4_comparison_20261004/full')
    args = parser.parse_args()
    build(Path(args.baseline_design).resolve(), args.output, Path(args.history).resolve())
