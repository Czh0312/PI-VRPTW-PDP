"""Registered bounded M5 guard diagnostic, warm column ablation and cold run."""
import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT/'output/experiments/model5_hybrid_round8_20261005'
NIGHT = ROOT/'output/experiments/model5_alns_overnight_20261001/probes'


def read(path):
    return json.loads(Path(path).read_text())


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def register():
    assert not (OUT/'plan.json').exists()
    original = NIGHT/'public1500_01'
    seal = read(original/'final_verification.json')
    assert seal['status'] == read(original/'validation.json')['status'] == 'VERIFIED'
    for name, digest in seal['artifact_sha256'].items():
        assert sha(original/name) == digest, name
    old = read(original/'plan.json')
    cfg = read(old['case'])
    inputs = OUT/'inputs'
    inputs.mkdir()
    for field in ('data', 'orders', 'routing_config', 'distance_matrix', 'duration_matrix', 'dataset_metadata'):
        if cfg.get(field):
            origin = Path(cfg[field]); target = inputs/origin.name
            shutil.copy2(origin, target); assert sha(origin) == sha(target)
            cfg[field] = str(target)
    write(inputs/'case.json', cfg)
    payload = read(original/'seed50_public1500.json')
    write(inputs/'warm_result.json', payload['experiment']['result'])
    write(inputs/'warm_certificates.json', dict(
        reference=payload['experiment']['adaptive_certificate']['reference_certificate'],
        conditional=payload['experiment']['conditional_certificate']))
    for source in [*ROOT.glob('models/**/*.py'), *ROOT.glob('tools/configuration/*.py'), ROOT/'tools/__init__.py',
                   Path(__file__).resolve()]:
        target = OUT/'source_snapshot'/source.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(source, target)
    checkers = OUT/'probes/checkers'
    checkers.mkdir(parents=True)
    for source in (NIGHT/'public1500_02').glob('validation_*.py'):
        target = checkers/source.name
        content = source.read_text()
        content = content.replace('from configuration', 'from tools.configuration')
        if source.name == 'validation_core.py':
            content = content.replace("str(RUNS/'candidate_source')", "str(HERE.parents[1]/'source_snapshot')")
        target.write_text(content)
    options = {**old['jobs'][0]['options'], 'primal_revisit_time_limit': 120,
               'retain_all_pricing_columns': True}
    jobs = [dict(id='guard_counterexample', type='guard', budget=10),
            dict(id='warm_selected_columns', type='warm', budget=180, retain_all=False),
            dict(id='warm_all_columns', type='warm', budget=180, retain_all=True),
            dict(id='native_seed50', type='native', options=options, budget=1500)]
    model = NIGHT/'public1500_03/seed53_public1500_models/conditional/master_0.mps.gz'
    frozen = [*inputs.iterdir(), *(OUT/'source_snapshot').rglob('*.py'), *checkers.glob('*.py')]
    write(OUT/'plan.json', dict(registered_before_run=True, mode='continuous',
        registered_at=datetime.now().astimezone().isoformat(), jobs=jobs,
        sha256={str(p.relative_to(OUT)): sha(p) for p in frozen},
        runner_sha256=sha(Path(__file__)), python=sys.version, hash_seed=0,
        external_sha256={str(model):sha(model), str(original/'final_verification.json'):sha(original/'final_verification.json'),
                         str(original/'seed50_public1500.json'):sha(original/'seed50_public1500.json')},
        guard_model=str(model), guard_claim=3059.8355903420966, guard_fallback=3045.7926940795637,
        warm_floor=payload['experiment']['bound'], extra_slots_per_vehicle=1,
        hypotheses=['Independent cut-free replay rejects the known disputed14.04-unit inventory claim.',
                    'Retaining previously discarded subset and conditional routes improves the same frozen warm upper in180seconds.',
                    'The combined bounded candidate may improve a true seed50 cold run; one run does not establish stability.'],
        interpretation='Warm pair uses historical audited initial and certified floor; never described as cold performance. Native run has no external initial, routes or bounds. Comparing its prior night result is observational, not a matched-time controlled pair.',
        validation_rule='Independent full two-cycle audits, physical-pool MPS projections, complete-domain certificate checks before any best-record promotion.',
        load_rule='Sequential owned jobs, record external activity and load; no claim of controlled machine load.',
        stopped_automation='m5-alns remains paused; no new automation or cross-day schedule.'))
    print('REGISTERED', jobs, flush=True)


def verify_frozen(plan):
    assert sha(Path(__file__)) == plan['runner_sha256']
    for name, digest in plan['sha256'].items():
        assert sha(OUT/name) == digest, name
    for name, digest in plan['external_sha256'].items():
        assert sha(name) == digest, name


