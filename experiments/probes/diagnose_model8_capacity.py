# Historical M8 experiments: retain their original two-stage semantics.
"""Bounded audit-failure diagnosis on the unchanged M8 v2 search."""
from collections import Counter
import json
from pathlib import Path
from unittest.mock import patch

from experiments.runs.run_model8_capacity_probe import load_problem, dump
from models.routing.variants.model_8.two_stage_v2.alns import solve_alns
from models.routing.variants.model_8.two_stage_v2.state import Evaluator


def main():
    folder = Path('output/experiments/model8_capacity_20260922')
    rows = []
    for instance in ('Set2_100-10-1', 'Set2_200-10-1'):
        p, a, b, _ = load_problem(instance)
        base = json.loads((folder / instance / 'base.json').read_text())
        failures = Counter()
        original = Evaluator.audit
        def measured(self, state):
            try:
                return original(self, state)
            except ValueError as error:
                prefix = 'with_handoff: ' if state['transfers'] else 'no_handoff: '
                failures[prefix + str(error)] += 1
                raise
        with patch.object(Evaluator, 'audit', measured):
            _, _, info = solve_alns(p, a, b, base['effective_vehicle_count'],
                                    base_routes=(base['e1_routes'], base['e2_routes']),
                                    base_info=base['info'], max_time=5,
                                    max_iterations=1000000, seed=42,
                                    verbose=False, return_info=True)
        row = {'instance': instance, 'diagnostic_only': True, 'seed': 42,
               'budget': 5, 'iterations': info['iterations'],
               'capacity_blocked_candidates': info['capacity_blocked_candidates'],
               'feasible_relay_repairs': info['feasible_relay_repairs'],
               'objective': info['objective'], 'audit_rejections': dict(failures)}
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
    dump(folder / 'diagnostic.json', rows)


if __name__ == '__main__':
    main()
