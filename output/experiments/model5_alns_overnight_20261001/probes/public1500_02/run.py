"""One direct public M5 call; actual ALNS and one1500s shared deadline."""
from datetime import datetime, timezone
import hashlib, json, os
from pathlib import Path
import shutil, subprocess, sys, time

HERE = Path(__file__).resolve().parent
NIGHT = HERE.parents[1]
ROOT = HERE.parents[4]
def read(p): return json.loads(p.read_text())
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def write(p, v): p.write_text(json.dumps(v, indent=2, ensure_ascii=False, allow_nan=False) + '\n')
def checkpoint(**values):
    obj = read(NIGHT / 'checkpoint.json')
    obj.update(values, updated_at=datetime.now(timezone.utc).isoformat())
    write(NIGHT / 'checkpoint.json', obj)

if sys.argv[1] == 'register':
    assert not (HERE / 'plan.json').exists()
    previous = read(HERE.parent / 'long01/plan.json')
    assert read(HERE / 'smoke_profiles.json')['status'] == 'PASSED'
    assert read(HERE / 'checker_smoke.json')['status'] == 'VERIFIED'
    source = {str(p.relative_to(HERE / 'source_snapshot')): sha(p)
              for p in (HERE / 'source_snapshot').rglob('*.py')}
    assert source == previous['source_sha256']['candidate_source']
    frozen = HERE / 'inputs'
    frozen.mkdir()
    cfg = read(Path(previous['case']))
    bindings = []
    for row in previous['input_bindings']:
        origin = Path(row['origin'])
        target = frozen / Path(row['frozen']).name
        assert sha(origin) == sha(Path(row['frozen'])) == row['sha256']
        shutil.copy2(Path(row['frozen']), target)
        cfg[row['field']] = str(target)
        bindings.append(dict(field=row['field'], origin=str(origin), frozen=str(target), sha256=sha(target)))
    write(frozen / 'case.json', cfg)
    options = {**previous['jobs'][0]['options'], 'seed': 51}
    assert options['time_limit'] == 1500 and 'subset_time_fraction' not in options
    job = dict(id='seed51_public1500', source='source_snapshot', options=options)
    write(HERE / 'plan.json', dict(registered_before_run=True, mode='continuous', jobs=[job],
        case=str(frozen / 'case.json'), input_bindings=bindings,
        input_sha256={str(p.relative_to(HERE)): sha(p) for p in frozen.iterdir()},
        source_sha256={'source_snapshot': source}, declared_source_changes=[],
        runner_sha256=sha(Path(__file__)), validator_sha256=sha(HERE / 'validate.py'),
        checker_sha256={p.name: sha(p) for p in HERE.glob('validation_*.py')},
        smoke_sha256={name: sha(HERE / name) for name in ['smoke_profiles.py', 'smoke_profiles.json',
            'smoke_profiles.log', 'checker_smoke.py', 'checker_smoke.json', 'checker_smoke.log',
            'capacity_repair_regression.log', 'research_note.json']},
        predecessor_plan_sha256=sha(HERE.parent / 'long01/plan.json'), hash_seed=0, python=sys.version,
        scope='One direct public1500s fresh ALNS/hybrid run, additional development seed51 on the same100-customer case. No external initial, historical solution, price or lower bound.',
        hypothesis='The registered25percent subset-reserve live-CG profile may also meet5percent from an independently generated public ALNS initial at a1500s total deadline. One extra seed does not establish population stability.',
        budget_rule='Wall clock includes actual fresh ALNS, hybrid model cleanup and final physical/periodic audit. Input loading and independent proof validation are outside the solver budget.',
        selection_note='Profile and seed51 were fixed before either native seed50 or51 ran, following verified seed48 and unverified seed49 observations. This is an additional development seed on one already-observed instance, not a held-out case or unbiased success-rate estimate; all outcomes remain recorded.',
        gate='Wait for public1500_01 and fastcolumns01 full verification and final seals; no new large runs after local07:00.'))
    print('REGISTERED DIRECT PUBLIC PIPELINE', flush=True)
    sys.exit()

plan = read(HERE / 'plan.json')
assert sha(Path(__file__)) == plan['runner_sha256']
for folder, files in plan['source_sha256'].items():
    for name, digest in files.items(): assert sha(HERE / folder / name) == digest, name