def worker(name):
    plan = read(OUT/'plan.json'); verify_frozen(plan)
    job = next(j for j in plan['jobs'] if j['id'] == name)
    destination = OUT/(name+'.json')
    assert not destination.exists(), 'Never overwrite or silently retry a recorded run'
    sys.path.insert(0, str(OUT/'source_snapshot'))
    checkpoint = read(OUT/'checkpoint.json')
    checkpoint.update(status='RUNNING_'+name.upper(), running=dict(
        pid=os.getpid(), job=name, log=str(OUT/(name+'.log')), budget=job['budget'],
        started_at=datetime.now().astimezone().isoformat()))
    write(OUT/'checkpoint.json', checkpoint)
    load = os.getloadavg(); started = time.perf_counter()
    if job['type'] == 'guard':
        import gurobipy as gp
        from models.routing.solvers.exact.multibatch_inventory_guard import check_inventory_bound
        model = gp.read(plan['guard_model'])
        try:
            result = check_inventory_bound(model, plan['guard_claim'],
                fallback_bound=plan['guard_fallback'], deadline=time.perf_counter()+job['budget'],
                diagnostics_path=OUT/'guard_replay.mps.gz')
            assert result['status'] == 'PRIMARY_BOUND_REJECTED', result
            assert result['witness']['valid'] and result['accepted_raw_bound'] <= 3045.7931131
        finally:
            model.dispose()
        payload = dict(job=job, result=result, scope='Known historical master diagnostic; not a new full M5 solve.')
    else:
        if (OUT/'source_snapshot/configuration/loader.py').is_file():
            from configuration.loader import load_routing_case
        else:
            from tools.configuration.loader import load_routing_case
        from models.routing.common.cycle import load_instance
        from models.routing.common.cycle.audit import audit_solution
        problem = load_instance(*load_routing_case(OUT/'inputs/case.json', use_pool_config=False))
        assert len({o.customer for o in problem.orders}) == 100
        assert len(problem.satellites) == 5 and len(problem.operating_policy.batches) == 3
        assert problem.operating_policy.container_pool_size == 155
        begun = time.perf_counter()
        if job['type'] == 'warm':
            from models.routing.solvers.exact.multibatch_route_pool import reoptimize_route_pool
            from models.routing.solvers.exact.multibatch_hybrid_primal import collect_pricing_columns
            initial = read(OUT/'inputs/warm_result.json')
            certificates = read(OUT/'inputs/warm_certificates.json'); ref = certificates['reference']
            columns = collect_pricing_columns(problem, ref, certificates['conditional']) if job['retain_all'] else {
                s:[c for r in ref['sites'] if r['satellite']==s for c in r['columns']] for s in problem.satellites}
            result = reoptimize_route_pool(problem, initial, time_limit=job['budget'], seed=50,
                transfers=True, exchanges=True, recombinations=True, max_passes=2,
                additional_columns=columns, max_columns=64, extra_slots_per_vehicle=1,
                e1_backend='patterns', verbose=True, diagnostics_dir=OUT/(name+'_models'))
            audit = audit_solution(problem, result)
            payload = dict(job=job, initial=initial, result=result, independent_audit=audit,
                upper=audit['objective'], historical_verified_floor=plan['warm_floor'],
                combined_gap=(audit['objective']-plan['warm_floor'])/audit['objective'],
                proposed_columns={s:len(cs) for s,cs in columns.items()},
                scope='Warm180-second column-retention ablation on identical frozen initial; not cold.')
        else:
            import models.routing.service as service
            from models.routing.variants.model_5.exact import solve_hybrid
            actual = service.solve_routing
            experiment = solve_hybrid(problem, **job['options'], verbose=True,
                diagnostics_dir=OUT/(name+'_models'))
            assert service.solve_routing is actual
            audit = audit_solution(problem, experiment['result'])
            payload = dict(job=job, experiment=experiment, independent_audit=audit,
                direct_public_entry=True, service_not_monkeypatched=True,
                scope='Native public fresh ALNS/hybrid; all initialization, cleanup and final audit charged.')
        assert audit['valid'] and audit['periodic_replay']['valid']
        payload['pipeline_wall_seconds'] = time.perf_counter()-begun
        payload['within_registered_budget'] = payload['pipeline_wall_seconds'] <= job['budget']+.5
    payload.update(outer_seconds=time.perf_counter()-started, load_start=load, load_end=os.getloadavg(),
                   proof_verification='PENDING')
    verify_frozen(plan)
    write(destination, payload)
    checkpoint.update(status='NEEDS_VERIFICATION_'+name.upper(), running=None,
                      latest_result=str(destination), updated_at=datetime.now().astimezone().isoformat())
    write(OUT/'checkpoint.json', checkpoint)
    print('RESULT', {k:v for k,v in payload.items() if k not in ('experiment','initial','result')}, flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['register','guard_counterexample','warm_selected_columns','warm_all_columns','native_seed50'])
    args = parser.parse_args()
    register() if args.action == 'register' else worker(args.action)
