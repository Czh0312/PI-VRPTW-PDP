# Historical M8 experiments: retain their original two-stage semantics.
"""Separate bounded mechanism run and multi-collection rejection diagnosis."""
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import runpy
import shutil
from unittest.mock import patch

from experiments.runs.run_model8_capacity_probe import ROOT, load_problem
from experiments.runs.run_model8_split_comparison import DEFAULT_OUTPUT
from experiments.runs.run_model8_small_comparison import write
from models.routing.variants.model_8.two_stage_v2.alns import solve_alns
from models.routing.variants.model_8.two_stage_v2.baseline import prepare_baseline
from models.routing.variants.model_8.two_stage_v2.state import Evaluator


def main():
    folder = ROOT / DEFAULT_OUTPUT
    if not (folder / 'summary.json').exists():
        raise RuntimeError('Finish the primary comparison and audit first')
    target = folder / 'diagnostic'
    target.mkdir(exist_ok=False)
    source = Path(__file__).resolve()
    shutil.copy2(source, target / source.name)
    write(target / 'manifest.json', {'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
          'scope': 'post-experiment diagnostics, do not merge into paired experiment',
          'benchmark_budget': 5, 'benchmark_seed': 19})
    demo = runpy.run_path(str(ROOT / 'tests/routing/test_model8_split.py'))
    p, a, b, base, evaluator, _ = demo['split_example']()
    x, y, info = solve_alns(p, a, b, 3, max_time=5, max_iterations=1000, seed=19,
                            verbose=False, return_info=True,
                            base_routes=(base['e1_routes'], base['e2_routes']),
                            base_info={'objective': base['objective']})
    evaluator.audit(info['state'])
    write(target / 'constructed_end_to_end.json', {'scope': 'synthetic_mechanism_not_empirical_benchmark',
          'base': base, 'e1_routes': x, 'e2_routes': y, 'info': info})
    rows = []
    for instance in ('Set2_100-5-1', 'Set2_100-5-2', 'Set2_200-10-2'):
        saved = json.loads((folder / instance / 'base.json').read_text())
        p, a, b, _ = load_problem(instance)
        base = prepare_baseline(p, a, b, p.fleet.e2_per_sat_count,
                                (saved['e1_routes'], saved['e2_routes']))
        failures, accepted = Counter(), Counter()
        original = Evaluator.audit

        def measured(self, state):
            recipients = defaultdict(set)
            for transfer in state['transfers']:
                recipients[transfer['satellite'], transfer['event'], transfer['sender_vehicle']].add(transfer['receiver_vehicle'])
            split = any(len(receivers) > 1 for receivers in recipients.values())
            label = 'with_split' if split else 'without_split'
            try:
                result = original(self, state)
            except ValueError as exc:
                failures[label + ': ' + str(exc)] += 1
                raise
            accepted[label] += 1
            return result

        with patch.object(Evaluator, 'audit', measured):
            x, y, info = solve_alns(
                p, a, b, p.fleet.e2_per_sat_count, max_time=5, max_iterations=1000000,
                seed=19, verbose=False, return_info=True, max_handoff_receivers=3,
                search_all_receivers=True, multi_donor_pickups=True,
                base_routes=(saved['e1_routes'], saved['e2_routes']), base_info=saved['info'])
        Evaluator(p, a, b, base).audit(info['state'])
        row = {'instance': instance, 'diagnostic_only': True, 'budget': 5, 'seed': 19,
               'objective': info['objective'], 'iterations': info['iterations'],
               'accepted_audit_states': dict(accepted), 'rejections': dict(failures)}
        rows.append(row)
        write(target / f'{instance}.json', {'e1_routes': x, 'e2_routes': y, 'info': info, 'diagnostic': row})
        write(target / 'rejections.json', rows)
        print(json.dumps(row, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
