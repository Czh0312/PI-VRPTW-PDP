"""S04: rederive reductions, solve the complete retained MILP, compare baseline.

No production solver changes. An audited historical scalar upper bound is
used in the enhanced arm; no routes, starts or hints are injected into either
full MILP. Both use identical original physical inputs and clocks.
"""
import argparse
from functools import partial
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from experiments.runs.research_model8_shared_relaxation import enumerate_trip_library, build_relaxation
from experiments.runs.probe_model8_physical_bounds import run as baseline_run, source_hashes
from models.common.cycle import load_instance
from models.common.cycle.audit import audit_solution
from models.common.cycle.calendar import attach_calendar_decisions
from models.common.solver_config import read_milp_info, accept_milp_audit
from models.routing.model_8.exact.formulation import build_model
from models.routing.model_8.exact.solution import extract_solution
from tools.configuration.loader import load_routing_case


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def derive_certificate(p, witness, out):
    import gurobipy as gp
    begun = time.perf_counter()
    audit = audit_solution(p, witness)
    assert audit['valid'] and audit['periodic_replay']['valid']
    U = audit['objective']
    library, prefixes = enumerate_trip_library(p)
    model, ctx = build_relaxation(p, library)
    try:
        model.Params.TimeLimit = 30
        model.optimize()
        assert model.Status == gp.GRB.OPTIMAL
        L = model.ObjBound
        model.addConstr(model.getObjective() <= U + 1e-7, name='audited_cost_cap')
        K = gp.quicksum(k * v for k, v in ctx['selectors'].items())
        values = {}
        for name, expression, sign in [('K_min', K, 1), ('K_max', K, -1),
                                       ('N_min', ctx['pool'], 1), ('N_max', ctx['pool'], -1)]:
            model.setObjective(sign * expression)
            model.optimize()
            assert model.Status == gp.GRB.OPTIMAL
            values[name] = round(sign * model.ObjVal)
        assert values['K_min'] == values['K_max'] == 2
        assert values['N_min'] == values['N_max'] == 4
        certificate = dict(**values, global_lower_bound=L, audited_upper_bound=U,
                           original_K=list(p.batch_count_candidates), route_columns=len(library),
                           enumerated_prefixes=prefixes, wall_seconds=time.perf_counter()-begun,
                           witness_audit=audit, proof='Every original solution of cost <= audited U '
                           'projects to the cost-capped relaxation; its optimal K/N extrema imply K=2,N=4.',
                           uses_historical_scalar_upper_bound=True, uses_historical_routes_as_start=False)
        save(out/'reduction_certificate.json', certificate)
        return certificate
    finally:
        model.dispose()


