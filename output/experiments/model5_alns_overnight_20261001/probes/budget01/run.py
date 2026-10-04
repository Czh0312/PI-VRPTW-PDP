"""Registered cover-stage budget comparison on one identical audited incumbent."""
from copy import deepcopy
import datetime, hashlib, json, os
from pathlib import Path
import shutil, subprocess, sys, time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[4]
NIGHT = HERE.parents[1]
OLD = ROOT/'output/experiments/model5_gap_round7_20261001'
CHANGED = 'models/routing/solvers/exact/multibatch_hybrid.py'
def read(p): return json.loads(p.read_text())
def write(p, v): p.write_text(json.dumps(v, ensure_ascii=False, indent=2, allow_nan=False)+'\n')
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def checkpoint(**values):
    obj = read(NIGHT/'checkpoint.json')
    obj.update(values, updated_at=datetime.datetime.now(datetime.timezone.utc).isoformat())
    write(NIGHT/'checkpoint.json', obj)

if sys.argv[1] == 'register':
    assert not (HERE/'plan.json').exists()
    base = read(HERE/'baseline_manifest.json')['source_sha256']
    for name, digest in base.items(): assert sha(HERE/'baseline_source'/name) == digest
    shutil.copytree(HERE/'baseline_source', HERE/'candidate_source')
    shutil.copy2(ROOT/CHANGED, HERE/'candidate_source'/CHANGED)
    frozen = HERE/'inputs'; frozen.mkdir()
    cfg = read(OLD/'inputs/full/case.json'); bindings=[]
    previous = read(OLD/'plan.json')
    for field in ['data', 'orders', 'routing_config', 'dataset_metadata']:
        source = Path(cfg[field]); target = frozen/source.name
        assert sha(source) == previous['input_sha256'][str(source.relative_to(OLD))]
        shutil.copy2(source, target); cfg[field] = str(target)
        bindings.append(dict(field=field, origin=str(source), sha256=sha(source), frozen=str(target)))
    write(frozen/'case.json', cfg)
    payload_path = OLD/'full_candidate_43.json'
    assert sha(payload_path) == read(OLD/'final_verification.json')['artifacts_sha256']['full_candidate_43.json']
    write(frozen/'initial.json', read(payload_path)['experiment']['result'])
    common = dict(time_limit=320.5, alns_time_limit=1, primal_time_limit=0, cover_time_limit=320,
                  conditional_time_limit=0, adaptive_finish=False, target_gap=.05, seed=43)
    jobs = [dict(id='baseline_subset25', source='baseline_source', options=common),
            dict(id='candidate_subset0', source='candidate_source', options={**common, 'subset_time_fraction':0})]
    write(HERE/'plan.json', dict(registered_before_run=True, mode='continuous', jobs=jobs,
        case=str(frozen/'case.json'), initial=str(frozen/'initial.json'), input_bindings=bindings,
        incumbent_origin=str(payload_path), incumbent_origin_sha256=sha(payload_path),
        source_sha256={directory:{str(p.relative_to(HERE/directory)):sha(p)
            for p in (HERE/directory).rglob('*.py')} for directory in ['baseline_source','candidate_source']},
        input_sha256={str(p.relative_to(HERE)):sha(p) for p in frozen.iterdir()},
        checker_sha256={p.name:sha(p) for p in HERE.glob('validation_*.py')},
        runner_sha256=sha(Path(__file__)), validator_sha256=sha(HERE/'validate.py'),
        baseline_manifest_sha256=sha(HERE/'baseline_manifest.json'),
        scope='Paired sequential 320s cover-stage development probes. Both inject the exact same frozen audited incumbent in place of ALNS, and import no historical bounds. No cold-start or population stability claim.',
        hypothesis='Allocating all cover budget to original full-domain pricing may certify more effectively than reserving 25% to restart pricing with selected capacity subsets.',
        declared_source_changes=[CHANGED], python=sys.version, hash_seed=0,
        fallback='Keep default fraction .25; retain failures. Adopt a profile only after independent verification, then test cold.' ))
    print('REGISTERED', [j['id'] for j in jobs], flush=True)
    sys.exit()

