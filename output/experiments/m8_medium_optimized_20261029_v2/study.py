"""One 20-customer M8 timing probe, using the validated small-case method.

Only the research size guards are extended. Original inputs and production
entry points are unchanged. An incomplete trip library is never a bound.
"""
from __future__ import annotations

import argparse
import gzip
import pickle
from copy import deepcopy
from datetime import datetime, timezone
from functools import partial
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import sys
import time
import traceback
import zipfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path[:0] = [str(HERE), str(ROOT)]
CASE = ROOT / 'data/formal_benchmark27_uniform_7_20_50_v1/model_8/medium/M01/case.json'
OLD = ROOT / 'output/experiments/staged_comparison_27_seed42_20261024_v1'
WITNESS = OLD / 'runs/B_M01_m8_cost_alns_600s_s42/attempt_001/result.json'
SYNC = ROOT / 'output/experiments/m8_conflict_acceleration_20261028_v1'
TOL = 1e-6


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def save(path, value):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.name == 'trip_library.json':
        with gzip.open(target.with_suffix('.pkl.gz'), 'wb', compresslevel=1) as stream:
            pickle.dump(value, stream, protocol=5)
        return
    pending = target.with_suffix(target.suffix + '.tmp')
    pending.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    pending.replace(target)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load():
    from models.common.cycle import load_instance
    from tools.configuration.loader import load_routing_case
    return load_instance(*load_routing_case(CASE, use_pool_config=False))


def module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def finite(value):
    return float(value) if math.isfinite(value) and abs(value) < 1e90 else None


def master_lower_bound(model, fallback):
    import gurobipy as gp
    if model.Status in (gp.GRB.INFEASIBLE, gp.GRB.INF_OR_UNBD):
        raise RuntimeError('Master contradicts the original audited feasible witness')
    bound = finite(model.ObjBound)
    return max(fallback, bound) if bound is not None else fallback


def master(p, library):
    from shared_medium import build_relaxation
    from master_sync import augment
    phase = module(HERE / 'phase_bound.py', 'medium_phase')
    m, ctx = build_relaxation(p, library)
    phase.augment(p, m, ctx)
    augment(p, m, ctx, library)
    return m, ctx, phase


def native(p, certificate, deadline):
    from models.common.cycle.unified_milp import build_unified_model
    from models.routing.model_8.exact.formulation import build_model
    from models.routing.model_8.exact.solution import extract_solution
    from models.common.cycle.calendar import attach_calendar_decisions
    from models.common.cycle.audit import audit_solution
    from models.common.solver_config import read_milp_info, accept_milp_audit
    begun = time.perf_counter()

    def build(child, **kwargs):
        m, ctx = build_model(child, strengthening='physical_time', **kwargs)
        ctx['pool'].LB = max(ctx['pool'].LB, certificate['N_min'])
        ctx['pool'].UB = min(ctx['pool'].UB, certificate['N_max'])
        return m, ctx

    def exclude(child):
        k = len(child.operating_policy.batches)
        if k not in certificate['retained_K']:
            return dict(reason='FRESH_CERTIFIED_COMPETITIVE_DOMAIN', K=k)
        return None

    m, ctx = build_unified_model(p, build_fixed=build, compact_e2=True, deadline=deadline, count_certificate=exclude)
    try:
        m.addConstr(m.getObjective() >= certificate['global_lower_bound'] - 1e-7)
        m.addConstr(m.getObjective() <= certificate['audited_upper_bound'] + 1e-7)
        m.Params.Threads = 1
        m.Params.Seed = 42
        m.Params.TimeLimit = max(.01, deadline - time.perf_counter())
        m.Params.LogFile = os.path.relpath(HERE / 'native.log', ROOT)
        m.optimize()
        info = read_milp_info(m, proof_scope='complete_original_M8_certified_competitive_domain', build_time=time.perf_counter()-begun-m.Runtime)
        result = None
        if m.SolCount:
            k = next(k for k, v in ctx['selectors'].items() if v.X > .5)
            block = ctx['blocks'][k]
            result = extract_solution(block['problem'], m, block['context'], info)
            boundaries = [b['start'] for b in result['batches']] + [p.operating_policy.cycle_end]
            attach_calendar_decisions(p, result, k, boundaries)
            result['audit'] = audit_solution(p, result)
            accept_milp_audit(result, m.ObjVal)
            assert result['audit']['valid'] and result['audit']['periodic_replay']['valid']
        save(HERE / 'native_summary.json', info)
        return info, result
    finally:
        m.dispose()


