"""Recompute full-domain reductions and attempt S04's complete exact optimum.

Use the previous independently audited complete solution only as a scalar
upper bound. Keep the production model, integer clock and all business rules.
"""
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from experiments.runs.validate_model8_reduction_s04 import (
    derive_certificate, enhanced_run, save,
)
from experiments.runs.probe_model8_physical_bounds import source_hashes
from models.common.cycle import load_instance
from tools.configuration.loader import load_routing_case

HERE = Path(__file__).resolve().parent


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    begun = time.perf_counter()
    if (HERE / 'manifest.json').exists():
        raise FileExistsError('This experiment already has a manifest; use a new directory.')
    case = ROOT / 'data/formal_benchmark27_uniform_7_20_50_v1/model_8/small/S04/case.json'
    cfg, fleet = load_routing_case(case, use_pool_config=False)
    problem = load_instance(cfg, fleet)
    witness_path = ROOT / 'output/experiments/m8_s04_reduction_validation_20261026_v1/enhanced/result.json'
    witness = json.loads(witness_path.read_text(encoding='utf-8-sig'))
    files = [case, witness_path, Path(__file__),
             ROOT / 'experiments/runs/validate_model8_reduction_s04.py',
             ROOT / 'experiments/runs/research_model8_shared_relaxation.py']
    for key in ('data', 'routing_config', 'economic_config', 'orders',
                'operating_policy', 'parameter_contract', 'batch_count_certificate',
                'dataset_metadata'):
        if cfg.get(key):
            files.append((ROOT / cfg[key]).resolve())
    hashes = {str(path.resolve()): sha(path) for path in files}
    sources = source_hashes()
    save(HERE / 'manifest.json', dict(
        case='S04', customers=len(problem.orders), original_K=list(problem.batch_count_candidates),
        optimization_limit_seconds=600, threads=1, seed=42, formulation='compact',
        strengthening='physical_time', preprocessing=False, vehicle_symmetry=False,
        historical_scalar_upper_bound=True, mip_start_supplied=False,
        input_and_driver_hashes=hashes, source_hashes=sources,
        budget_scope='600 seconds optimization; preflight, reduction, build and audit recorded separately',
        reused_witness=str(witness_path), production_solver_modified=False,
    ))
    certificate = derive_certificate(problem, witness, HERE)
    print(json.dumps(dict(stage='reduction_complete', K=[certificate['K_min'], certificate['K_max']],
                          N=[certificate['N_min'], certificate['N_max']],
                          lower_bound=certificate['global_lower_bound'],
                          upper_bound=certificate['audited_upper_bound'],
                          seconds=certificate['wall_seconds'])), flush=True)
    summary = enhanced_run(problem, certificate, 600, HERE / 'enhanced')
    assert source_hashes() == sources
    assert all(sha(Path(path)) == digest for path, digest in hashes.items())
    save(HERE / 'completion.json', dict(
        sources_and_inputs_unchanged=True,
        production_solver_modified=False,
        original_K=list(problem.batch_count_candidates),
        status=summary['status'], objective=summary['objective'], best_bound=summary['best_bound'],
        external_full_domain_optimality_certified=summary['external_full_domain_optimality_certified'],
        driver_wall_seconds=time.perf_counter()-begun,
    ))


if __name__ == '__main__':
    main()
