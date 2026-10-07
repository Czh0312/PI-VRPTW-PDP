"""Pre-registered followup: matched primal-focus pair and one native M5 run."""
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
PARENT = ROOT/'output/experiments/model5_hybrid_round8_20261005'
OUT = PARENT/'focus_primal'


def read(path): return json.loads(Path(path).read_text())
def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def write(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n')


def register():
    OUT.mkdir(exist_ok=False)
    original = read(PARENT/'plan.json')
    inputs = OUT/'inputs'; inputs.mkdir()
    for file in (PARENT/'inputs').iterdir():
        shutil.copy2(file, inputs/file.name)
    # Case paths still name the same frozen original files; hash them as well.
    for source in [*ROOT.glob('models/**/*.py'), *ROOT.glob('configuration/*.py'), Path(__file__).resolve()]:
        target = OUT/'source_snapshot'/source.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(source, target)
    checkers = OUT/'probes/checkers'; checkers.mkdir(parents=True)
    for file in (PARENT/'probes/checkers').glob('*.py'):
        code = file.read_text()
        if file.name == 'validation_core.py':
            code = code.replace('ROOT = HERE.parents[4]', "ROOT = HERE.parents[1]/'source_snapshot'")
        (checkers/file.name).write_text(code)
    verifier = (ROOT/'tools/audit/verify_model5_round8.py').read_text()
    verifier = verifier.replace('ROOT = Path(__file__).resolve().parents[2]', 'ROOT = Path('+repr(str(ROOT))+')')
    verifier = verifier.replace("OUT = ROOT/'output/experiments/model5_hybrid_round8_20261005'", 'OUT = Path(__file__).resolve().parent')
    verifier = verifier.replace("['warm_selected_columns','warm_all_columns','native_seed50']",
                                "['warm_focus0','warm_focus1','native_seed50_focus1']")
    verifier = verifier.replace("payload = read(OUT/(name+'.json'))", "payload = read(OUT/(name+'.json'))\n    assert payload['job'] == next(job for job in plan['jobs'] if job['id'] == name)")
    (OUT/'verify.py').write_text(verifier)
    options = {**original['jobs'][-1]['options'], 'retain_all_pricing_columns': False,
               'primal_revisit_time_limit': 180, 'primal_mip_focus': 1}
    jobs = [dict(id='warm_focus0', type='warm', budget=180, solver_budget=178.5, mip_focus=0),
            dict(id='warm_focus1', type='warm', budget=180, solver_budget=178.5, mip_focus=1),
            dict(id='native_seed50_focus1', type='native', budget=1500, options=options)]
    files = [*inputs.iterdir(), *(OUT/'source_snapshot').rglob('*.py'), *checkers.glob('*.py'), OUT/'verify.py']
    external = dict(original['external_sha256'])
    external.update({str(p):sha(p) for p in (PARENT/'inputs').iterdir()})
    write(OUT/'plan.json', dict(registered_before_run=True, registered_at=datetime.now().astimezone().isoformat(),
        runner_sha256=sha(Path(__file__)), sha256={str(p.relative_to(OUT)):sha(p) for p in files},
        external_sha256=external, warm_floor=original['warm_floor'], jobs=jobs,
        hash_seed=0, python=sys.version, mode='continuous',
        hypothesis='A primal-only physical route pool with MIPFocus1 may find a lower audited upper than MIPFocus0 in the same180second pipeline budget.',
        warm_control='Same frozen original3294.704155 initial, selected reference columns, seed50, input, source,178.5second solver limit and180second pipeline cap. Only MIPFocus changes.',
        native_scope='Combined candidate: primal focus1, near-target revisit180, small finalization reserve, independent inventory replay. No external initial, routes or bounds. Prior night comparison is observational, not controlled.',
        negative_predecessors='Both first warm jobs exceeded preset180.5 cap; retained-all also had a worse upper. Keep results and failed validations. Native120/retained-all precursor is NOT_EXECUTED_SUPERSEDED_BEFORE_RUN.',
        workload='Owned heavy jobs and validation are sequential; external chat load is uncontrolled and measured.',
        budget_rule='Native1500 includes ALNS, disposal and final independent audit. Warm178.5 reserves1.5seconds for final audit under180+.5 cap; tolerance remains unchanged.',
        stopped_automation='No automation resumed or created; this is bounded authorized work in the current turn.'))
    write(OUT/'verification_plan.json', dict(registered_before_verification=True,
        validator_sha256=sha(OUT/'verify.py'), rules='Same full physical/domain checks and fixed0.5second budget tolerance; no threshold changes.'))
    write(OUT/'checkpoint.json', dict(status='REGISTERED', running=None, jobs=jobs))
    print('REGISTERED FOCUS FOLLOWUP', jobs, flush=True)


def frozen(plan):
    assert sha(Path(__file__)) == plan['runner_sha256']
    for name, digest in plan['sha256'].items(): assert sha(OUT/name) == digest, name
    for name, digest in plan['external_sha256'].items(): assert sha(name) == digest, name


def worker(name):
    plan = read(OUT/'plan.json'); frozen(plan)
    job = next(j for j in plan['jobs'] if j['id'] == name)
    assert not (OUT/(name+'.json')).exists()
    checkpoint = read(OUT/'checkpoint.json')
    checkpoint.update(status='RUNNING_'+name.upper(), running=dict(pid=os.getpid(), job=name,
        log=str(OUT/(name+'.log')), started_at=datetime.now().astimezone().isoformat(), budget=job['budget']))
    write(OUT/'checkpoint.json', checkpoint)
    sys.path.insert(0, str(OUT/'source_snapshot'))
    from configuration.loader import load_routing_case
    from models.routing.common.cycle import load_instance
    from models.routing.common.cycle.audit import audit_solution
    p = load_instance(*load_routing_case(OUT/'inputs/case.json', use_pool_config=False))
    assert len({o.customer for o in p.orders}) == 100 and len(p.satellites) == 5
    assert p.operating_policy.container_pool_size == 155 and len(p.operating_policy.batches) == 3
    begun, load = time.perf_counter(), os.getloadavg()
    if job['type'] == 'warm':
        from models.routing.solvers.exact.multibatch_route_pool import reoptimize_route_pool
        initial = read(OUT/'inputs/warm_result.json')
        ref = read(OUT/'inputs/warm_certificates.json')['reference']
        columns = {s:[c for r in ref['sites'] if r['satellite']==s for c in r['columns']] for s in p.satellites}
        result = reoptimize_route_pool(p, initial, time_limit=job['solver_budget'], seed=50,
            transfers=True, exchanges=True, recombinations=True, max_passes=2,
            additional_columns=columns, max_columns=64, extra_slots_per_vehicle=1,
            e1_backend='patterns', mip_focus=job['mip_focus'], verbose=True, diagnostics_dir=OUT/(name+'_models'))
        audit = audit_solution(p, result)
        payload = dict(job=job, initial=initial, result=result, independent_audit=audit,
            upper=audit['objective'], historical_verified_floor=plan['warm_floor'],
            combined_gap=(audit['objective']-plan['warm_floor'])/audit['objective'],
            scope='Warm controlled180second primal focus ablation; historical floor, not cold.')
    else:
        import models.routing.service as service
        from models.routing.variants.model_5.exact import solve_hybrid
        actual = service.solve_routing
        experiment = solve_hybrid(p, **job['options'], verbose=True, diagnostics_dir=OUT/(name+'_models'))
        assert actual is service.solve_routing
        audit = audit_solution(p, experiment['result'])
        payload = dict(job=job, experiment=experiment, independent_audit=audit,
            direct_public_entry=True, service_not_monkeypatched=True,
            scope='Native public cold1500second candidate; not a same-budget comparison with historical run.')
    wall = time.perf_counter()-begun
    assert audit['valid'] and audit['periodic_replay']['valid']
    payload.update(pipeline_wall_seconds=wall, within_registered_budget=wall <= job['budget']+.5,
                   load_start=load, load_end=os.getloadavg(), proof_verification='PENDING')
    frozen(plan); write(OUT/(name+'.json'), payload)
    checkpoint.update(status='NEEDS_VERIFICATION_'+name.upper(), running=None, latest_result=name)
    write(OUT/'checkpoint.json', checkpoint)
    out = payload.get('experiment', {})
    print('RESULT', dict(id=name, upper=payload.get('upper', out.get('upper_bound')),
        lower=payload.get('historical_verified_floor', out.get('bound')),
        gap=payload.get('combined_gap', out.get('gap')), seconds=wall, within_budget=payload['within_registered_budget']), flush=True)


if __name__ == '__main__':
    register() if sys.argv[1] == 'register' else worker(sys.argv[1])
