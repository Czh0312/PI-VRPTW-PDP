"""Full-domain certified reductions followed by the complete retained M8 MILP.

Run independent cases in separate processes. Keep all retained K blocks in
one MILP and keep each original K's integer clock. A historical audited cost
is supplied as a scalar bound; its routes are never a MIP start or a hint.
"""
import argparse
from functools import partial
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from experiments.runs.probe_model8_physical_bounds import source_hashes
from experiments.runs.research_model8_shared_relaxation import build_relaxation, enumerate_trip_library
from models.common.cycle import load_instance
from models.common.cycle.audit import audit_solution
from models.common.cycle.calendar import attach_calendar_decisions
from models.common.cycle.unified_milp import build_unified_model
from models.common.solver_config import accept_milp_audit, read_milp_info
from models.routing.model_8.exact.formulation import build_model
from models.routing.model_8.exact.solution import extract_solution
from tools.configuration.loader import load_routing_case

HERE = Path(__file__).resolve().parent
TOL = 1e-6


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    temporary.replace(path)


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def finite(value):
    return float(value) if math.isfinite(value) and abs(value) < 1e90 else None


def derive_reduction(problem, witness, out):
    import gurobipy as gp

    begun = time.perf_counter()
    audit = audit_solution(problem, witness)
    if not audit['valid'] or not audit['periodic_replay']['valid']:
        raise RuntimeError('Historical witness failed original-input audit')
    upper = audit['objective']
    library, prefixes = enumerate_trip_library(problem)
    model, ctx = build_relaxation(problem, library)
    try:
        model.Params.TimeLimit = 30
        model.optimize()
        if model.Status != gp.GRB.OPTIMAL:
            raise RuntimeError('Shared relaxation did not reach optimality')
        lower = model.ObjBound
        if lower > upper + TOL:
            raise RuntimeError('Relaxation bound exceeds audited upper bound')
        model.addConstr(model.getObjective() <= upper + 1e-7, name='audited_cost_cap')
        count = gp.quicksum(k * selector for k, selector in ctx['selectors'].items())
        values = {}
        for name, expression, sign in [('K_min', count, 1), ('K_max', count, -1),
                                       ('N_min', ctx['pool'], 1), ('N_max', ctx['pool'], -1)]:
            model.setObjective(sign * expression)
            model.optimize()
            if model.Status != gp.GRB.OPTIMAL:
                raise RuntimeError(f'Certified endpoint {name} did not reach optimality')
            values[name] = round(sign * model.ObjVal)
        retained = [k for k in problem.batch_count_candidates if values['K_min'] <= k <= values['K_max']]
        certificate = dict(
            **values, original_K=list(problem.batch_count_candidates), retained_K=retained,
            global_lower_bound=lower, audited_upper_bound=upper,
            route_columns=len(library), enumerated_prefixes=prefixes,
            witness_audit=audit, wall_seconds=time.perf_counter()-begun,
            proof='All original solutions of cost <= audited U project into the cost-capped '
                  'shared relaxation. Its certified K/N extrema retain every original optimum.',
            exclusion_reason='COST_DOMINATED_BY_AUDITED_UPPER_BOUND_NOT_BUSINESS_INFEASIBILITY',
        )
        save(out / 'reduction_certificate.json', certificate)
        save(out / 'trip_library.json', library)
        return certificate
    finally:
        model.dispose()