def run():
    import gurobipy as gp
    from shared_medium import enumerate_trip_library
    from models.common.cycle.audit import audit_solution
    from models.routing.model_8.exact.parameters import necessary_time_bounds
    manifest = read(HERE / 'manifest.json')
    if (HERE / 'summary.json').exists() or (HERE / 'started.json').exists():
        raise FileExistsError('This timing attempt is immutable')
    assert all(sha(f) == h for f, h in manifest['hashes'].items())
    begun = time.perf_counter()
    deadline = begun + manifest['budget_seconds']
    save(HERE / 'started.json', dict(at=datetime.now(timezone.utc).isoformat()))
    from experiments.runs.model8_trip_library_native import build_native
    compile_started = time.perf_counter()
    build_native(manifest['compiler'], HERE / 'TripOracle.exe')
    compile_seconds = time.perf_counter()-compile_started
    p = load()
    best = read(HERE / 'initial_audited_result.json')
    audit = audit_solution(p, best)
    assert audit['valid'] and audit['periodic_replay']['valid']
    upper = audit['objective']
    physical = necessary_time_bounds(p)
    rates = p.cost_profile.rates
    # A conservative input-only bound remains valid even if enumeration stops.
    lower = (max(p.pool_min, physical['pool_lower_bound']) * rates['container_pool_cost_per_period']
             + sum(p.cost_profile.constant_cost(p.total).values()))
    save(HERE / 'physical_bound.json', dict(bound=lower, physical=physical, transport_costs_relaxed=True))
    row = dict(case='M01', customers=len(p.orders), original_K=list(p.batch_count_candidates),
        seed=42, threads=1, budget_seconds=manifest['budget_seconds'], complete_route_library=False, compile_seconds=compile_seconds,
        historical_feasible_plan_used=True, historical_lower_bound_used=False,
        cold_start_end_to_end=False, production_solver_modified=False)
    stage = 'trip_enumeration'
    start_enum = time.perf_counter()

    def progress(detail):
        save(HERE / 'progress.json', dict(stage=stage, elapsed_seconds=time.perf_counter()-begun, **detail))

    progress(dict(prefixes=0, columns=0))
    print(f'START M01: 20 customers, K=1..20, budget=600s, U={upper:.9f}', flush=True)
    try:
        library, prefixes = enumerate_trip_library(p, deadline=deadline, on_progress=progress)
        row.update(enumeration_seconds=time.perf_counter()-start_enum, complete_route_library=True,
                   route_columns=len(library), enumerated_prefixes=prefixes)
        export_started = time.perf_counter()
        save(HERE / 'trip_library.json', library)
        row['library_export_seconds'] = time.perf_counter()-export_started
        print(json.dumps(dict(stage='enumeration_complete', **row), ensure_ascii=False), flush=True)
        if time.perf_counter() >= deadline:
            raise TimeoutError('Budget exhausted by complete enumeration')
        stage = 'input_only_master'
        progress({})
        mark = time.perf_counter()
        m, ctx, phase = master(p, library)
        row['master_build_seconds'] = time.perf_counter()-mark
        domain = None
        try:
            m.Params.Seed = 42
            m.Params.Threads = 1
            m.Params.TimeLimit = max(.01, deadline-time.perf_counter())
            m.Params.LogFile = os.path.relpath(HERE / 'master.log', ROOT)
            m.optimize()
            bound = finite(m.ObjBound)
            lower = master_lower_bound(m, lower)
            assert lower <= upper + TOL
            raw = dict(native_status=m.Status, lower_bound=bound, effective_global_lower_bound=lower, finite_native_bound_available=bound is not None, native_runtime=m.Runtime,
                relaxation_objective=m.ObjVal if m.SolCount else None, variables=m.NumVars,
                constraints=m.NumConstrs+m.NumGenConstrs, original_K=list(p.batch_count_candidates),
                prior_U_or_L_supplied=False, K_or_N_fixed=False, complete_route_library=True)
            save(HERE / 'input_only_bound.json', raw)
            row.update(input_only_master_seconds=m.Runtime, master_variables=m.NumVars,
                       master_constraints=m.NumConstrs+m.NumGenConstrs)
            print(json.dumps(dict(stage='input_only_bound', lower=lower, upper=upper,
                                  elapsed_seconds=time.perf_counter()-begun)), flush=True)
            if upper-lower > TOL and time.perf_counter() < deadline:
                stage = 'certified_domain_endpoints'
                optimistic = m.getObjective()
                m.addConstr(optimistic <= upper+1e-7)
                count = gp.quicksum(k*v for k, v in ctx['selectors'].items())
                domain = dict(original_K=list(p.batch_count_candidates), global_lower_bound=lower,
                    audited_upper_bound=upper, endpoints={}, K_min=min(p.batch_count_candidates),
                    K_max=max(p.batch_count_candidates), N_min=p.pool_min, N_max=p.pool_max)
                for name, expression, sign in [('K_min', count, 1), ('K_max', count, -1),
                                               ('N_min', ctx['pool'], 1), ('N_max', ctx['pool'], -1)]:
                    if time.perf_counter() >= deadline:
                        break
                    progress(dict(endpoint=name))
                    m.setObjective(sign*expression)
                    m.Params.TimeLimit = max(.01, min(30., deadline-time.perf_counter()))
                    m.optimize()
                    if m.Status == gp.GRB.OPTIMAL:
                        domain[name] = round(sign*m.ObjVal)
                    domain['endpoints'][name] = dict(status=m.Status, bound=finite(m.ObjBound),
                        value=m.ObjVal if m.SolCount else None, seconds=m.Runtime,
                        used_for_reduction=m.Status == gp.GRB.OPTIMAL)
                domain['retained_K'] = [k for k in p.batch_count_candidates if domain['K_min'] <= k <= domain['K_max']]
                save(HERE / 'reduction_certificate.json', domain)
        finally:
            m.dispose()
        if upper-lower > TOL and time.perf_counter() < deadline:
            if domain is None:
                raise RuntimeError('Missing competitive-domain evidence')
            stage = 'exact_refinement'
            progress(dict(retained_K=domain['retained_K'], pool=[domain['N_min'], domain['N_max']]))
            unchanged_clocks = all(p.for_count(k).operating_policy.time_tick_seconds == p.operating_policy.time_tick_seconds
                                   for k in domain['retained_K'])
            if unchanged_clocks:
                from decomposition_medium import solve
                best, info = solve(p, best, domain['retained_K'], [domain['N_min'], domain['N_max']], lower, library,
                    phase, max(.01, deadline-time.perf_counter()), 42, on_event=progress)
                lower = max(lower, info['global_lower_bound'])
                save(HERE / 'algorithm_summary.json', info)
                row.update(patterns=info['patterns'], cuts=info['cuts'], refinement_seconds=info['elapsed_seconds'])
            else:
                info, candidate = native(p, domain, deadline)
                if candidate is not None and candidate['audit']['objective'] < upper:
                    best = candidate
                if info['best_bound'] is not None:
                    lower = max(lower, info['best_bound'])
        row['status'] = 'OPTIMAL_CERTIFICATE' if upper-lower <= TOL else 'TIME_LIMIT'
    except TimeoutError as exc:
        row.update(status='TIME_LIMIT_ENUMERATION' if stage == 'trip_enumeration' else 'TIME_LIMIT',
                   stopped_stage=stage, stop_reason=str(exc))
        if stage == 'trip_enumeration':
            row['enumeration_seconds'] = time.perf_counter()-start_enum
            row['last_enumeration_progress'] = read(HERE / 'progress.json')
    except Exception as exc:
        row.update(status='ERROR', stopped_stage=stage, error=str(exc))
        save(HERE / 'error.json', dict(error=str(exc), traceback=traceback.format_exc()))
    final_audit = audit_solution(p, best)
    assert final_audit['valid'] and final_audit['periodic_replay']['valid']
    upper = final_audit['objective']
    assert lower <= upper+TOL
    row.update(audited_upper_bound=upper, global_lower_bound=lower, absolute_gap=max(0., upper-lower),
        gap_over_upper=max(0., upper-lower)/max(1e-9, abs(upper)), proven_optimal=upper-lower<=TOL,
        selected_K=best['cycle_batch_count'], selected_N=best['container_pool_size'],
        business_audit_valid=True, periodic_replay_valid=True,
        lower_bound_source='fresh_full_library_master' if (HERE/'input_only_bound.json').exists() else 'necessary_pool_bound_only',
        total_wall_seconds=time.perf_counter()-begun,
        original_sources_and_inputs_unchanged=all(sha(f)==h for f,h in manifest['hashes'].items()))
    save(HERE / 'best_audited_result.json', best)
    save(HERE / 'audit.json', final_audit)
    save(HERE / 'summary.json', row)
    save(HERE / 'progress.json', dict(stage='complete', **row))
    print(json.dumps(row, ensure_ascii=False), flush=True)
    if row['status'] == 'ERROR':
        raise RuntimeError(row['error'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['run'])
    args = parser.parse_args()
    run()
