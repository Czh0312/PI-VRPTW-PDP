"""Freeze matched M8 stability inputs, audited U and certified domains."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))
from models.common.cycle import load_instance
from models.common.cycle.audit import audit_solution
from tools.configuration.loader import load_routing_case
from experiments.runs.research_model8_shared_relaxation import build_relaxation, enumerate_trip_library


def module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def main():
    import gurobipy as gp

    previous = ROOT / 'output/experiments/m8_remaining_small_optimality_20261028_v1'
    solver_path = previous / 'run_case.py'
    phase_path = ROOT / 'output/experiments/m8_s04_reduction_validation_20261026_v1/phase_bound.py'
    helper = module(solver_path, 'existing_domain_certificates')
    phase = module(phase_path, 'existing_phase_bound')
    for case in ['S03', 'S08', 'S05']:
        out = HERE / 'inputs' / case
        out.mkdir(parents=True, exist_ok=False)
        case_path = ROOT / f'data/formal_benchmark27_uniform_7_20_50_v1/model_8/small/{case}/case.json'
        cfg, fleet = load_routing_case(case_path, use_pool_config=False)
        p = load_instance(cfg, fleet)
        if case in ('S03', 'S08'):
            prior_folder = ROOT / 'output/experiments/m8_long_budget_20261028_v1' / case
            witness_path = prior_folder / 'best_audited_result.json'
            prior = read(prior_folder / 'summary.json')
            prior_certificate = read(prior_folder / 'reduction_certificate.json')
        else:
            prior_folder = previous / case
            witness_path = prior_folder / 'final_best_result.json'
            prior = read(prior_folder / 'final_summary.json')
            prior_certificate = read(prior_folder / 'refinement/reduction_certificate.json')
        witness = read(witness_path)
        audit = audit_solution(p, witness)
        assert audit['valid'] and audit['periodic_replay']['valid']
        certificate = helper.derive_reduction(p, witness, out)
        library, _ = enumerate_trip_library(p)
        model, ctx = build_relaxation(p, library)
        try:
            phase.augment(p, model, ctx)
            model.Params.TimeLimit = 30
            model.optimize()
            assert model.Status == gp.GRB.OPTIMAL
            floor = model.ObjBound
        finally:
            model.dispose()
        # Hard-case variants use the SAME already-proven bound. For the fast
        # control, do not supply its known optimum as an artificial lower bound.
        if case in ('S03', 'S08'):
            floor = max(floor, prior['global_lower_bound'])
        old_K = [k for k in p.batch_count_candidates if prior_certificate['K_min'] <= k <= prior_certificate['K_max']]
        files = [case_path, witness_path, solver_path, phase_path, Path(__file__)]
        for key in ('data', 'routing_config', 'economic_config', 'orders', 'operating_policy',
                    'parameter_contract', 'batch_count_certificate', 'dataset_metadata'):
            if cfg.get(key):
                files.append((ROOT / cfg[key]).resolve())
        value = dict(case=case, case_path=str(case_path), witness_path=str(witness_path),
                     original_K=list(p.batch_count_candidates), audited_upper_bound=audit['objective'],
                     common_lower_bound=floor, previous_K=old_K,
                     previous_N=[prior_certificate['N_min'], prior_certificate['N_max']],
                     reduced_K=certificate['retained_K'], reduced_N=[certificate['N_min'], certificate['N_max']],
                     witness_audit=audit, input_hashes={str(path): sha(path) for path in files},
                     lower_bound_proof_path=str(prior_folder / ('summary.json' if case in ('S03','S08') else 'phase_bound.json')),
                     historical_information_used=True, mip_start_supplied=False,
                     pilot_seed=42, held_out_seeds=[7, 19, 31],
                     comparison='Same original problem, audited U, certified L, thread count and budget; '
                                'no historical route MIP start. Compare previous domain, rederived domain and additive route-mask bounds.')
        save(out / 'context.json', value)
        print(json.dumps({k:value[k] for k in ('case','audited_upper_bound','common_lower_bound','previous_K','previous_N','reduced_K','reduced_N')}), flush=True)


if __name__ == '__main__':
    main()
