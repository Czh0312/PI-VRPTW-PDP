"""Repeat all nine current small M8 cases with fresh bounds and trip libraries.

Only an existing, independently audited feasible plan is reused. No prior lower
bound, domain certificate, route library, solution hint or start is supplied.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import sys
import time
import traceback

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
SYNC = ROOT / 'output/experiments/m8_conflict_acceleration_20261028_v1'
STABILITY = ROOT / 'output/experiments/m8_stability_20261028_v1'
sys.path[:0] = [str(ROOT), str(SYNC), str(STABILITY)]

from experiments.runs.probe_model8_physical_bounds import source_hashes
from experiments.runs.research_model8_shared_relaxation import build_relaxation, enumerate_trip_library
from models.common.cycle import load_instance
from models.common.cycle.audit import audit_solution
from tools.configuration.loader import load_routing_case
from master_sync import augment as add_trip_sync
import decomposition_sync

CASES = [f'S{i:02}' for i in range(1, 10)]
DATASET = ROOT / 'data/formal_benchmark27_uniform_7_20_50_v1'
PHASE_PATH = ROOT / 'output/experiments/m8_s04_reduction_validation_20261026_v1/phase_bound.py'
HELPER_PATH = ROOT / 'output/experiments/m8_remaining_small_optimality_20261028_v1/run_case.py'
TOL = 1e-6


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)
    for attempt in range(10):
        try:
            path.write_text(payload, encoding='utf-8')
            return
        except PermissionError:
            if attempt == 9:
                raise
            time.sleep(min(.02 * 2 ** attempt, .5))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def load(case):
    return load_instance(*load_routing_case(DATASET / f'model_8/small/{case}/case.json', use_pool_config=False))


def candidate_paths(case):
    base = ROOT / 'output/experiments'
    return [
        base / f'small7_m8_alns300_20261024_v1/runs/{case}_m8_alns_300s_s42/attempt_001/result.json',
        base / f'm8_small7_exact_alns_gap_20261024_v1/runs/exact/{case}/physical_time_result.json',
        base / f'm8_{case.lower()}_cold300_20261024_v1/run_frozen/physical_time_result.json',
        base / f'm8_remaining_small_optimality_20261028_v1/{case}/final_best_result.json',
        base / f'm8_remaining_small_optimality_20261028_v1/{case}/best_audited_result.json',
        base / f'm8_long_budget_20261028_v1/{case}/best_audited_result.json',
        base / f'm8_candidate_long_budget_20261028_v1/{case}/best_audited_result.json',
        SYNC / f'certified_{case}.json',
    ] + ([base / 'm8_stock_interval_probe_20261024/full_domain_certificate/audited_witness_with_certificate.json'] if case == 'S01' else []) + ([base / 'm8_s04_reduction_validation_20261026_v1/enhanced/result.json'] if case == 'S04' else [])


def prepare():
    path = HERE / 'plan.json'
    if path.exists():
        raise FileExistsError(path)
    registry = read(DATASET / 'manifest.json')
    members = [r for r in registry['cases'] if r['model'] == 8 and r['size'] == 'small']
    assert sorted(r['base_case_id'] for r in members) == CASES
    assert all(r['customer_count'] == 7 for r in members)
    inputs = {}
    entries = []
    for case in CASES:
        p = load(case)
        audits = []
        for candidate in candidate_paths(case):
            if candidate.exists():
                result = read(candidate)
                audit = audit_solution(p, result)
                audits.append(dict(path=str(candidate), valid=audit['valid'] and audit['periodic_replay']['valid'],
                                   cost=audit['objective']))
        valid = [r for r in audits if r['valid']]
        if not valid:
            raise RuntimeError(f'No audited feasible starting plan for {case}: {audits}')
        selected = min(valid, key=lambda r: r['cost'])
        case_path = DATASET / f'model_8/small/{case}/case.json'
        cfg, _ = load_routing_case(case_path, use_pool_config=False)
        files = [case_path, Path(selected['path'])]
        for key in ('data', 'routing_config', 'economic_config', 'orders', 'operating_policy',
                    'parameter_contract', 'batch_count_certificate', 'dataset_metadata'):
            if isinstance(cfg.get(key), str):
                files.append((ROOT / cfg[key]).resolve())
        inputs.update({str(f): sha(f) for f in files})
        entries.append(dict(case=case, witness_path=selected['path'], initial_U=selected['cost'],
                            original_K=list(p.batch_count_candidates), witness_candidates=audits))
    sources = source_hashes()
    files = [Path(__file__), SYNC / 'master_sync.py', SYNC / 'decomposition_sync.py',
             STABILITY / 'route_bounds.py', ROOT / 'experiments/runs/research_model8_shared_relaxation.py',
             PHASE_PATH, HELPER_PATH, DATASET / 'manifest.json']
    plan = dict(registered_at=datetime.now(timezone.utc).isoformat(), cases=entries, seeds=[42],
                threads=1, concurrent_solver_processes=1, budget_seconds_per_case=300,
                historical_feasible_plan_used=True, historical_lower_bound_used=False,
                historical_domain_or_route_library_used=False, mip_start_supplied=False,
                numerical_absolute_tolerance=TOL, production_entry_changed=False,
                timing_scope='Load, audit, fresh full trip enumeration, fresh all-K bound, fresh certified '
                             'domain endpoints if needed, exact refinement and final audit. Excludes '
                             'historical witness search, launcher preflight and study preparation.',
                input_hashes=inputs, source_hashes=sources,
                driver_hashes={str(f): sha(f) for f in files})
    save(path, plan)
    print(json.dumps(dict(cases=[(r['case'], r['initial_U']) for r in entries], seeds=[42]), ensure_ascii=False), flush=True)


def verify(plan):
    assert source_hashes() == plan['source_hashes']
    for key in ('input_hashes', 'driver_hashes'):
        assert all(sha(path) == digest for path, digest in plan[key].items()), key


def finite(value):
    return float(value) if math.isfinite(value) and abs(value) < 1e90 else None


def run_case(entry, seed, budget):
    import gurobipy as gp
    begun = time.perf_counter()
    case = entry['case']
    out = HERE / 'runs' / case / f's{seed}'
    out.mkdir(parents=True, exist_ok=False)
    print(f'START {case} seed={seed}', flush=True)
    try:
        p = load(case)
        witness = read(entry['witness_path'])
        audit = audit_solution(p, witness)
        assert audit['valid'] and audit['periodic_replay']['valid']
        upper = audit['objective']
        assert abs(upper - entry['initial_U']) <= TOL
        best = witness
        mark = time.perf_counter()
        library, prefixes = enumerate_trip_library(p)
        enumeration_seconds = time.perf_counter() - mark
        save(out / 'trip_library.json', library)
        phase = module(PHASE_PATH, f'phase_{case}_{seed}')
        mark = time.perf_counter()
        model, ctx = build_relaxation(p, library)
        phase.augment(p, model, ctx)
        add_trip_sync(p, model, ctx, library)
        model.Params.Seed = seed
        model.Params.Threads = 1
        build_seconds = time.perf_counter() - mark
        try:
            model.Params.TimeLimit = max(.01, min(60., budget - (time.perf_counter() - begun)))
            model.optimize()
            lower = finite(model.ObjBound)
            if lower is None or model.Status in (gp.GRB.INFEASIBLE, gp.GRB.INF_OR_UNBD):
                raise RuntimeError(f'Unexpected all-K master status {model.Status}, bound={lower}')
            assert lower <= upper + TOL
            raw = dict(case=case, native_status=model.Status, lower_bound=lower,
                       relaxation_objective=model.ObjVal if model.SolCount else None,
                       native_runtime=model.Runtime, work=model.Work, nodes=model.NodeCount,
                       full_route_columns=len(library), enumerated_prefixes=prefixes,
                       enumeration_seconds=enumeration_seconds, build_seconds=build_seconds,
                       prior_U_or_L_supplied=False, K_or_N_fixed=False,
                       original_K=list(p.batch_count_candidates), seed=seed)
            save(out / 'input_only_bound.json', raw)
            print(f'{case} fresh L={lower:.9f} U={upper:.9f} elapsed={time.perf_counter()-begun:.2f}s', flush=True)
            info = dict(status='INPUT_ONLY_BOUND_CERTIFICATE' if upper-lower <= TOL else 'TIME_LIMIT',
                        patterns=0, iterations=0, cuts=0, events=[], elapsed_seconds=0.,
                        master_seconds=0., follower_seconds=0., mip_work=0.)
            domain = None
            if upper - lower > TOL and time.perf_counter() - begun < budget:
                domain_start = time.perf_counter()
                optimistic = model.getObjective()
                model.addConstr(optimistic <= upper + 1e-7, name='fresh_audited_cost_cap')
                count = gp.quicksum(k*v for k, v in ctx['selectors'].items())
                domain = dict(original_K=list(p.batch_count_candidates), global_lower_bound=lower,
                              audited_upper_bound=upper, route_columns=len(library), endpoints={})
                for name, expression, sign in [('K_min', count, 1), ('K_max', count, -1),
                                               ('N_min', ctx['pool'], 1), ('N_max', ctx['pool'], -1)]:
                    model.setObjective(sign * expression)
                    model.Params.TimeLimit = max(.01, min(30., budget - (time.perf_counter() - begun)))
                    model.optimize()
                    if model.Status != gp.GRB.OPTIMAL:
                        raise RuntimeError(f'Uncertified domain endpoint {name}: {model.Status}')
                    domain[name] = round(sign * model.ObjVal)
                    domain['endpoints'][name] = dict(native_status=model.Status, objective=model.ObjVal,
                                                     bound=model.ObjBound, seconds=model.Runtime)
                domain['retained_K'] = [k for k in p.batch_count_candidates if domain['K_min'] <= k <= domain['K_max']]
                domain['wall_seconds'] = time.perf_counter() - domain_start
                domain['proof'] = 'Extrema of the fresh full-library strengthened all-K relaxation capped only '
                domain['proof'] += 'by an independently audited feasible cost retain every original competitive solution.'
                save(out / 'reduction_certificate.json', domain)
        finally:
            model.dispose()
        if upper - lower > TOL and time.perf_counter() - begun < budget:
            remaining = budget - (time.perf_counter() - begun)
            unchanged_clocks = domain is not None and all(
                (p.for_count(k).operating_policy.time_tick_seconds, p.for_count(k).operating_policy.time_tick_subdivision)
                == (p.operating_policy.time_tick_seconds, p.operating_policy.time_tick_subdivision)
                for k in domain['retained_K'])
            if domain is not None and domain['N_min'] == domain['N_max'] and unchanged_clocks:
                def observe(event):
                    save(out / 'progress.json', dict(case=case, stage='exact_refinement', **event))
                    with (out / 'trace.jsonl').open('a', encoding='utf-8') as stream:
                        stream.write(json.dumps(event, ensure_ascii=False) + '\n')
                best, info = decomposition_sync.solve(p, witness, domain['retained_K'], domain['N_min'],
                                                     lower, library, phase, remaining, seed, on_event=observe)
                lower = max(lower, info['global_lower_bound'])
                save(out / 'algorithm_summary.json', info)
            else:
                helper = module(HELPER_PATH, f'native_helper_{case}_{seed}')
                helper.save = save
                cert = domain or dict(retained_K=list(p.batch_count_candidates), N_min=p.pool_min,
                                      N_max=p.pool_max, global_lower_bound=lower, audited_upper_bound=upper,
                                      exclusion_reason='FRESH_CERTIFIED_COST_COMPETITIVE_DOMAIN')
                cert['exclusion_reason'] = 'FRESH_CERTIFIED_COST_COMPETITIVE_DOMAIN'
                native, result = helper.solve_retained(p, cert, remaining, out)
                if result is not None and audit_solution(p, result)['objective'] < upper:
                    best = result
                if native['best_bound'] is not None:
                    assert native['best_bound'] <= upper + TOL
                    lower = max(lower, native['best_bound'])
                info.update(status=f"NATIVE_{native['status']}", native_info=native)
        final_audit = audit_solution(p, best)
        assert final_audit['valid'] and final_audit['periodic_replay']['valid']
        upper = final_audit['objective']
        assert lower <= upper + TOL
        closed = abs(upper - lower) <= TOL
        save(out / 'best_audited_result.json', best)
        save(out / 'audit.json', final_audit)
        row = dict(case=case, seed=seed, proven_optimal=closed, audited_upper_bound=upper,
                   global_lower_bound=lower, absolute_gap=max(0., upper-lower),
                   gap_over_upper=max(0., upper-lower)/max(1e-9, abs(upper)),
                   total_wall_seconds=time.perf_counter()-begun,
                   enumeration_seconds=enumeration_seconds, master_build_seconds=build_seconds,
                   input_only_master_seconds=raw['native_runtime'], input_only_master_work=raw['work'],
                   refinement_seconds=info['elapsed_seconds'], follower_seconds=info['follower_seconds'],
                   patterns=info['patterns'], cuts=info['cuts'], route_columns=len(library),
                   selected_K=best['cycle_batch_count'], selected_N=best['container_pool_size'],
                   original_K=list(p.batch_count_candidates), native_initial_master_status=raw['native_status'],
                   proof_method=info['status'], business_audit_valid=True, periodic_replay_valid=True,
                   historical_feasible_plan_used=True, historical_lower_bound_used=False,
                   historical_library_or_domain_used=False, initial_U=entry['initial_U'],
                   witness_path=entry['witness_path'], numerical_absolute_tolerance=TOL,
                   budget_seconds=budget, cold_start_end_to_end=False)
        save(out / 'summary.json', row)
        save(out / 'progress.json', dict(stage='complete', **row))
        print(json.dumps(row, ensure_ascii=False), flush=True)
        return row
    except Exception as error:
        save(out / 'error.json', dict(error=str(error), traceback=traceback.format_exc()))
        print(f'ERROR {case}: {error}', flush=True)
        raise


def run():
    plan = read(HERE / 'plan.json')
    verify(plan)
    rows = []
    started = time.perf_counter()
    failures = []
    for entry in plan['cases']:
        for seed in plan['seeds']:
            out = HERE / 'runs' / entry['case'] / f's{seed}'
            if (out / 'summary.json').exists():
                rows.append(read(out / 'summary.json'))
                continue
            if out.exists():
                raise RuntimeError(f'Investigate incomplete run before repeating: {out}')
            try:
                rows.append(run_case(entry, seed, plan['budget_seconds_per_case']))
            except Exception as error:
                failures.append(dict(case=entry['case'], seed=seed, error=str(error)))
            save(HERE / 'progress.json', dict(completed=len(rows), expected=9,
                 optimal=sum(r['proven_optimal'] for r in rows), failures=failures, rows=rows))
    verify(plan)
    save(HERE / 'execution.json', dict(driver_seconds=time.perf_counter()-started, rows=rows, failures=failures))
    if failures:
        raise RuntimeError(f'{len(failures)} trials failed; evidence preserved')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'run'])
    args = parser.parse_args()
    prepare() if args.action == 'prepare' else run()
