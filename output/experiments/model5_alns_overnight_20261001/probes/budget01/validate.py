"""Independent complete-domain proof replay for the controlled budget probe."""
import json, math
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE/'candidate_source')); sys.path.insert(0, str(HERE))
import validation_core as check
import validation_conjunction as models
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.solvers.exact.multibatch_bounds import distance_certificate
from models.routing.solvers.exact.multibatch_route_pricing import route_context

plan = check.read(HERE/'plan.json')
assert check.sha(HERE/'run.py') == plan['runner_sha256']
assert check.sha(Path(__file__)) == plan['validator_sha256']
assert check.sha(HERE/'baseline_manifest.json') == plan['baseline_manifest_sha256']
for directory, hashes in plan['source_sha256'].items():
    for name, digest in hashes.items(): assert check.sha(HERE/directory/name)==digest
for name, digest in plan['input_sha256'].items(): assert check.sha(HERE/name)==digest
for name, digest in plan['checker_sha256'].items(): assert check.sha(HERE/name)==digest
for origin in check.read(HERE/'validation_sources.json')['origins'].values():
    assert check.sha(Path(origin['source']))==origin['sha256']
base, candidate = (plan['source_sha256'][name] for name in ['baseline_source','candidate_source'])
assert set(base)==set(candidate)
assert {name for name in base if base[name]!=candidate[name]}==set(plan['declared_source_changes'])
assert check.sha(Path(plan['incumbent_origin']))==plan['incumbent_origin_sha256']
initial = check.read(Path(plan['initial']))
assert initial == check.read(Path(plan['incumbent_origin']))['experiment']['result']
for binding in plan['input_bindings']:
    assert check.sha(Path(binding['origin']))==check.sha(Path(binding['frozen']))==binding['sha256']
summary = check.read(HERE/'summary.json')
assert [r['job'] for r in summary] == plan['jobs'] and not any(r['failure'] for r in summary)
checks, cases, audits = [], [], 0
for item in summary:
    job = item['job']; payload = check.read(HERE/(job['id']+'.json')); out = payload['experiment']
    p = load_instance(*load_routing_case(plan['case'], use_pool_config=False)); shortest = check.floyd(p)
    assert out['initial_result']==initial
    a, b = check.checked_audit(p, out['initial_result']), check.checked_audit(p, out['result']); audits+=2
    check.close(a['objective'], b['objective'])
    assert payload['wall_seconds'] <= job['options']['time_limit']+.5
    assert not out['proven_optimal'] and not out['proven_infeasible']
    assert out['conditional_certificate'] is None and out['adaptive_certificate'] is None
    assert [s['stage'] for s in out['stages']] == ['alns','route_cover']
    ref = out['reference_certificate']; baseline = distance_certificate(p)
    assert ref['distance_certificate']==baseline
    dest = HERE/(job['id']+'_models')
    checks.append(check.verify_e1(p, out['result'], ref['e1_certificate'], dest/'reference/e1_inventory.mps'))
    for number, row in enumerate(ref['sites']):
        context = check.independent_context(p, row['satellite'], shortest)
        assert context == route_context(p,row['satellite'])
        check.check_site(p, row, context)
        promoted = any(r['certificate']==row for r in ref.get('subset_runs',[]))
        if not promoted:
            models.verify_pricing_models(p,row,context,dest/'reference'/f'site_{number}_best_pricing.mps')
    for number, row in enumerate(ref.get('subset_runs',[])):
        assert not row['restricted_objective_used_as_bound']
        context = check.independent_context(p,row['satellite'],shortest)
        check.check_site(p,row['certificate'],context)
        models.verify_pricing_models(p,row['certificate'],context,dest/f'subsets_{number}_pricing.mps')
    if job['id']=='candidate_subset0': assert ref['subset_runs']==[]
    e1 = math.fsum(max(0,r['bound']-1e-7*max(1,r['bound'])) for r in baseline['components'] if r['echelon']=='E1')
    check.close(ref['e1_bound'],max(e1,ref['e1_certificate']['bound']))
    distances = {r['satellite']:max(0,r['bound']-1e-7*max(1,r['bound']))
                 for r in baseline['components'] if r['echelon']=='E2'}
    rows = {r['satellite']:r for r in ref['sites']}
    assert set(rows)==set(distances)=={c['satellite'] for c in ref['components']}
    for component in ref['components']:
        site = component['satellite']
        check.close(component['distance_bound'], distances[site])
        check.close(component['route_cover_bound'], rows[site]['bound'])
        check.close(component['bound'], max(distances[site],rows[site]['bound']))
    check.close(ref['raw_bound'],ref['e1_bound']+math.fsum(c['bound'] for c in ref['components']))
    check.close(ref['bound'], max(baseline['bound'],max(0,ref['raw_bound']-1e-7*max(1,abs(ref['raw_bound'])))))
    check.close(out['bound'],ref['bound']); check.close(out['upper_bound'],b['objective'])
    check.close(out['gap'],max(0,out['upper_bound']-out['bound'])/abs(out['upper_bound']))
    assert out['bound']<=out['upper_bound'] and out['target_reached']==(out['gap']<=.05)
    cases.append(dict(id=job['id'],upper=out['upper_bound'],lower=out['bound'],gap_percent=100*out['gap'],
                      target_reached=out['target_reached'],wall_seconds=payload['wall_seconds'],
                      site_bounds={r['satellite']:r['bound'] for r in ref['sites']},
                      system_load_at_start=payload['system_load_at_start'],system_load_at_end=payload['system_load_at_end']))
    print('VERIFIED COVER PROBE', cases[-1],flush=True)
checks += models.models
delta = cases[1]['lower']-cases[0]['lower']
out = dict(status='VERIFIED',cases=cases,lower_bound_delta=delta,whole_cycle_audits=audits,
           replayed_columns=check.capacity.columns,dual_repairs=check.capacity.repair_checks,model_checks=checks,
           interpretation='Same source except optional budget control, same frozen full incumbent and input, sequential equal 320s stage ceilings, one seed. Wall time and machine load vary; this is development evidence, not cold-start or stability evidence.')
(HERE/'validation.json').write_text(json.dumps(out,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
print('VERIFIED',delta,'models',len(checks),flush=True)
