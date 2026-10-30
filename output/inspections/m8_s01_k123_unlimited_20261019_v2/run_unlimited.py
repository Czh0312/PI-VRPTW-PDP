"""Uncapped M8/S01 diagnostic using frozen production formulation and audit."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import time
import traceback

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
BASE = ROOT / 'output/inspections/m8_s01_k123_20261019_v1'
FROZEN = OUT / 'source_snapshot'
ZONE = timezone(timedelta(hours=8))


def now():
    return datetime.now(ZONE).isoformat(timespec='seconds')


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def save(path, value):
    payload = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(payload, encoding='utf-8')
    temporary.replace(path)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def freeze():
    baseline = read(BASE / 'manifest.json')
    sources, inputs = {}, {}
    for name, expected in baseline['source_sha256'].items():
        payload = (BASE / 'source_snapshot' / name).read_bytes()
        if digest(payload) != expected:
            raise RuntimeError(f'Baseline source hash mismatch: {name}')
        destination = FROZEN / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(payload)
        sources[name] = expected
    for original_name, expected in baseline['input_sha256'].items():
        source = Path(original_name)
        payload = source.read_bytes()
        if digest(payload) != expected:
            raise RuntimeError(f'Input changed since the 300-second run: {source.name}')
        relative = source.resolve().relative_to(ROOT)
        destination = FROZEN / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(payload)
        inputs[str(relative)] = expected
    config, fleet = baseline['config'], baseline['fleet']
    assert config['model'] == 8 and config['joint_batch_options']['batch_count_candidates'] == [1, 2, 3]
    config['exact_time'] = None
    config['solver'] = 'exact'
    save(OUT / 'resolved_case.json', config)
    save(OUT / 'resolved_fleet.json', fleet)
    save(OUT / 'manifest.json', dict(
        started_at=now(), pid=os.getpid(), parent_pid=os.getppid(),
        interpreter=sys.executable, python=sys.version, baseline=str(BASE),
        case='S01', model=8, batch_count_candidates=[1, 2, 3],
        time_limit=None, time_limit_description='Infinity; no outer timeout',
        threads=1, seed=42, formulation='expanded', initial_solution=False,
        source_sha256=sources, input_sha256=inputs,
        driver_sha256=digest(Path(__file__).read_bytes()),
        frozen_project=str(FROZEN), source_hash_mismatches=0, input_hash_mismatches=0))
    return config, fleet


def main():
    if (OUT / 'manifest.json').exists():
        raise RuntimeError('This output already contains a registered run; refusing a duplicate')
    save(OUT / 'progress.json', dict(state='PREPARING', pid=os.getpid(), updated_at=now()))
    config, fleet = freeze()
    # This tree contains the exact prior source bytes and the original input paths.
    sys.path.insert(0, str(FROZEN))
    os.chdir(FROZEN)
    from models.common.cycle import load_instance
    from models.common.cycle.audit import audit_solution
    from models.common.cycle.calendar import attach_calendar_decisions
    from models.common.cycle.joint import joint_empty_result
    from models.common.cycle.unified_milp import _identity
    from models.common.solver_config import MilpOptions, read_milp_info, accept_milp_audit
    from models.routing.model_8.exact.model import build_model
    from models.routing.model_8.exact.solution import extract_solution
    import gurobipy as gp

    problem = load_instance(config, fleet)
    begun = time.perf_counter()
    model = None
    try:
        save(OUT / 'progress.json', dict(state='BUILDING', pid=os.getpid(), updated_at=now()))
        model, context = build_model(problem, verbose=True, deadline=None, compact_e2=False)
        build_time = time.perf_counter() - begun
        model.Params.Threads = 1
        model.Params.Seed = 42
        model.Params.TimeLimit = gp.GRB.INFINITY
        # Native Gurobi file writers receive ASCII paths relative to the frozen cwd.
        model.Params.LogFile = '../gurobi.log'
        model.Params.SolFiles = '../incumbent'
        assert math.isinf(model.Params.TimeLimit) or model.Params.TimeLimit >= gp.GRB.INFINITY
        assert model.NumStart == 0 and model.Params.MIPGap == 0 and model.Params.MIPGapAbs == 0
        baseline_info = read(BASE / 'result.json')['info']
        assert model.NumVars == baseline_info['variables']
        assert model.NumConstrs + model.NumGenConstrs == baseline_info['constraints']
        model.write('../model.mps')
        parameters = MilpOptions().metadata(None, 42)
        parameters.update(time_limit_description='Infinity', outer_timeout=None,
                          batch_count_formulation='one_hot_disjunctive_MILP', k_search=None,
                          retained_model_bounds=False, custom_callback=False,
                          formulation_options=dict(compact_e2=False, strengthening=False),
                          native_incumbent_files=True)
        save(OUT / 'parameters.json', dict(
            **parameters, actual_time_limit=str(model.Params.TimeLimit),
            gurobi_version=list(gp.gurobi.version()), variables=model.NumVars,
            constraints=model.NumConstrs + model.NumGenConstrs,
            binary_variables=model.NumBinVars, build_time=build_time))
        save(OUT / 'progress.json', dict(
            state='OPTIMIZING', pid=os.getpid(), updated_at=now(),
            time_limit=None, actual_time_limit=str(model.Params.TimeLimit),
            batch_count_candidates=list(problem.batch_count_candidates),
            variables=model.NumVars, constraints=model.NumConstrs + model.NumGenConstrs,
            build_time=build_time, live_log=str(OUT / 'gurobi.log')))
        print('UNCAPPED_OPTIMIZATION_STARTED', now(), flush=True)
        model.optimize()
        scope = 'model_8_restricted_K_1_2_3_joint_cost_integer_time_MILP'
        info = read_milp_info(model, proof_scope=scope, build_time=build_time)
        result = joint_empty_result(problem, 'exact')
        result['cycle_batch_count'] = None
        result['info'] = info
        if model.SolCount:
            selected = [k for k, value in context['selectors'].items() if value.X > 0.5]
            if len(selected) != 1:
                raise RuntimeError('Expected exactly one selected batch count')
            count = selected[0]
            block = context['blocks'][count]
            result = extract_solution(block['problem'], model, block['context'], info)
            if result.get('audit', {}).get('valid'):
                boundaries = [batch['start'] for batch in result['batches']]
                boundaries.append(block['problem'].operating_policy.cycle_end)
                attach_calendar_decisions(problem, result, count, boundaries)
                _identity(problem, result)
                result['audit'] = audit_solution(problem, result)
            accept_milp_audit(result, float(model.ObjVal))
            model.write('../final.sol')
        _identity(problem, result)
        result['solver_module'] = 'uncapped_diagnostic_using_frozen_model_8_exact_model'
        result['info'].update(
            proof_scope=scope, runtime=time.perf_counter()-begun,
            solver_config=parameters, exact_strategy='gurobi_unified_milp',
            exact_strategy_requested='milp', batch_count_candidates=[1, 2, 3],
            complete_count_domain=True, model_size_scope='restricted_K_1_2_3',
            mip_runtime_scope='single_unified_model')
        save(OUT / 'result.json', result)
        save(OUT / 'progress.json', dict(
            state='FINISHED', pid=os.getpid(), updated_at=now(),
            status=result['info']['status'], objective=result['info'].get('objective'),
            best_bound=result['info'].get('best_bound'), mip_gap=result['info'].get('mip_gap'),
            proven_optimal=result['info']['proven_optimal'],
            audit_valid=result.get('audit', {}).get('valid', False),
            runtime=result['info']['runtime']))
        print('UNCAPPED_OPTIMIZATION_FINISHED', json.dumps(read(OUT / 'progress.json')), flush=True)
    finally:
        if model is not None:
            model.dispose()


if __name__ == '__main__':
    try:
        main()
    except BaseException as exc:
        save(OUT / 'error.json', dict(at=now(), error=repr(exc), traceback=traceback.format_exc()))
        save(OUT / 'progress.json', dict(state='FAILED', pid=os.getpid(), updated_at=now(), error=repr(exc)))
        raise
