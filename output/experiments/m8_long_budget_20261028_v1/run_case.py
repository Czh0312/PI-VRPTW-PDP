"""Long-budget M8 experiments with certified all-K reductions and audits.

Earlier native search trees were disposed. Each case rebuilds the same
complete formulation with the best audited scalar cost and 3600 seconds.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import time
import traceback
import zipfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
PREVIOUS = ROOT / 'output/experiments/m8_remaining_small_optimality_20261028_v1'
sys.path.insert(0, str(ROOT))

from experiments.runs.probe_model8_physical_bounds import source_hashes
from experiments.runs.research_model8_shared_relaxation import build_relaxation, enumerate_trip_library
from models.common.cycle import load_instance
from models.common.cycle.audit import audit_solution
from tools.configuration.loader import load_routing_case


def module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def save(path, value):
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    temporary.replace(path)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def phase_bound(problem, out, phase):
    import gurobipy as gp

    begun = time.perf_counter()
    library, _ = enumerate_trip_library(problem)
    model, ctx = build_relaxation(problem, library)
    try:
        phase.augment(problem, model, ctx)
        model.Params.TimeLimit = 30
        model.optimize()
        if model.Status != gp.GRB.OPTIMAL:
            raise RuntimeError('Phase-ordered all-K relaxation did not reach optimality')
        value = dict(original_K=list(problem.batch_count_candidates), lower_bound=model.ObjBound,
                     status='OPTIMAL_RELAXATION_NOT_COMPLETE_M8_SOLUTION',
                     wall_seconds=time.perf_counter()-begun, optimize_seconds=model.Runtime)
        save(out / 'phase_bound.json', value)
        return value
    finally:
        model.dispose()


def run(args):
    begun = time.perf_counter()
    out = HERE / args.case
    out.mkdir(exist_ok=False)
    case = ROOT / f'data/formal_benchmark27_uniform_7_20_50_v1/model_8/small/{args.case}/case.json'
    cfg, fleet = load_routing_case(case, use_pool_config=False)
    problem = load_instance(cfg, fleet)
    witness_path = PREVIOUS / args.case / 'final_best_result.json'
    prior_path = PREVIOUS / args.case / 'final_summary.json'
    witness = read(witness_path)
    prior = read(prior_path)
    solver_path = PREVIOUS / 'run_case.py'
    phase_path = ROOT / 'output/experiments/m8_s04_reduction_validation_20261026_v1/phase_bound.py'
    solver = module(solver_path, 'frozen_reduction_solver')
    phase = module(phase_path, 'frozen_phase_bound')
    files = [case, witness_path, prior_path, Path(__file__), solver_path, phase_path,
             ROOT / 'experiments/runs/research_model8_shared_relaxation.py']
    for key in ('data', 'routing_config', 'economic_config', 'orders', 'operating_policy',
                'parameter_contract', 'batch_count_certificate', 'dataset_metadata'):
        if cfg.get(key):
            files.append((ROOT / cfg[key]).resolve())
    hashes = {str(path.resolve()): sha(path) for path in files}
    sources = source_hashes()
    with zipfile.ZipFile(out / 'frozen_sources_inputs.zip', 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        frozen = dict(hashes)
        frozen.update({str((ROOT / path).resolve()): digest for path, digest in sources.items()})
        for path, digest in frozen.items():
            content = Path(path).read_bytes()
            if hashlib.sha256(content).hexdigest() != digest:
                raise RuntimeError('A source or input changed while freezing')
            archive.writestr(Path(path).relative_to(ROOT).as_posix(), content)
    save(out / 'manifest.json', dict(
        case=args.case, original_K=list(problem.batch_count_candidates), customers=len(problem.orders),
        optimization_limit_seconds=args.seconds, threads=1, seed=42, formulation='compact',
        strengthening='physical_time', preprocessing=False, vehicle_symmetry=False,
        case_process_concurrency=3, source_hashes=sources, input_and_driver_hashes=hashes,
        reused_solver_module=str(solver_path), phase_module=str(phase_path),
        witness_path=str(witness_path), prior_summary=str(prior_path),
        previous_search_tree_reused=False, restarted_complete_MILP=True,
        mip_start_supplied=False, route_hints_supplied=False,
        historical_scalar_upper_bound_used=True, production_solver_modified=False,
        frozen_archive_sha256=sha(out / 'frozen_sources_inputs.zip'),
        budget_scope=f'One {args.seconds:g}-second optimization budget per original case; '
                     'retained K share one complete MILP. Preflight, reduction, load, build and audit are separate.',
    ))
    certificate = solver.derive_reduction(problem, witness, out)
    phase_row = phase_bound(problem, out, phase)
    solver_certificate = dict(certificate)
    solver_certificate.update(
        shared_relaxation_lower_bound=certificate['global_lower_bound'],
        phase_ordered_lower_bound=phase_row['lower_bound'],
        global_lower_bound=max(certificate['global_lower_bound'], phase_row['lower_bound']),
        lower_bound_sources=['reduction_certificate.json', 'phase_bound.json'],
    )
    if solver_certificate['global_lower_bound'] > certificate['audited_upper_bound'] + 1e-6:
        raise RuntimeError('Lower bound exceeds audited feasible cost')
    save(out / 'solver_certificate.json', solver_certificate)
    print(json.dumps(dict(stage='long_run_reduction', case=args.case, retained_K=certificate['retained_K'],
                          N=[certificate['N_min'], certificate['N_max']],
                          lower=solver_certificate['global_lower_bound'], upper=certificate['audited_upper_bound'],
                          optimization_limit_seconds=args.seconds)), flush=True)
    native, result = solver.solve_retained(problem, solver_certificate, args.seconds, out)
    best = result if result is not None and native['objective'] <= certificate['audited_upper_bound'] else witness
    audit = audit_solution(problem, best)
    if not audit['valid'] or not audit['periodic_replay']['valid']:
        raise RuntimeError('Best complete solution failed original-input audit')
    upper = audit['objective']
    lower = solver_certificate['global_lower_bound']
    if native['best_bound'] is not None:
        lower = max(lower, min(certificate['audited_upper_bound'], native['best_bound']))
    if lower > upper + 1e-6:
        raise RuntimeError('Global bound exceeds audited feasible cost')
    assert source_hashes() == sources
    assert all(sha(Path(path)) == digest for path, digest in hashes.items())
    save(out / 'best_audited_result.json', best)
    summary = dict(
        case=args.case, original_K=list(problem.batch_count_candidates),
        retained_K=certificate['retained_K'], retained_pool=[certificate['N_min'], certificate['N_max']],
        native_status=native['status'], native_objective=native['objective'], native_bound=native['best_bound'],
        best_audited_upper_bound=upper, global_lower_bound=lower,
        gap_over_upper=max(0., upper-lower)/abs(upper),
        external_full_domain_optimality_certified=abs(upper-lower) <= 1e-6,
        selected_K=best['cycle_batch_count'], selected_pool=best['container_pool_size'],
        best_witness_source='native_result.json' if best is result else str(witness_path),
        optimization_limit_seconds=args.seconds, mip_runtime=native['mip_runtime'],
        reduction_seconds=certificate['wall_seconds'], phase_bound_seconds=phase_row['wall_seconds'],
        build_seconds=native['build_time'], driver_wall_seconds=time.perf_counter()-begun,
        variables=native['variables'], constraints=native['constraints'],
        prior_upper_bound=prior['best_audited_upper_bound'], prior_lower_bound=prior['global_lower_bound'],
        prior_gap_over_upper=prior['gap_over_upper'], prior_optimization_seconds=prior['mip_runtime'],
        original_sources_and_inputs_unchanged=True, business_audit_valid=True, periodic_replay_valid=True,
        historical_scalar_upper_bound_used=True, mip_start_supplied=False,
        previous_search_tree_reused=False, production_solver_modified=False,
    )
    save(out / 'summary.json', summary)
    save(out / 'progress.json', dict(stage='complete', **summary))
    print(json.dumps(summary), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', required=True, choices=['S03', 'S08', 'S09'])
    parser.add_argument('--seconds', type=float, default=3600)
    args = parser.parse_args()
    if args.seconds <= 0:
        parser.error('Positive --seconds required')
    try:
        run(args)
    except Exception as error:
        folder = HERE / args.case
        if folder.is_dir():
            save(folder / 'error.json', dict(error=str(error), traceback=traceback.format_exc()))
        raise


if __name__ == '__main__':
    main()
