"""Audit all known witnesses, strengthen shared bounds, and merge evidence."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from experiments.runs.research_model8_shared_relaxation import build_relaxation, enumerate_trip_library
from models.common.cycle import load_instance
from models.common.cycle.audit import audit_solution
from tools.configuration.loader import load_routing_case

HERE = Path(__file__).resolve().parent
CASES = ['S03', 'S05', 'S07', 'S08', 'S09']


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def problem(case):
    return load_instance(*load_routing_case(
        ROOT / f'data/formal_benchmark27_uniform_7_20_50_v1/model_8/small/{case}/case.json',
        use_pool_config=False,
    ))


def known_witnesses(case, p):
    historical = ROOT / f'output/experiments/m8_shared_relaxation_research_20261026_v2/{case}.json'
    paths = [ROOT / row['path'] for row in read(historical)['witness_audits']]
    paths.extend(HERE / case / name for name in ('best_audited_result.json', 'native_result.json'))
    paths.append(HERE / case / 'refinement/enhanced/result.json')
    audits = []
    for path in dict.fromkeys(paths):
        if path.exists():
            audit = audit_solution(p, read(path))
            audits.append(dict(path=str(path), sha256=sha(path), valid=audit['valid'],
                               periodic_replay_valid=audit.get('periodic_replay', {}).get('valid', False),
                               objective=audit.get('objective')))
    valid = [row for row in audits if row['valid'] and row['periodic_replay_valid']]
    if not valid:
        raise RuntimeError(f'{case}: no audited witness')
    return audits, min(valid, key=lambda row: row['objective'])


def phase_module():
    path = ROOT / 'output/experiments/m8_s04_reduction_validation_20261026_v1/phase_bound.py'
    spec = importlib.util.spec_from_file_location('phase_ordered_route_bound', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, path


def phase_bounds():
    import gurobipy as gp

    module, source = phase_module()
    for case in CASES:
        begun = time.perf_counter()
        p = problem(case)
        library, _ = enumerate_trip_library(p)
        model, ctx = build_relaxation(p, library)
        try:
            module.augment(p, model, ctx)
            model.Params.TimeLimit = 30
            model.optimize()
            if model.Status != gp.GRB.OPTIMAL:
                raise RuntimeError(f'{case}: phase relaxation did not reach optimality')
            audits, best = known_witnesses(case, p)
            if model.ObjBound > best['objective'] + 1e-6:
                raise RuntimeError('Phase bound exceeds audited feasible cost')
            row = dict(case=case, original_K=list(p.batch_count_candidates),
                       lower_bound=model.ObjBound, status='OPTIMAL_RELAXATION',
                       upper_bound_at_run=best['objective'], witness_audits=audits,
                       phase_source=str(source), phase_source_sha256=sha(source),
                       driver_sha256=sha(Path(__file__)),
                       optimize_seconds=model.Runtime, wall_seconds=time.perf_counter()-begun,
                       kind='LOWER_BOUND_RELAXATION_NOT_COMPLETE_M8_SOLUTION')
            save(HERE / case / 'phase_bound.json', row)
            print(json.dumps(dict(case=case, phase_lower=row['lower_bound'],
                                  audited_upper=best['objective'])), flush=True)
        finally:
            model.dispose()


def merge():
    rows = []
    for case in CASES:
        p = problem(case)
        run = read(HERE / case / 'summary.json')
        phase = read(HERE / case / 'phase_bound.json')
        audits, best = known_witnesses(case, p)
        lower = max(run['global_lower_bound'], phase['lower_bound'])
        refinement = None
        refined_path = HERE / case / 'refinement/enhanced/summary.json'
        if refined_path.exists():
            refined = read(refined_path)
            certificate = read(HERE / case / 'refinement/reduction_certificate.json')
            refined_lower = certificate['global_lower_bound']
            if refined['best_bound'] is not None:
                refined_lower = max(refined_lower, min(certificate['audited_upper_bound'], refined['best_bound']))
            lower = max(lower, refined_lower)
            refinement = dict(native_status=refined['status'], global_lower_bound=refined_lower,
                              retained_K=certificate['retained_K'],
                              retained_pool=[certificate['N_min'], certificate['N_max']],
                              native_objective=refined['objective'], mip_runtime=refined['mip_runtime'],
                              optimization_limit_seconds=refined['optimization_limit_seconds'],
                              driver_wall_seconds=read(HERE / case / 'refinement/completion.json')['driver_wall_seconds'])
        upper = best['objective']
        assert lower <= upper + 1e-6
        result = read(Path(best['path']))
        row = dict(run)
        row.update(
            best_audited_upper_bound=upper, global_lower_bound=lower,
            absolute_gap=max(0., upper-lower), gap_over_upper=max(0., upper-lower)/abs(upper),
            selected_K=result['cycle_batch_count'], selected_pool=result['container_pool_size'],
            external_full_domain_optimality_certified=abs(upper-lower) <= 1e-6,
            best_witness_source=best['path'], best_witness_sha256=best['sha256'],
            phase_bound=phase['lower_bound'], witness_audits=audits,
            refinement=refinement,
            final_native_status=refinement['native_status'] if refinement else run['native_status'],
            total_optimization_seconds=run['mip_runtime']+(refinement['mip_runtime'] if refinement else 0.),
        )
        save(HERE / case / 'final_summary.json', row)
        save(HERE / case / 'final_best_result.json', result)
        rows.append(row)
    save(HERE / 'summary.json', rows)
    print(json.dumps([{key: row[key] for key in (
        'case', 'native_status', 'best_audited_upper_bound', 'global_lower_bound', 'gap_over_upper',
        'external_full_domain_optimality_certified', 'selected_K', 'selected_pool', 'mip_runtime',
    )} for row in rows]), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['phase', 'merge'])
    args = parser.parse_args()
    if args.mode == 'phase':
        phase_bounds()
    else:
        merge()


if __name__ == '__main__':
    main()
