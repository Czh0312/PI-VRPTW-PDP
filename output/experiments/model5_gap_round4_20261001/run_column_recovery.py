"""Additional registered 150s: equivalent E2 floors, joint LB, global columns UB."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
DEST = HERE/'column_recovery'
FRESH = HERE/'fresh'


def read(path): return json.loads(path.read_text())
def write(path, value): path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n')
def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def material(ident):
    fresh = read(FRESH/(ident+'.json'))
    ref, conditions, result, bound = (fresh['experiment'][k] for k in
        ('reference_certificate', 'conditional_certificate', 'result', 'bound'))
    sources = [FRESH/(ident+'.json')]
    extra = HERE/'adaptive'/(ident+'.json')
    if extra.exists():
        x = read(extra)['experiment']; ref, result, bound = x['reference_certificate'], x['result'], x['bound']
        sources.append(extra)
    return fresh, ref, conditions, result, bound, sources


if len(sys.argv) > 1:
    sys.path.insert(0, str(DEST/'source_snapshot'))
    from configuration.loader import load_routing_case
    from models.routing.common.cycle import load_instance
    from models.routing.common.cycle.audit import audit_solution
    from models.routing.solvers.exact.multibatch_route_pricing import route_context
    from models.routing.solvers.exact.multibatch_conditional_inventory import solve_conditional_inventory
    from models.routing.solvers.exact.multibatch_route_pool import reoptimize_route_pool
    import math
    job = read(DEST/'jobs.json')[int(sys.argv[1])]
    for name, digest in job['source_sha256'].items(): assert sha(Path(name)) == digest
    original, reference, conditions, initial, lower, _ = material(job['id'])
    reference = deepcopy(reference)
    p = load_instance(*load_routing_case(original['job']['case'], use_pool_config=False))
    start = time.perf_counter(); deadline = start+150
    equivalences = []
    if job['full_source']:
        source, source_ref, source_cond, _, _, _ = material(job['full_source'])
        q = load_instance(*load_routing_case(source['job']['case'], use_pool_config=False))
        assert p.fleet.e2_weight_cap == q.fleet.e2_weight_cap and p.fleet.e2_volume_cap == q.fleet.e2_volume_cap
        for key in ('cycle_start', 'cycle_end', 'e2_return_unload_time', 'e2_turnaround_time'):
            assert getattr(p.operating_policy, key) == getattr(q.operating_policy, key)
        assert p.operating_policy.batches[-1].cutoff == q.operating_policy.batches[-1].cutoff
        for site in p.satellites:
            context = route_context(p, site); other = route_context(q, site)
            assert context == other
            locations = [site]+list({n['order'].customer for n in context['nodes']})
            assert all(p.distance(a, b) == q.distance(a, b) and p.travel(a, b) == q.travel(a, b)
                       for a in locations for b in locations)
            candidate = next(r for r in source_ref['sites'] if r['satellite'] == site)
            current = next(r for r in reference['sites'] if r['satellite'] == site)
            equivalences.append(dict(site=site, complete_context_and_raw_arcs_equal=True,
                                      source_bound=candidate['bound'], target_bound=current['bound']))
            if candidate['bound'] > current['bound']:
                reference['sites'][reference['sites'].index(current)] = deepcopy(candidate)
                component = next(c for c in reference['components'] if c['satellite'] == site)
                component.update(route_cover_bound=candidate['bound'], bound=max(component['bound'], candidate['bound']))
        extra_conditions = (source_cond or {}).get('conditions', [])
    else:
        extra_conditions = []
    raw = reference['e1_bound']+math.fsum(c['bound'] for c in reference['components'])
    reference.update(raw_bound=raw, bound=max(reference['bound'], max(0, raw-1e-7*max(1, abs(raw)))))
    lower = max(lower, reference['bound'])
    leaves = (conditions or {}).get('conditions', [])+extra_conditions
    rows = [{k: v for k, v in c.items() if k != 'certificate'} for c in leaves]
    unique = {(r['site'], r['batch'], r['threshold']): r for r in rows}
    floors = {c['satellite']: c['bound'] for c in reference['components']}
    master = solve_conditional_inventory(p, floors, list(unique.values()), deadline=min(deadline, start+30),
        initial_result=initial, e1_bound=reference['e1_bound'], fallback_bound=lower, seed=43,
        verbose=True, diagnostics_path=DEST/(job['id']+'_models')/'joint.mps.gz')
    lower = max(lower, master['bound'])
    result = reoptimize_route_pool(p, initial, time_limit=max(1e-12, min(120, deadline-time.perf_counter())),
        seed=43, exchanges=True, recombinations=True, max_columns=128, max_passes=4, e1_backend='patterns',
        additional_columns={r['satellite']: r['columns'] for r in reference['sites']}, additional_columns_per_slot=128,
        verbose=True, diagnostics_dir=DEST/(job['id']+'_models')/'primal')
    audit = audit_solution(p, result)
    assert audit['valid'] and audit['periodic_replay']['valid'] and lower <= audit['objective']+1e-6
    upper = audit['objective']
    write(DEST/(job['id']+'.json'), dict(job=job, result=result, independent_audit=audit,
        bound=lower, upper_bound=upper, gap=max(0, upper-lower)/max(1e-9, abs(upper)),
        equivalences=equivalences, reference_certificate=reference, joint_certificate=master,
        condition_leaves=leaves, wall_seconds=time.perf_counter()-start))
else:
    if not DEST.exists():
        DEST.mkdir()
        for file in [*ROOT.glob('models/**/*.py'), *ROOT.glob('configuration/*.py')]:
            target = DEST/'source_snapshot'/file.relative_to(ROOT)
            target.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(file, target)
        write(DEST/'plan.json', dict(time_limit=150, joint_time_limit=30, primal_time_limit=120, seed=43,
            extra_per_slot=128, ordinary_per_slot=128, registered_before_run=True,
            scope='Additional budget, NOT part of the original 660s. Every completed failed case is retained. Equivalent E2 pricing domains may share floors; E1 inventory remains pool-specific.',
            source_sha256={str(p.relative_to(DEST/'source_snapshot')): sha(p) for p in (DEST/'source_snapshot').rglob('*.py')}))
    for name, digest in read(DEST/'plan.json')['source_sha256'].items(): assert sha(DEST/'source_snapshot'/name) == digest
    jobs = read(DEST/'jobs.json') if (DEST/'jobs.json').exists() else []
    summary = read(DEST/'summary.json') if (DEST/'summary.json').exists() else []
    known = {j['id'] for j in jobs}
    completed = read(FRESH/'summary.json')
    for record in completed:
        ident = record['job']['id']
        if ident in known or record['failure']: continue
        adaptive = HERE/'adaptive'/(ident+'.json')
        gap = read(adaptive)['gap'] if adaptive.exists() else record['gap']
        if gap <= .05: continue
        source = next((r['job']['id'] for r in completed if r['job']['family'] == record['job']['family']
                       and r['job']['pool_label'] == 'full' and r['job']['id'] != ident and not r['failure']), None)
        _, _, _, _, _, files = material(ident)
        if source: files += material(source)[-1]
        job = dict(id=ident, full_source=source, source_sha256={str(f): sha(f) for f in files})
        jobs.append(job); write(DEST/'jobs.json', jobs)
        with (DEST/(ident+'.log')).open('w') as log:
            try:
                run = subprocess.run([sys.executable, str(Path(__file__)), str(len(jobs)-1)],
                    stdout=log, stderr=subprocess.STDOUT, timeout=170, env={**os.environ, 'PYTHONHASHSEED': '0'})
                failure = f'EXIT_{run.returncode}' if run.returncode else None
            except subprocess.TimeoutExpired: failure = 'OUTER_TIMEOUT'
        result = dict(id=ident, failure=failure)
        if failure is None:
            x = read(DEST/(ident+'.json')); result.update(bound=x['bound'], upper_bound=x['upper_bound'], gap=x['gap'], wall_seconds=x['wall_seconds'])
        summary.append(result); write(DEST/'summary.json', summary); print(result, flush=True)
