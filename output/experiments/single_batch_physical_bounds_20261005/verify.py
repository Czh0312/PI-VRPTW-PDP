"""Independent frozen-MPS replay and full-original schedule audit; no timing reruns."""
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json
import math
import sys
import time

OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(OUT/'candidate_source'))
import gurobipy as gp
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.common.cycle.audit import audit_solution
from models.routing.solvers.exact.single_batch_columns import problem_fingerprint
from models.routing.solvers.exact.single_batch_physical_bounds import build_physical_site, project_original_site, site_costs


def read(p): return json.loads(Path(p).read_text())
def write(p, v): Path(p).write_text(json.dumps(v, ensure_ascii=False, indent=2, allow_nan=False)+'\n')
def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def main():
    target = OUT/'verification'; target.mkdir()
    begun = time.perf_counter(); hashes = 0
    for label in ('baseline', 'candidate'):
        for rel, value in read(OUT/f'{label}_manifest.json').items():
            assert sha(OUT/f'{label}_source'/rel) == value
            hashes += 1
    for rel, value in read(OUT/'input_manifest.json').items():
        assert sha(OUT/rel) == value
        hashes += 1
    assert sha(OUT/'driver_frozen.py') == read(OUT/'preparation.json')['driver_sha256']
    assert sha(OUT/'plan.json') == read(OUT/'preparation.json')['plan_sha256']
    jobs = [read(p) for p in read(OUT/'jobs.json')]
    assert len(jobs) == len(read(OUT/'comparison.json')) == 24
    rows, original_audits, checks, templates = [], [], [], {}
    for job in jobs:
        cfg, fleet = load_routing_case(job['input']); p = load_instance(cfg, fleet)
        initial = read(job['start']); result = read(job['result']); row = read(job['row'])
        assert problem_fingerprint(p) == job['original_fp'] == row['original_fp']
        assert sha(job['input']) == job['input_sha256'] == row['input_sha256']
        assert sha(job['start']) == job['start_sha256'] == row['start_sha256']
        assert sha(job['result']) == row['result_sha256']
        assert row['python_hash_seed'] == 0
        a, b = audit_solution(p, initial), audit_solution(p, result)
        assert a['valid'] and b['valid'] and a['periodic_replay']['valid'] and b['periodic_replay']['valid']
        expected = {(o.id, kind) for o in p.orders for kind in ('d', 'p')}
        actual = [(e['order_id'], e['kind']) for t in result['e2_trips'] for e in t['events']]
        assert len(actual) == len(expected) == 40 and set(actual) == expected
        assert not result['info']['proven_infeasible']
        assert row['upper'] <= a['objective']+1e-6 and row['lower'] <= row['upper']+1e-5
        assert math.isclose(row['upper'], b['objective'], abs_tol=1e-6)
        assert math.isclose(row['gap'], max(0, row['upper']-row['lower'])/max(1e-9, abs(row['upper'])), abs_tol=1e-6)
        cert = result['info'].get('physical_site_certificate')
        assert bool(cert) == (job['method'] == 'physical')
        if cert:
            assert not cert['proven_optimal'] and not cert['proven_infeasible']
            assert cert['problem_sha256'] == job['original_fp']
            assert math.isclose(cert['bound'], math.fsum(x['bound'] for x in cert['sites'].values()), abs_tol=1e-7)
            for site, record in cert['sites'].items():
                assert record['bound'] <= site_costs(p, result)[site]+1e-6
                if record['bound'] <= 0:
                    continue
                assert math.isclose(record['bound'], max(0, record['raw_bound']-record['margin']), abs_tol=1e-7)
                key = (job['case'], site)
                if key not in templates:
                    fresh, context = build_physical_site(p, site)
                    try:
                        base = {v.VarName:(v.LB, v.UB, v.Obj, v.VType) for v in fresh.getVars()}
                        projected = project_original_site(p, context, initial, fix=True)
                        fresh.update()
                        fixed = {v.VarName:(v.LB, v.UB) for v in fresh.getVars()}
                        templates[key] = (base, fixed, projected, fresh.NumConstrs, fresh.NumGenConstrs)
                    finally:
                        fresh.dispose()
                base, fixed, projected, nrows, ngens = templates[key]
                path = Path(record['model_file'])
                assert path.is_relative_to(Path(job['run'])) and path.exists()
                item = dict(case=job['case'], seed=job['seed'], budget=job['budget'], site=site,
                    claimed_bound=record['bound'], model_file=str(path), model_sha256=sha(path))
                with gp.Env(empty=True) as env:
                    env.setParam('OutputFlag', 0); env.start()
                    m = gp.read(str(path), env=env)
                    try:
                        assert m.NumVars == len(base) and m.NumConstrs == nrows and m.NumGenConstrs == ngens
                        assert {v.VarName for v in m.getVars()} == set(base)
                        for v in m.getVars():
                            lo, hi, obj, kind = base[v.VarName]
                            assert all(math.isclose(x,y,rel_tol=1e-9,abs_tol=1e-8) for x,y in ((v.LB,lo),(v.UB,hi),(v.Obj,obj)))
                            assert v.VType == kind
                        m.Params.Threads = 1; m.Params.Seed = job['seed']+101
                        m.Params.DualReductions = 0
                        m.Params.IntFeasTol = m.Params.FeasibilityTol = 1e-9
                        m.Params.MIPGap = m.Params.MIPGapAbs = 0
                        m.Params.TimeLimit = 5
                        m.Params.OutputFlag = 1; m.Params.LogToConsole = 0
                        m.Params.LogFile = str(target/f'price_{len(checks)}.log')
                        m.optimize()
                        lb = float(m.ObjBound)
                        item.update(status=int(m.Status), replay_bound=lb if math.isfinite(lb) and abs(lb)<1e90 else None,
                                    replay_runtime=m.Runtime, supported=math.isfinite(lb) and abs(lb)<1e90 and record['bound'] <= lb+1e-6)
                        for v in m.getVars():
                            v.LB, v.UB = fixed[v.VarName]
                        m.Params.TimeLimit = 5
                        m.Params.LogFile = str(target/f'projection_{len(checks)}.log')
                        m.optimize()
                        item.update(projection_status=int(m.Status), projection_runtime=m.Runtime,
                                    original_site_cost=projected['cost'], all_site_tasks=projected['tasks'])
                        assert m.Status == gp.GRB.OPTIMAL and math.isclose(m.ObjVal, projected['cost'], abs_tol=1e-6)
                    finally:
                        m.dispose()
                checks.append(item)
                write(target/'site_checks.json', checks)
                print(f"Physical proof {len(checks)}: {job['case']} {site}, supported={item['supported']}", flush=True)
        original_audits.append(dict(case=job['case'], seed=job['seed'], budget=job['budget'], method=job['method'],
                                   full_audit=True, periodic_replay=True, all40tasks=True))
        rows.append(row)
    groups = {}
    for row in rows:
        groups.setdefault((row['case'],row['seed'],row['budget']),{})[row['method']] = row
    pairs = []
    for (case,seed,budget), group in groups.items():
        assert set(group) == {'baseline','physical'}
        base, candidate = group['baseline'],group['physical']
        assert base['original_fp'] == candidate['original_fp'] and base['start_sha256'] == candidate['start_sha256']
        pairs.append(dict(case=case,seed=seed,budget=budget,baseline=base,physical=candidate,
                          gap_improvement=base['gap']-candidate['gap']))
    assert len(pairs)==12
    wins = [p for p in pairs if p['gap_improvement']>1e-5]
    losses = [p for p in pairs if p['gap_improvement'] < -1e-5]
    gap_gate = (len(wins)>=8 and not losses and len({p['case'] for p in wins})==2
                and {p['seed'] for p in wins}=={42,43,44} and {p['budget'] for p in wins}=={15,30})
    speed_gate = all(p['baseline']['proven_optimal'] and p['physical']['proven_optimal'] for p in pairs)
    for case,budget in {(p['case'],p['budget']) for p in pairs}:
        subset = [p for p in pairs if p['case']==case and p['budget']==budget]
        for metric in ('runtime','work'):
            def value(row):
                return row['runtime'] if metric=='runtime' else row['kernel_work']+row['component_work']
            speed_gate = speed_gate and sum(value(p['physical']) for p in subset) <= .9*sum(value(p['baseline']) for p in subset)
    loads = [read(p) for p in (OUT/'runs').rglob('load_*.json')]
    summary = dict(status='VERIFIED' if all(c['supported'] for c in checks) else 'SOME_SITE_BOUNDS_NOT_INDEPENDENTLY_CONFIRMED',
        hashes_checked=hashes,original_outputs_audited=24,original_initials_audited=24,all_task_and_periodic_replays=24,
        positive_site_checks=len(checks),supported_site_checks=sum(c['supported'] for c in checks),
        fixed_mps_projections=len(checks),pairs=12,gap_wins=len(wins),gap_losses=len(losses),
        acceptance_gate=bool((gap_gate or speed_gate) and all(c['supported'] for c in checks)),production_default_adopted=False,
        registered_seconds=540,actual_runtime=sum(r['runtime'] for r in rows),
        process_wall=sum(x.get('process_wall',0) for x in loads),
        maximum_total_overrun=max(r['runtime']-r['budget'] for r in rows),
        component_runtime=sum(r['component_runtime'] for r in rows),
        minimum_load1=min(x['loadavg'][0] for x in loads),maximum_load1=max(x['loadavg'][0] for x in loads),
        verification_wall=time.perf_counter()-begun,verification_pricing_runtime=sum(c['replay_runtime'] for c in checks),
        verification_projection_runtime=sum(c['projection_runtime'] for c in checks),
        caveat='MPS replay is independent optimization of the frozen formulation, not a symbolic proof. Independent tiny raw-route enumeration and original-integer oracle tests are separate; uncontrolled host load prevents clean causal timing attribution.')
    write(target/'audits.json',original_audits);write(target/'pairs.json',pairs);write(target/'summary.json',summary)
    print(json.dumps(summary,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
