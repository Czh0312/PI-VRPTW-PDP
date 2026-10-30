"""Frozen cold-start comparison of original and strengthened complete M3 MILPs."""
from __future__ import annotations

import csv
import hashlib
import json
import math
from pathlib import Path
import statistics
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


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def save(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verify(hashes):
    for relative, expected in hashes.items():
        assert sha(ROOT / relative) == expected, relative


def prepare():
    assert not (OUT / 'plan.json').exists(), 'Refusing to overwrite a study'
    jobs = []
    inputs = set()
    for i in range(1, 10):
        case = f'S{i:02}'
        path = ROOT / f'data/formal_benchmark27_uniform_7_20_50_v1/model_3/small/{case}/case.json'
        cfg, _ = load_routing_case(path, use_pool_config=False)
        inputs.add(path)
        inputs.update(ROOT / cfg[key] for key in ('data', 'orders', 'routing_config', 'operating_policy', 'economic_config', 'parameter_contract'))
        for variant in (['baseline', 'optimized'] if i % 2 else ['optimized', 'baseline']):
            jobs.append(dict(case=case, variant=variant, seed=42, seconds=30))
    jobs += [dict(case=f'S{i:02}', variant='optimized', seed=101, seconds=30) for i in range(1, 10)]
    sources = {Path(__file__), ROOT / 'models/common/solver_config.py', ROOT / 'tests/routing/test_model3_route_partition.py'}
    for folder in ('models/routing/model_3/exact', 'models/common', 'models/cost', 'tools/configuration'):
        sources.update((ROOT / folder).rglob('*.py'))
    hashes = {p.relative_to(ROOT).as_posix(): sha(p) for p in sources | inputs}
    save(OUT / 'plan.json', dict(jobs=jobs, hashes=hashes, threads=1, formulation='expanded',
        cold_start=True, historical_solution_or_bounds_used=False,
        time_scope='Case load, all fresh preparation, complete MILP solve and independent final audit; process/runtime preflight excluded.',
        baseline='Current exact solver with route_strengthening=False; same complete original model.',
        optimized='Default exact solver: fresh route bound and internal start; complete unrestricted main MILP.',
        python=sys.version, interpreter=sys.executable, gurobi_version=list(gp.gurobi.version())))
    with zipfile.ZipFile(OUT / 'source_inputs.zip', 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        for relative in sorted(hashes):
            archive.write(ROOT / relative, relative)


def run():
    plan = read(OUT / 'plan.json')
    verify(plan['hashes'])
    rows = []
    for job in plan['jobs']:
        name = f"{job['case']}_{job['variant']}_s{job['seed']}"
        target = OUT / name
        target.mkdir()
        verify(plan['hashes'])
        print('START ' + name, flush=True)
        begun = time.perf_counter()
        cfg, fleet = load_routing_case(ROOT / f"data/formal_benchmark27_uniform_7_20_50_v1/model_3/small/{job['case']}/case.json", use_pool_config=False)
        p = load_instance(cfg, fleet)
        calls = []
        original = gp.Model.optimize

        def observed(model, *args, **kwargs):
            assert not args and not kwargs
            phase = {'m3_input_route_partition_lower_bound': 'route_bound',
                     'm3_fresh_partition_schedule': 'internal_schedule'}.get(model.ModelName, 'full_model')
            model.Params.OutputFlag = 1
            model.Params.LogToConsole = 0
            model.Params.LogFile = (target.relative_to(ROOT) / (phase + '.log')).as_posix()
            before = dict(phase=phase, variables=model.NumVars, binary_variables=model.NumBinVars,
                          constraints=model.NumConstrs+model.NumGenConstrs, starts=model.NumStart)
            try:
                return original(model)
            finally:
                before.update(native_status=model.Status, runtime=model.Runtime, work=model.Work)
                calls.append(before)

        gp.Model.optimize = observed
        try:
            result = solve_exact(p, time_limit=job['seconds'], seed=job['seed'],
                milp_options=dict(threads=1, formulation='expanded'),
                route_strengthening=job['variant'] == 'optimized')
        finally:
            gp.Model.optimize = original
        audit = audit_solution(p, result) if result.get('audit', {}).get('valid') else None
        if audit:
            assert audit['valid'] and audit['periodic_replay']['valid']
        row = dict(job, total_wall_seconds=time.perf_counter()-begun,
            status=result['info']['status'], optimal=result['info']['proven_optimal'],
            audited_feasible=bool(audit), objective=result['info']['objective'],
            best_bound=result['info']['best_bound'], mip_gap=result['info']['mip_gap'],
            info=result['info'], optimize_calls=calls,
            economic_profile_hash=p.cost_profile.hash, history_used=False)
        assert calls[-1]['phase'] == 'full_model'
        if row['optimal']:
            assert calls[-1]['native_status'] == gp.GRB.OPTIMAL and audit
        save(target / 'result.json', result)
        save(target / 'independent_audit.json', audit)
        save(target / 'summary.json', row)
        rows.append(row)
        save(OUT / 'rows.json', rows)
        print(json.dumps({k: row[k] for k in ('case','variant','seed','total_wall_seconds','status','optimal','objective')}, ensure_ascii=False), flush=True)
    verify(plan['hashes'])
    # Historical costs are used only for this post-run comparison.
    old = {r['case']: r for r in read(ROOT / 'output/experiments/m3_m8_small_comparison_20261029_v1/rows.json')}
    for row in rows:
        if row['optimal']:
            assert math.isclose(row['objective'], old[row['case']]['m3_optimal_cost'], abs_tol=1e-6, rel_tol=0)
    optimized = [r for r in rows if r['variant'] == 'optimized']
    stats = dict(calls=len(rows), optimized_calls=len(optimized),
                 optimized_optimal=sum(r['optimal'] for r in optimized),
                 baseline_optimal=sum(r['optimal'] for r in rows if r['variant'] == 'baseline'),
                 optimized_total_seconds=sum(r['total_wall_seconds'] for r in optimized),
                 optimized_min_seconds=min(r['total_wall_seconds'] for r in optimized),
                 optimized_median_seconds=statistics.median(r['total_wall_seconds'] for r in optimized),
                 optimized_max_seconds=max(r['total_wall_seconds'] for r in optimized),
                 original_inputs_and_sources_unchanged=True, historical_costs_checked_after_all_solves=True)
    save(OUT / 'statistics.json', stats)
    fields = ['case','variant','seed','total_wall_seconds','status','optimal','audited_feasible','objective','best_bound','mip_gap']
    with (OUT / 'runs.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)
    print(json.dumps(stats, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    if sys.argv[1:] == ['prepare']:
        prepare()
    elif sys.argv[1:] == ['run']:
        run()
    else:
        raise SystemExit('Use prepare or run')