def solve_retained(problem, certificate, seconds, out):
    import gurobipy as gp

    begun = time.perf_counter()
    retained = certificate['retained_K']

    def fixed(child, **kwargs):
        model, ctx = build_model(child, strengthening='physical_time', preprocessing=False,
                                 vehicle_symmetry=False, **kwargs)
        ctx['pool'].LB = max(ctx['pool'].LB, certificate['N_min'])
        ctx['pool'].UB = min(ctx['pool'].UB, certificate['N_max'])
        model.update()
        return model, ctx

    def exclude(child):
        count = len(child.operating_policy.batches)
        if count not in retained:
            return dict(reason=certificate['exclusion_reason'], K=count,
                        upper_bound=certificate['audited_upper_bound'],
                        witness='reduction_certificate.json',
                        scope='Necessary domain for all original solutions of cost <= audited U')
        return None

    model, ctx = build_unified_model(problem, build_fixed=fixed, compact_e2=True,
                                     count_certificate=exclude)
    try:
        assert list(ctx['selectors']) == list(problem.batch_count_candidates)
        assert sorted(ctx['blocks']) == retained
        objective = model.getObjective()
        model.addConstr(objective >= certificate['global_lower_bound'] - 1e-7,
                        name='certified_shared_cost_lower_bound')
        model.addConstr(objective <= certificate['audited_upper_bound'] + 1e-7,
                        name='audited_incumbent_cost_upper_bound')
        model.update()
        assert model.NumStart == 0
        build_seconds = time.perf_counter()-begun
        model.Params.TimeLimit = seconds
        model.Params.Threads = 1
        model.Params.Seed = 42
        model.Params.OutputFlag = 1
        model.Params.LogToConsole = 0
        model.Params.LogFile = os.path.relpath(out / 'gurobi.log', ROOT)
        incumbents = []
        last_progress = -20.

        def observe(m, where):
            nonlocal last_progress
            if where == gp.GRB.Callback.MIPSOL:
                incumbents.append(dict(seconds=m.cbGet(gp.GRB.Callback.RUNTIME),
                                       objective=m.cbGet(gp.GRB.Callback.MIPSOL_OBJ)))
            if where == gp.GRB.Callback.MIP:
                elapsed = m.cbGet(gp.GRB.Callback.RUNTIME)
                if elapsed - last_progress >= 10:
                    save(out / 'progress.json', dict(
                        stage='optimization', seconds=elapsed,
                        native_incumbent=finite(m.cbGet(gp.GRB.Callback.MIP_OBJBST)),
                        native_bound=finite(m.cbGet(gp.GRB.Callback.MIP_OBJBND)),
                        nodes=m.cbGet(gp.GRB.Callback.MIP_NODCNT),
                    ))
                    last_progress = elapsed

        print(json.dumps(dict(stage='optimization', retained_K=retained,
                              pool=[certificate['N_min'], certificate['N_max']],
                              variables=model.NumVars, constraints=model.NumConstrs+model.NumGenConstrs,
                              seconds=seconds, build_seconds=build_seconds)), flush=True)
        model.optimize(observe)
        scope = 'complete_M8_original_constraints_and_K_clocks_in_certified_cost_competitive_domain'
        info = read_milp_info(model, proof_scope=scope, build_time=build_seconds)
        info.update(incumbents=incumbents, original_K=list(problem.batch_count_candidates),
                    retained_K=retained, retained_pool=[certificate['N_min'], certificate['N_max']],
                    screened_counts=ctx['screened_counts'], historical_scalar_upper_bound_used=True,
                    mip_start_supplied=False, route_hints_supplied=False,
                    observational_callback=True, optimization_limit_seconds=seconds)
        result = None
        if model.SolCount:
            selected = [k for k, selector in ctx['selectors'].items() if selector.X > .5]
            if len(selected) != 1:
                raise RuntimeError('Incumbent must select exactly one K')
            k = selected[0]
            block = ctx['blocks'][k]
            result = extract_solution(block['problem'], model, block['context'], dict(info))
            boundaries = [b['start'] for b in result['batches']] + [block['problem'].operating_policy.cycle_end]
            attach_calendar_decisions(problem, result, k, boundaries)
            result['audit'] = audit_solution(problem, result)
            result['info']['proof_scope'] = scope
            accept_milp_audit(result, model.ObjVal)
            if not result['audit']['valid'] or not result['audit']['periodic_replay']['valid']:
                raise RuntimeError('Native complete-model solution failed original full-domain audit')
            info.update(objective=result['info']['objective'], mip_gap=result['info']['mip_gap'],
                        audit_valid=True, periodic_replay_valid=True, selected_K=k,
                        selected_pool=result['container_pool_size'],
                        max_constraint_violation=model.ConstrVio, max_integer_violation=model.IntVio)
            result['info'].update(info)
            save(out / 'native_result.json', result)
        if model.Status in (gp.GRB.INFEASIBLE, gp.GRB.INF_OR_UNBD):
            raise RuntimeError('Certified competitive domain unexpectedly excludes its feasible witness')
        info['wall_seconds'] = time.perf_counter()-begun
        save(out / 'native_summary.json', info)
        return info, result
    finally:
        model.dispose()