def enhanced_run(p, certificate, seconds, out):
    import gurobipy as gp
    out.mkdir(parents=True, exist_ok=True)
    begun = time.perf_counter()
    child = p.for_count(certificate['K_min'])
    model, ctx = build_model(child, compact_e2=True, strengthening='physical_time',
                             preprocessing=False, vehicle_symmetry=False)
    try:
        ctx['pool'].LB = certificate['N_min']
        ctx['pool'].UB = certificate['N_max']
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
        model.Params.LogFile = os.path.relpath(out/'gurobi.log')
        events = []

        def observe(m, where):
            if where == gp.GRB.Callback.MIPSOL:
                events.append(dict(seconds=m.cbGet(gp.GRB.Callback.RUNTIME),
                                   objective=m.cbGet(gp.GRB.Callback.MIPSOL_OBJ)))

        print(json.dumps(dict(stage='enhanced_optimization', variables=model.NumVars,
                              constraints=model.NumConstrs+model.NumGenConstrs,
                              seconds=seconds, K=2, N=4, build_seconds=build_seconds)), flush=True)
        model.optimize(observe)
        scope = 'complete_original_M8_constraints_at_certified_K2_N4_with_cost_cap'
        info = read_milp_info(model, proof_scope=scope, build_time=build_seconds)
        info.update(incumbents=events, first_solver_incumbent_seconds=events[0]['seconds'] if events else None,
                    original_K=list(p.batch_count_candidates), native_solved_K=[2],
                    pool_fixed_by_certificate=4, scalar_upper_bound_supplied=True,
                    mip_start_supplied=False, route_hints_supplied=False,
                    observational_callback=True, optimization_limit_seconds=seconds)
        if model.SolCount:
            result = extract_solution(child, model, ctx, dict(info))
            boundaries = [b['start'] for b in result['batches']]+[child.operating_policy.cycle_end]
            attach_calendar_decisions(p, result, 2, boundaries)
            result['audit'] = audit_solution(p, result)
            result['info']['proof_scope'] = scope
            accept_milp_audit(result, model.ObjVal)
            assert result['audit']['valid'] and result['audit']['periodic_replay']['valid']
            info.update(objective=result['info']['objective'], mip_gap=result['info']['mip_gap'],
                        audit_valid=True, periodic_replay_valid=True,
                        selected_K=2, pool=result['container_pool_size'],
                        max_constraint_violation=model.ConstrVio, max_integer_violation=model.IntVio)
            result['info'].update(info)
            save(out/'result.json', result)
        info['external_full_domain_optimality_certified'] = bool(
            model.Status == gp.GRB.OPTIMAL and info.get('audit_valid') and info['objective'] <= certificate['audited_upper_bound']+1e-6)
        info['wall_seconds'] = time.perf_counter()-begun
        info['enhanced_pipeline_seconds'] = certificate['wall_seconds']+info['wall_seconds']
        save(out/'summary.json', info)
        print(json.dumps(dict(stage='enhanced_complete', status=info['status'], objective=info['objective'],
                              bound=info['best_bound'], full_domain_optimal=info['external_full_domain_optimality_certified'],
                              total_seconds=info['enhanced_pipeline_seconds'])), flush=True)
        return info
    finally:
        model.dispose()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--seconds', type=float, default=120)
    parser.add_argument('--output', type=Path, default=ROOT/'output/experiments/m8_s04_reduction_validation_20261026_v1')
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    case = ROOT/'data/formal_benchmark27_uniform_7_20_50_v1/model_8/small/S04/case.json'
    cfg, fleet = load_routing_case(case, use_pool_config=False)
    p = load_instance(cfg, fleet)
    witness_path = ROOT/'output/experiments/small7_m8_alns300_20261024_v1/runs/S04_m8_alns_300s_s42/attempt_001/result.json'
    witness = json.loads(witness_path.read_text(encoding='utf-8-sig'))
    files = [case, witness_path, Path(__file__), ROOT/'experiments/runs/research_model8_shared_relaxation.py']
    files += [ROOT/cfg[k] for k in ('data', 'orders', 'routing_config', 'operating_policy', 'economic_config')]
    hashes = {str(path):hashlib.sha256(path.read_bytes()).hexdigest() for path in files}
    source = source_hashes()
    save(out/'manifest.json', dict(case='S04', customers=len(p.orders), K=list(p.batch_count_candidates),
         seconds_per_full_MILP=args.seconds, threads=1, seed=42, formulation='compact',
         strengthening='physical_time', preprocessing=False, vehicle_symmetry=False,
         enhanced_uses_historical_scalar_upper_bound=True, mip_starts=False,
         input_and_driver_hashes=hashes, source_hashes=source,
         budget_scope='optimization budgets equal; construction, reduction and audit separately recorded'))
    certificate = derive_certificate(p, witness, out)
    enhanced = enhanced_run(p, certificate, args.seconds, out/'enhanced')
    assert source_hashes() == source
    baseline_dir = out/'baseline'
    baseline_dir.mkdir()
    baseline = baseline_run(SimpleNamespace(seconds=args.seconds, seed=42, formulation='compact',
                            preprocessing=False, vehicle_symmetry=False, solution_limit=None),
                            p, 'physical_time', baseline_dir)
    assert source_hashes() == source
    assert all(hashlib.sha256(Path(path).read_bytes()).hexdigest() == digest for path,digest in hashes.items())
    save(out/'comparison.json', dict(reduction=certificate, enhanced=enhanced, baseline=baseline,
                                    sources_and_inputs_unchanged=True))


if __name__ == '__main__':
    main()