for name, digest in plan['input_sha256'].items(): assert sha(HERE / name) == digest, name
job = plan['jobs'][0]
if sys.argv[1] == 'all':
    for probe in ['public1500_01', 'fastcolumns01']:
        parent = HERE.parent / probe
        assert read(parent / 'validation.json')['status'] == 'VERIFIED'
        seal = read(parent / 'final_verification.json')
        assert seal['status'] == 'VERIFIED'
        for name, digest in seal['artifact_sha256'].items(): assert sha(parent / name) == digest, name
    if datetime.now(timezone.utc) >= datetime(2026, 10, 1, 23, 0, tzinfo=timezone.utc):
        checkpoint(status='STOPPED_AT_NO_NEW_RUN_CUTOFF', running=None,
                   next_step='Public rerun remains registered; local07:00 cutoff reached. Finalize existing proofs only.')
        sys.exit()
    assert not (HERE / 'summary.json').exists() and not (HERE / (job['id'] + '.json')).exists()
    with (HERE / 'run_started.json').open('x') as marker:
        json.dump(dict(started_utc=datetime.now(timezone.utc).isoformat(), conductor_pid=os.getpid()), marker)
    begun = time.perf_counter()
    with (HERE / (job['id'] + '.log')).open('w') as log:
        proc = subprocess.Popen([sys.executable, str(Path(__file__)), job['id']], stdout=log,
            stderr=subprocess.STDOUT, env={**os.environ, 'PYTHONHASHSEED': '0'}, cwd=ROOT)
        checkpoint(status='RUNNING_DIRECT_PUBLIC1500', current_probe=str(HERE),
            running=dict(pid=proc.pid, conductor_pid=os.getpid(), job=job['id'],
                log=str(HERE / (job['id'] + '.log')), started_utc=datetime.now(timezone.utc).isoformat()))
        try:
            rc = proc.wait(timeout=1520)
            failure = 'EXIT_' + str(rc) if rc else None
        except subprocess.TimeoutExpired:
            proc.kill(); proc.wait(); failure = 'OUTER_TIMEOUT'
    row = dict(id=job['id'], job=job, failure=failure, outer_seconds=time.perf_counter()-begun)
    if failure:
        write(HERE / 'failure.json', row)
        checkpoint(status='PUBLIC1500_FAILED', running=None, next_step='Keep original failed run and logs; do not overwrite or restart it.')
        sys.exit(1)
    payload = read(HERE / (job['id'] + '.json')); out = payload['experiment']
    row.update(upper=out['upper_bound'], lower=out['bound'], gap=out['gap'],
        target_reached=out['target_reached'], wall_seconds=payload['wall_seconds'],
        pipeline_wall_seconds=payload['pipeline_wall_seconds'], valid=payload['independent_audit']['valid'], verified=False)
    write(HERE / 'summary.json', [row])
    print('UNVERIFIED DIRECT PUBLIC RESULT', row, flush=True)
    checkpoint(status='PUBLIC1500_NEEDS_VERIFICATION', running=None,
        next_step='Run original public1500_02/validate.py; promote no record until full independent proof checks pass.')
    sys.exit()

assert sys.argv[1] == job['id'] and not (HERE / (job['id'] + '.json')).exists()
sys.path.insert(0, str(HERE / 'source_snapshot'))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.common.cycle.audit import audit_solution
import models.routing.service as service
from models.routing.variants.model_5.exact import solve_hybrid
p = load_instance(*load_routing_case(plan['case'], use_pool_config=False))
actual_service = service.solve_routing
begun = time.perf_counter(); loads = os.getloadavg()
out = solve_hybrid(p, **job['options'], verbose=True, diagnostics_dir=HERE / (job['id'] + '_models'))
assert service.solve_routing is actual_service
audit = audit_solution(p, out['result'])
assert audit['valid'] and audit['periodic_replay']['valid']
wall = time.perf_counter()-begun
assert wall <= 1500.5
write(HERE / (job['id'] + '.json'), dict(job=job, experiment=out, independent_audit=audit,
    wall_seconds=wall, pipeline_wall_seconds=wall, direct_public_entry=True, service_not_monkeypatched=True,
    system_load_at_start=loads, system_load_at_end=os.getloadavg()))
print('DIRECT PUBLIC RESULT', out['upper_bound'], out['bound'], out['gap'], wall, flush=True)
