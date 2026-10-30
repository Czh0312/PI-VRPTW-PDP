"""One current-default M3 exact run: medium M01, seed 42, 300 seconds."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import time
import zipfile

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
sys.path.insert(0, str(ROOT))
import gurobipy as gp
from tools.configuration.loader import load_routing_case
from models.common.cycle import load_instance
from models.common.cycle.audit import audit_solution
from models.routing.model_3.exact import solve_exact
from models.routing.model_3.exact.route_partition import MAX_ORDERS


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def main():
    assert not (OUT / 'plan.json').exists(), 'Refusing to overwrite an attempt'
    case_path = ROOT / 'data/formal_benchmark27_uniform_7_20_50_v1/model_3/medium/M01/case.json'
    config, _ = load_routing_case(case_path, use_pool_config=False)
    files = {Path(__file__), case_path}
    files.update(ROOT / config[key] for key in ('data', 'routing_config', 'economic_config',
        'dataset_metadata', 'orders', 'operating_policy', 'parameter_contract'))
    for folder in ('models/routing/model_3/exact', 'models/common', 'models/cost', 'tools/configuration'):
        files.update((ROOT / folder).rglob('*.py'))
    hashes = {p.relative_to(ROOT).as_posix(): sha(p) for p in files}
    plan = dict(case='M01', model=3, customers=20, seed=42, budget_seconds=300, threads=1,
        formulation='expanded', strategy='milp', route_strengthening_requested=True,
        small_route_enumeration_limit=MAX_ORDERS, expected_small_acceleration_applied=False,
        historical_solution_or_bound_supplied=False, single_seed_single_instance=True,
        interpreter=sys.executable, gurobi_version=list(gp.gurobi.version()), hashes=hashes,
        registered_at=datetime.now(timezone.utc).isoformat(),
        timing='Case loading, model preparation/optimization, extraction and independent audit; startup/preflight/archive excluded.')
    save(OUT / 'plan.json', plan)
    with zipfile.ZipFile(OUT / 'source_inputs.zip', 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        for relative in sorted(hashes):
            archive.write(ROOT / relative, relative)
    begun = time.perf_counter()
    cfg, fleet = load_routing_case(case_path, use_pool_config=False)
    problem = load_instance(cfg, fleet)
    assert problem.model_number == 3 and problem.cost_objective
    assert len(problem.orders) == problem.total == 20
    original = gp.Model.optimize
    calls = []

    def observed(model, *args, **kwargs):
        assert not args and not kwargs
        model.update()
        model.Params.OutputFlag = 1
        model.Params.LogToConsole = 0
        model.Params.LogFile = (OUT.relative_to(ROOT) / 'gurobi.log').as_posix()
        record = dict(model_name=model.ModelName, variables=model.NumVars, binary_variables=model.NumBinVars,
            linear_constraints=model.NumConstrs, general_constraints=model.NumGenConstrs,
            starts=model.NumStart, fingerprint=model.Fingerprint,
            optimize_begins_after_seconds=time.perf_counter()-begun)
        save(OUT / 'progress.json', dict(stage='complete_MILP_optimization', **record))
        print(json.dumps(record, ensure_ascii=False), flush=True)
        try:
            return original(model)
        finally:
            record.update(native_status=model.Status, runtime=model.Runtime, work=model.Work,
                nodes=model.NodeCount, optimize_ends_after_seconds=time.perf_counter()-begun)
            calls.append(record)

    gp.Model.optimize = observed
    try:
        result = solve_exact(problem, time_limit=300, seed=42)
    finally:
        gp.Model.optimize = original
    info = result['info']
    audit = audit_solution(problem, result) if result.get('audit', {}).get('valid') else None
    if audit:
        assert audit['valid'] and audit['periodic_replay']['valid']
    if info['proven_optimal']:
        assert audit and info['status'] == 'OPTIMAL' and calls[-1]['native_status'] == 2
    summary = dict(case='M01', model=3, customers=20, seed=42, budget_seconds=300,
        total_wall_seconds=time.perf_counter()-begun, **info,
        audited_feasible=bool(audit), independent_periodic_replay_valid=bool(audit and audit['periodic_replay']['valid']),
        optimized_entry_used=True, small_acceleration_actually_applied=info['route_strengthening']['applied'],
        original_call_scope='Complete fixed-assignment, fixed-calendar, fixed-pool M3 cost MILP',
        actual_optimize_calls=calls, production_source_modified=False)
    assert not summary['small_acceleration_actually_applied']
    assert info['route_strengthening']['reason'] == 'outside_small_fixed_assignment_scope'
    for relative, expected in hashes.items():
        assert sha(ROOT / relative) == expected, relative
    save(OUT / 'result.json', result)
    save(OUT / 'independent_audit.json', audit)
    save(OUT / 'summary.json', summary)
    save(OUT / 'progress.json', dict(stage='completed', **summary))
    print(json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