def run(args):
    begun = time.perf_counter()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    case = ROOT / f'data/formal_benchmark27_uniform_7_20_50_v1/model_8/small/{args.case}/case.json'
    cfg, fleet = load_routing_case(case, use_pool_config=False)
    problem = load_instance(cfg, fleet)
    witness_path = ROOT / f'output/experiments/small7_m8_alns300_20261024_v1/runs/{args.case}_m8_alns_300s_s42/attempt_001/result.json'
    witness = read(witness_path)
    files = [case, witness_path, Path(__file__), ROOT / 'experiments/runs/research_model8_shared_relaxation.py']
    for key in ('data', 'routing_config', 'economic_config', 'orders', 'operating_policy',
                'parameter_contract', 'batch_count_certificate', 'dataset_metadata'):
        if cfg.get(key):
            files.append((ROOT / cfg[key]).resolve())
    hashes = {str(path.resolve()): sha(path) for path in files}
    sources = source_hashes()
    save(out / 'manifest.json', dict(
        case=args.case, original_K=list(problem.batch_count_candidates), customers=len(problem.orders),
        optimization_limit_seconds=args.seconds, threads=1, seed=42, formulation='compact',
        strengthening='physical_time', preprocessing=False, vehicle_symmetry=False,
        case_process_concurrency=5, input_and_driver_hashes=hashes, source_hashes=sources,
        witness_path=str(witness_path), production_solver_modified=False,
        historical_scalar_upper_bound_used=True, mip_start_supplied=False, route_hints_supplied=False,
        budget_scope='One 600-second optimization budget per original case; retained K share one MILP. '
                     'Preflight, load, reduction, construction and audit are outside optimization budget.',
    ))
    certificate = derive_reduction(problem, witness, out)
    print(json.dumps(dict(stage='reduction', case=args.case, retained_K=certificate['retained_K'],
                          N=[certificate['N_min'], certificate['N_max']],
                          lower=certificate['global_lower_bound'], upper=certificate['audited_upper_bound'])), flush=True)
    native, result = solve_retained(problem, certificate, args.seconds, out)
    best = result if result is not None and native['objective'] <= certificate['audited_upper_bound'] else witness
    best_audit = audit_solution(problem, best)
    assert best_audit['valid'] and best_audit['periodic_replay']['valid']
    upper = best_audit['objective']
    lower = certificate['global_lower_bound']
    if native['best_bound'] is not None:
        lower = max(lower, min(certificate['audited_upper_bound'], native['best_bound']))
    if lower > upper + TOL:
        raise RuntimeError('Combined global lower bound exceeds audited incumbent')
    closed = abs(upper-lower) <= TOL
    assert source_hashes() == sources
    assert all(sha(Path(path)) == digest for path, digest in hashes.items())
    save(out / 'best_audited_result.json', best)
    summary = dict(
        case=args.case, native_status=native['status'], original_K=list(problem.batch_count_candidates),
        retained_K=certificate['retained_K'], retained_pool=[certificate['N_min'], certificate['N_max']],
        best_audited_upper_bound=upper, global_lower_bound=lower,
        absolute_gap=max(0., upper-lower), gap_over_upper=max(0., upper-lower)/max(1e-9, abs(upper)),
        external_full_domain_optimality_certified=closed,
        selected_K=best['cycle_batch_count'], selected_pool=best['container_pool_size'],
        best_witness_source='native_result.json' if best is result else str(witness_path),
        native_objective=native['objective'], native_best_bound=native['best_bound'],
        mip_runtime=native['mip_runtime'], build_seconds=native['build_time'],
        reduction_seconds=certificate['wall_seconds'], driver_wall_seconds=time.perf_counter()-begun,
        variables=native['variables'], constraints=native['constraints'],
        business_audit_valid=True, periodic_replay_valid=True,
        original_sources_and_inputs_unchanged=True, production_solver_modified=False,
        historical_scalar_upper_bound_used=True, mip_start_supplied=False,
        proof='The certified domain contains every original optimum. The minimum of its native bound '
              'and historical audited U, combined with the shared global lower bound, is a valid '
              'full-original-domain lower bound. Equality with an audited feasible cost closes the proof.',
        numerical_absolute_tolerance=TOL,
    )
    save(out / 'summary.json', summary)
    save(out / 'progress.json', dict(stage='complete', **summary))
    print(json.dumps(summary), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', required=True, choices=['S03', 'S05', 'S07', 'S08', 'S09'])
    parser.add_argument('--seconds', type=float, default=600)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if not math.isfinite(args.seconds) or args.seconds <= 0:
        parser.error('--seconds must be finite and positive')
    try:
        run(args)
    except Exception as error:
        if args.output.is_dir():
            save(args.output / 'error.json', dict(error=str(error), traceback=traceback.format_exc()))
        raise


if __name__ == '__main__':
    main()
