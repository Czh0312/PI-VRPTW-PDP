"""Registered continuation: full-domain E2 certification, then pool-specific M5."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
PREVIOUS = ROOT/'output/experiments/model5_gap_round4_20261001'

def read(path): return json.loads(path.read_text())
def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n')
def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()

if sys.argv[1] == 'register':
    assert not (HERE/'plan.json').exists()
    for source in [*ROOT.glob('models/**/*.py'), *ROOT.glob('configuration/*.py')]:
        target = HERE/'source_snapshot'/source.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(source, target)
    inputs = []
    for number, label in [(2, 'full'), (3, 'tight')]:
        directory = HERE/'inputs'/label; directory.mkdir(parents=True)
        for source, name in [(PREVIOUS/'fresh/inputs'/('zhou_Set2_100-5-1b_'+label)/'case.json', 'case.json'),
                             (PREVIOUS/'column_recovery'/f'0{number}_zhou_Set2_100-5-1b_{label}_43.json', 'previous.json')]:
            shutil.copy2(source, directory/name)
            inputs.append(dict(file=str((directory/name).relative_to(HERE)), sha256=sha(directory/name),
                               source=str(source), source_sha256=sha(source)))
    write(HERE/'plan.json', dict(registered_before_run=True, mode='continuous', seed=43,
        family='zhou_Set2_100-5-1b_b3', cases=['full', 'tight'], target_gap=.05,
        E2_total_seconds=720, E2_priority='largest restricted-master minus certified-floor deficit; equal remaining shares',
        E2_pricing_seconds=8, E2_heuristic=True, shared_E2_requires_complete_domain_equality=True,
        joint_seconds=30, conditional_seconds_per_failed_pool=240, max_conditions=6,
        primal_seconds_per_failed_pool=150, primal_columns=128, extra_columns_per_slot=128,
        scope='Additional warm-start budgets, not a cold start or a same-budget ALNS comparison. All attempts retained. Restricted objectives select scheduling only, never certify bounds.',
        inputs=inputs, source_sha256={str(p.relative_to(HERE/'source_snapshot')): sha(p)
                    for p in (HERE/'source_snapshot').rglob('*.py')}, runner_sha256=sha(Path(__file__))))
    print('REGISTERED', flush=True); sys.exit()

plan = read(HERE/'plan.json')
assert sha(Path(__file__)) == read(HERE/'amendment.json')['runner_sha256']
for name, digest in plan['source_sha256'].items(): assert sha(HERE/'source_snapshot'/name) == digest
for item in plan['inputs']: assert sha(HERE/item['file']) == item['sha256']
sys.path.insert(0, str(HERE/'source_snapshot'))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.common.cycle.audit import audit_solution
from models.routing.solvers.exact.multibatch_route_pricing import route_context, evaluate_column
from models.routing.solvers.exact.multibatch_route_cg import site_route_cover
from models.routing.solvers.exact.multibatch_conditional_inventory import solve_conditional_inventory, refine_conditional_bound
from models.routing.solvers.exact.multibatch_route_pool import reoptimize_route_pool
import math

problems = {label: load_instance(*load_routing_case(str(HERE/'inputs'/label/'case.json'), use_pool_config=False))
            for label in plan['cases']}
prior = {label: read(HERE/'inputs'/label/'previous.json') for label in plan['cases']}
p, q = problems['full'], problems['tight']
equivalence = []
contexts = {}
for site in p.satellites:
    left, right = route_context(p, site), route_context(q, site)
    contexts[site] = left
    assert left == right
    assert (p.fleet.e2_weight_cap, p.fleet.e2_volume_cap) == (q.fleet.e2_weight_cap, q.fleet.e2_volume_cap)
    for field in ('cycle_start', 'cycle_end', 'e2_return_unload_time', 'e2_turnaround_time'):
        assert getattr(p.operating_policy, field) == getattr(q.operating_policy, field)
    assert p.operating_policy.batches[-1].cutoff == q.operating_policy.batches[-1].cutoff
    locations = [site]+list({n['order'].customer for n in left['nodes']})
    assert all(p.distance(a,b) == q.distance(a,b) and p.travel(a,b) == q.travel(a,b)
               for a in locations for b in locations)
    equivalence.append(dict(site=site, complete_context_and_caps_and_raw_arcs_equal=True))

if sys.argv[1] == 'e2':
    assert not (HERE/'e2.json').exists()
    ref = deepcopy(prior['full']['reference_certificate'])
    seed_columns = {s: [] for s in p.satellites}
    for payload in prior.values():
        rows = payload['reference_certificate']['sites']
        rows += [r['certificate'] for r in payload['reference_certificate'].get('subset_runs', [])]
        rows += [r['certificate'] for r in payload['condition_leaves']]
        for row in rows:
            seed_columns[row['satellite']].extend(row['columns'])
        for trip in payload['result']['e2_trips']:
            seed_columns[trip['satellite_id']].append(dict(tasks=[(e['order_id'],e['kind']) for e in trip['events']]))
    seed_columns = {site: list({tuple(tuple(k) for k in c['tasks']): evaluate_column(p, contexts[site],c['tasks'])
                        for c in rows}.values()) for site, rows in seed_columns.items()}
    assert all(c is not None for rows in seed_columns.values() for c in rows)
    write(HERE/'seed_columns.json', seed_columns)
    def deficit(row):
        objectives = [i['master_objective'] for i in row['iterations'] if i.get('phase')=='distance']
        return max(0, objectives[-1]-row['bound']) if objectives else 0
    pending = sorted([r for r in ref['sites'] if not r['lp_converged']], key=lambda r: (-deficit(r), r['satellite']))
    begun = time.perf_counter(); deadline = begun+plan['E2_total_seconds']; runs = []
    for number, old in enumerate(pending):
        site = old['satellite']; now = time.perf_counter()
        cuts = next((r['cuts'] for r in ref.get('subset_runs', []) if r['satellite']==site), [])
        bag = dict(e2_trips=[dict(satellite_id=site, events=[dict(order_id=o,kind=k) for o,k in c['tasks']])
                            for c in seed_columns[site]])
        leaf = site_route_cover(p, route_context(p,site), deadline=now+max(0,deadline-now)/(len(pending)-number),
            initial_result=bag, seed=plan['seed'], batch_pricing=True, dual_repair=True,
            delivery_count_cuts=True, subset_cuts=cuts, pricing_time_limit=plan['E2_pricing_seconds'],
            heuristic_pricing=plan['E2_heuristic'], pricing_export_path=HERE/'e2_models'/f'{site}_pricing.mps')
        runs.append(dict(satellite=site, previous_bound=old['bound'], diagnostic_deficit=deficit(old),
                         initial_columns=len(seed_columns[site]), cuts=cuts, certificate=leaf))
        if leaf['bound'] > old['bound']:
            ref['sites'][ref['sites'].index(old)] = leaf
            component = next(c for c in ref['components'] if c['satellite']==site)
            component.update(route_cover_bound=leaf['bound'], bound=max(component['bound'],leaf['bound']))
        write(HERE/'e2_progress.json', dict(runs=runs, wall_seconds=time.perf_counter()-begun))
        print('SITE',site,'old',old['bound'],'new',leaf['bound'],'status',leaf['status'],'seconds',leaf['runtime'],flush=True)
    write(HERE/'e2.json', dict(reference=ref, runs=runs, equivalence=equivalence, wall_seconds=time.perf_counter()-begun))
    sys.exit()

label = sys.argv[1]
assert label in plan['cases'] and not (HERE/(label+'.json')).exists()
p = problems[label]; initial = prior[label]['result']; audit = audit_solution(p,initial)
assert audit['valid'] and audit['periodic_replay']['valid']
shared = read(HERE/'e2.json'); ref = deepcopy(prior[label]['reference_certificate'])
ref['sites'] = deepcopy(shared['reference']['sites'])
for component in ref['components']:
    candidate = next(c for c in shared['reference']['components'] if c['satellite']==component['satellite'])
    assert candidate['distance_bound'] == component['distance_bound']
    component.update(route_cover_bound=candidate['route_cover_bound'], bound=max(component['bound'],candidate['bound']))
raw = ref['e1_bound']+math.fsum(c['bound'] for c in ref['components'])
ref.update(raw_bound=raw, bound=max(ref['bound'],max(0,raw-1e-7*max(1,abs(raw)))))
begun = time.perf_counter(); lower = max(prior[label]['bound'],ref['bound'])
conditions = prior[label]['condition_leaves']; floors = {c['satellite']:c['bound'] for c in ref['components']}
master = solve_conditional_inventory(p,floors,[{k:v for k,v in c.items() if k!='certificate'} for c in conditions],
    deadline=time.perf_counter()+plan['joint_seconds'], initial_result=initial, e1_bound=ref['e1_bound'],
    fallback_bound=lower, seed=plan['seed'], diagnostics_path=HERE/(label+'_models')/'joint.mps.gz')
lower=max(lower,master['bound']); conditional=None; result=deepcopy(initial)
def gap(): return max(0,audit['objective']-lower)/max(1e-9,abs(audit['objective']))
if gap()>plan['target_gap']:
    conditional=refine_conditional_bound(p,ref,initial,deadline=time.perf_counter()+plan['conditional_seconds_per_failed_pool'],
        seed=plan['seed'],target_gap=plan['target_gap'],max_conditions=plan['max_conditions'],
        diagnostics_dir=HERE/(label+'_models')/'conditional')
    lower=max(lower,conditional['bound'])
if gap()>plan['target_gap']:
    additional={site: [c for row in shared['reference']['sites']+[r['certificate'] for r in shared['runs']]
                       if row['satellite']==site for c in row['columns']] for site in p.satellites}
    result=reoptimize_route_pool(p,initial,time_limit=plan['primal_seconds_per_failed_pool'],seed=plan['seed'],
        transfers=True,exchanges=True,recombinations=True,max_columns=plan['primal_columns'],max_passes=4,
        e1_backend='patterns',additional_columns=additional,additional_columns_per_slot=plan['extra_columns_per_slot'],
        diagnostics_dir=HERE/(label+'_models')/'primal')
    audit=audit_solution(p,result)
assert audit['valid'] and audit['periodic_replay']['valid'] and lower<=audit['objective']+1e-6
write(HERE/(label+'.json'),dict(label=label,result=result,audit=audit,reference_certificate=ref,
    joint_certificate=master,previous_conditions=conditions,conditional_certificate=conditional,
    upper_bound=audit['objective'],bound=lower,gap=gap(),wall_seconds=time.perf_counter()-begun,
    target_reached=gap()<=plan['target_gap'],shared_E2_seconds=shared['wall_seconds']))
print(label,'UB',audit['objective'],'LB',lower,'GAP',gap(),flush=True)