plan = read(HERE/'plan.json')
assert sha(Path(__file__)) == plan['runner_sha256']
for directory, hashes in plan['source_sha256'].items():
    for name, digest in hashes.items(): assert sha(HERE/directory/name) == digest
for name, digest in plan['input_sha256'].items(): assert sha(HERE/name) == digest
if sys.argv[1] == 'all':
    assert not (HERE/'summary.json').exists(); summary=[]
    for job in plan['jobs']:
        begun = time.perf_counter()
        with (HERE/(job['id']+'.log')).open('w') as log:
            proc = subprocess.Popen([sys.executable, str(Path(__file__)), job['id']], stdout=log,
                stderr=subprocess.STDOUT, env={**os.environ, 'PYTHONHASHSEED':'0'}, cwd=ROOT)
            checkpoint(status='RUNNING_CONTROLLED_BUDGET_PROBE', running=dict(pid=proc.pid,
                conductor_pid=os.getpid(), job=job['id'], log=str(HERE/(job['id']+'.log')),
                started_utc=datetime.datetime.now(datetime.timezone.utc).isoformat()))
            try:
                rc = proc.wait(timeout=job['options']['time_limit']+20)
                failure = f'EXIT_{rc}' if rc else None
            except subprocess.TimeoutExpired:
                proc.kill(); proc.wait(); failure='OUTER_TIMEOUT'
        row = dict(job=job, failure=failure, outer_seconds=time.perf_counter()-begun)
        if (HERE/(job['id']+'.json')).exists():
            payload = read(HERE/(job['id']+'.json')); out = payload['experiment']
            row.update(upper=out['upper_bound'], lower=out['bound'], gap=out['gap'],
                       target_reached=out['target_reached'], wall_seconds=payload['wall_seconds'],
                       valid=payload['independent_audit']['valid'], verified=False)
        summary.append(row); write(HERE/'summary.json', summary)
        print('UNVERIFIED PROBE RESULT', row, flush=True)
        checkpoint(status='BUDGET_PROBE_NEEDS_VERIFICATION', running=None, next_step='Verify all pricing matrices, dual repairs, independent full schedule replay and frozen inputs; do not promote unverified results.')
    sys.exit()

job = next(j for j in plan['jobs'] if j['id'] == sys.argv[1])
assert not (HERE/(job['id']+'.json')).exists()
sys.path.insert(0, str(HERE/job['source']))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.common.cycle.audit import audit_solution
import models.routing.service as service
from models.routing.variants.model_5.exact import solve_hybrid
p = load_instance(*load_routing_case(plan['case'], use_pool_config=False))
initial = read(Path(plan['initial'])); a = audit_solution(p, initial)
assert a['valid'] and a['periodic_replay']['valid']
calls = []
def fixed_incumbent(**kw):
    assert kw['model'] == 5 and kw['method'] == 'alns'
    calls.append(dict(method='fixed_frozen_incumbent', requested_seed=kw['seed']))
    return deepcopy(initial)
service.solve_routing = fixed_incumbent
begun = time.perf_counter(); loads = os.getloadavg()
out = solve_hybrid(p, **job['options'], verbose=True, diagnostics_dir=HERE/(job['id']+'_models'))
assert len(calls)==1 and out['initial_result']==initial
audit = audit_solution(p, out['result'])
assert audit['valid'] and audit['periodic_replay']['valid']
assert not out['proven_optimal'] and not out['proven_infeasible']
write(HERE/(job['id']+'.json'), dict(job=job, experiment=out, independent_audit=audit,
    incumbent_injection=calls, wall_seconds=time.perf_counter()-begun,
    system_load_at_start=loads, system_load_at_end=os.getloadavg()))
print('COVER PROBE', job['id'], out['upper_bound'], out['bound'], out['gap'], flush=True)
