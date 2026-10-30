"""Use S05's improved upper bound to certify K=2,N=4 and solve exactly."""
import hashlib
import json
from pathlib import Path
import sys
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))
from run_case import derive_reduction
from experiments.runs.probe_model8_physical_bounds import source_hashes
from experiments.runs.validate_model8_reduction_s04 import enhanced_run
from models.common.cycle import load_instance
from tools.configuration.loader import load_routing_case


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def main():
    begun = time.perf_counter()
    out = HERE / 'S05/refinement'
    out.mkdir(exist_ok=False)
    case = ROOT / 'data/formal_benchmark27_uniform_7_20_50_v1/model_8/small/S05/case.json'
    cfg, fleet = load_routing_case(case, use_pool_config=False)
    p = load_instance(cfg, fleet)
    witness_path = HERE / 'S05/native_result.json'
    witness = json.loads(witness_path.read_text(encoding='utf-8-sig'))
    files = [case, witness_path, Path(__file__), HERE / 'run_case.py',
             ROOT / 'experiments/runs/validate_model8_reduction_s04.py',
             ROOT / 'experiments/runs/research_model8_shared_relaxation.py']
    for key in ('data', 'routing_config', 'economic_config', 'orders', 'operating_policy',
                'parameter_contract', 'batch_count_certificate', 'dataset_metadata'):
        if cfg.get(key):
            files.append((ROOT / cfg[key]).resolve())
    hashes = {str(path.resolve()): sha(path) for path in files}
    sources = source_hashes()
    save(out / 'manifest.json', dict(
        case='S05', original_K=list(p.batch_count_candidates),
        refinement_of='S05/summary.json', first_optimization_budget_seconds=600,
        refinement_optimization_budget_seconds=600,
        threads=1, seed=42, formulation='compact', strengthening='physical_time',
        preprocessing=False, vehicle_symmetry=False, case_process_concurrency=1,
        historical_scalar_upper_bound_used=True, mip_start_supplied=False,
        input_and_driver_hashes=hashes, source_hashes=sources,
        budget_scope='Additional 600-second optimization budget after the first round; '
                     'reduction, load, build, audit and preflight separately recorded.',
    ))
    certificate = derive_reduction(p, witness, out)
    assert certificate['retained_K'] == [2]
    assert certificate['N_min'] == certificate['N_max'] == 4
    print(json.dumps(dict(stage='refined_reduction', case='S05', K=2, N=4,
                          upper=certificate['audited_upper_bound'])), flush=True)
    summary = enhanced_run(p, certificate, 600, out / 'enhanced')
    assert source_hashes() == sources
    assert all(sha(Path(path)) == digest for path, digest in hashes.items())
    save(out / 'completion.json', dict(
        case='S05', original_K=list(p.batch_count_candidates),
        sources_and_inputs_unchanged=True, production_solver_modified=False,
        native_status=summary['status'], objective=summary['objective'],
        best_bound=summary['best_bound'],
        external_full_domain_optimality_certified=summary['external_full_domain_optimality_certified'],
        driver_wall_seconds=time.perf_counter()-begun,
    ))


if __name__ == '__main__':
    main()
