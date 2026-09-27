"""Bounded integration check of pair-only and joint-split M8 ALNS.

Two saved strong M3 baselines, original data, same seed and 10-second budgets.
This smoke comparison does not estimate optimal transfer value.
"""
import hashlib
import json
from pathlib import Path
import shutil

from experiments.runs.run_model8_capacity_probe import ROOT, load_problem
from experiments.runs.run_model8_small_comparison import write
from models.routing.alns.variants.model_8 import solve_alns
from models.routing.variants.model_8.baseline import prepare_baseline
from models.routing.variants.model_8.state import Evaluator


def main():
    folder = ROOT / 'output/experiments/model8_split_receivers_20260922'
    if folder.exists():
        raise FileExistsError('Preserve prior probe output')
    folder.mkdir(parents=True)
    rows = []
    sources = [Path(__file__).resolve(), ROOT / 'models/routing/alns/variants/model_8.py',
               ROOT / 'models/routing/variants/model_8/state.py',
               ROOT / 'models/routing/variants/model_8/baseline.py']
    hashes = {}
    for source in sources:
        relative = source.relative_to(ROOT)
        destination = folder / 'source' / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        hashes[str(relative)] = hashlib.sha256(source.read_bytes()).hexdigest()
    write(folder / 'manifest.json', {'seed': 42, 'seconds_per_branch': 10,
          'scope': 'integration_smoke_not_optimality_or_statistical_evidence',
          'source_sha256': hashes})
    for instance in ('Set2_100-5-1', 'Set2_200-10-2'):
        source = ROOT / 'output/experiments/model8_vs_model3_large_20260922/strong_baseline' / instance / 'base.json'
        saved = json.loads(source.read_text())
        p, a, b, data_source = load_problem(instance)
        base = prepare_baseline(p, a, b, p.fleet.e2_per_sat_count,
                                (saved['e1_routes'], saved['e2_routes']))
        evaluator = Evaluator(p, a, b, base)
        for receivers in (1, 3):
            x, y, info = solve_alns(
                p, a, b, p.fleet.e2_per_sat_count, max_time=10,
                max_iterations=1000000, seed=42, verbose=False, return_info=True,
                base_routes=(saved['e1_routes'], saved['e2_routes']), base_info=saved['info'],
                max_handoff_receivers=receivers)
            audit = evaluator.audit(info['state'])
            assert abs(audit['objective'] - info['objective']) < 1e-6
            assert info['baseline']['route_fingerprint'] == base['route_fingerprint']
            target = folder / f'{instance}_receivers{receivers}.json'
            write(target, {'e1_routes': x, 'e2_routes': y, 'info': info,
                          'base_source': str(source.relative_to(ROOT)),
                          'base_source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
                          'dataset_sha256': hashlib.sha256(data_source.read_bytes()).hexdigest()})
            # Verify serialization preserves the multiple-collection state.
            reloaded = json.loads(target.read_text())
            assert evaluator.audit(reloaded['info']['state']) == audit
            row = {'instance': instance, 'max_receivers': receivers, 'baseline': base['objective'],
                   'objective': info['objective'], 'iterations': info['iterations'],
                   'split_repair_audits': info['split_repair_audits'],
                   'feasible_split_repairs': info['feasible_split_repairs'],
                   'final_split_deposits': info['split_handoff_deposit_count'],
                   'deposits': info['handoff_deposit_count'],
                   'collection_visits': info['handoff_collection_visit_count'], 'audit_valid': True}
            rows.append(row)
            write(folder / 'verification.json', {'status': 'RUNNING', 'rows': rows})
            print(json.dumps(row), flush=True)
    write(folder / 'verification.json', {'status': 'PASS', 'rows': rows})


if __name__ == '__main__':
    main()
