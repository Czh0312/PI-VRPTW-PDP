"""Run one case's separately scoped M8/M9 fixed-K=2 diagnostics."""
import json
from pathlib import Path
import sys

from cli.multibatch import run_case
from tools.configuration.loader import load_routing_case, PROJECT_ROOT


case = sys.argv[1]
if case not in ('S03', 'S04'):
    raise ValueError('This experiment is limited to S03 and S04')
folder = PROJECT_ROOT/'output/experiments/model89_other_small_exact_20261030_v1'/case
progress = []
for model in (8, 9):
    config_path = folder/f'M{model}_fixedK2_case.json'
    config, fleet = load_routing_case(config_path)
    assert config['model'] == model and config['base_case_id'] == case
    assert config['solver'] == 'exact' and config['seed'] == 42 and config['exact_time'] == 300
    assert config['joint_batch_options']['batch_count_candidates'] == [2]
    assert config['milp_options'] == {'threads': 1, 'formulation': 'compact'}
    print(f'BEGIN {case} M{model} K=2 diagnostic seed42 300s', flush=True)
    result = run_case(config, fleet, case_path=str(config_path),
                      output=str(folder/f'M{model}_fixedK2_seed42_300s'))
    row = dict(case=case, model=model, domain=[2], seed=42, budget_seconds=300,
               status=result['info']['status'], cost=result['info']['objective'],
               bound=result['info'].get('best_bound'), gap=result['info'].get('mip_gap'),
               proven_optimal_within_K2=result['info']['proven_optimal'],
               audit_valid=result.get('audit', {}).get('valid', False))
    progress.append(row)
    (folder/'fixed_pair_progress.json').write_text(
        json.dumps(progress, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    print(json.dumps(row, ensure_ascii=False), flush=True)
