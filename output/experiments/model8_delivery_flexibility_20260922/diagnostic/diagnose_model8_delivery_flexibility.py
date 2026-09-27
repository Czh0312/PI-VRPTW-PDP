"""Post-experiment diagnostics of the added delivery neighborhoods, not a benchmark."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import shutil
from unittest.mock import patch

from experiments.runs.run_model8_capacity_probe import ROOT, load_problem
from experiments.runs.run_model8_delivery_flexibility import DEFAULT_OUTPUT
from experiments.runs.run_model8_small_comparison import write
from models.routing.alns.variants.model_8 import solve_alns
from models.routing.alns.variants import model8_delivery_moves as moves
from models.routing.variants.model_8.baseline import prepare_baseline
from models.routing.variants.model_8.state import Evaluator


def main():
    folder = ROOT / DEFAULT_OUTPUT
    primary = json.loads((folder / 'summary.json').read_text())
    target = folder / 'diagnostic'
    target.mkdir(exist_ok=False)
    source = Path(__file__).resolve()
    shutil.copy2(source, target / source.name)
    write(target / 'manifest.json', {'budget': 5, 'seed': 19, 'allow_transfers': True,
          'delivery_policy': 'flexible', 'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
          'scope': 'post-experiment instrumented runs; excluded from paired comparison'})
    results = []
    original_move, original_audit, original_profile = moves.delivery_move, Evaluator.audit, Evaluator.profile
    for instance in primary['settings']['instances']:
        saved = json.loads((folder / instance / 'base.json').read_text())
        p, a, b, _ = load_problem(instance)
        base = prepare_baseline(p, a, b, p.fleet.e2_per_sat_count,
                                (saved['e1_routes'], saved['e2_routes']))
        active = [False]
        outcomes, audit_failures, load_failures = Counter(), Counter(), Counter()

        def measured_profile(self, *args, **kwargs):
            result = original_profile(self, *args, **kwargs)
            rows, valid, carried = result
            if active[0] and not valid:
                if not carried:
                    label = 'native_inventory_invalid'
                elif (rows[0]['volume'] > self.problem.e2_volume_cap + 1e-6 or
                      rows[0]['weight'] > self.problem.e2_weight_cap + 1e-6):
                    label = 'initial_delivery_overload'
                else:
                    label = 'later_load_overload'
                load_failures[label] += 1
            return result

        def measured_audit(self, *args, **kwargs):
            try:
                return original_audit(self, *args, **kwargs)
            except ValueError as exc:
                if active[0]:
                    audit_failures[str(exc)] += 1
                raise

        def signature(e, state):
            return [[n for n in r if n in e.tasks and e.tasks[n].kind == 'd']
                    for paths in state['e2_routes'].values() for r in paths]

        def measured_move(e, current, rng, name, *args, **kwargs):
            outcomes[name + ':attempts'] += 1
            active[0] = True
            try:
                candidate, meta = original_move(e, current, rng, name, *args, **kwargs)
            finally:
                active[0] = False
            if candidate is not None:
                outcomes[name + ':feasible_returned'] += 1
                if signature(e, candidate[0]) != signature(e, current):
                    outcomes[name + ':feasible_delivery_changed'] += 1
                    if candidate[1]['objective'] < args[1] - 1e-6:
                        outcomes[name + ':improving_delivery_changed'] += 1
            return candidate, meta

        with patch.object(moves, 'delivery_move', measured_move), \
             patch.object(Evaluator, 'audit', measured_audit), \
             patch.object(Evaluator, 'profile', measured_profile):
            x, y, info = solve_alns(p, a, b, p.fleet.e2_per_sat_count,
                                    base_routes=(saved['e1_routes'], saved['e2_routes']), base_info=saved['info'],
                                    delivery_policy='flexible', allow_transfers=True,
                                    max_time=5, max_iterations=1000000, seed=19,
                                    search_all_receivers=True, return_info=True, verbose=False)
        audit = Evaluator(p, a, b, base, delivery_policy='flexible').audit(info['state'])
        assert abs(audit['objective'] - info['objective']) < 1e-6
        row = {'instance': instance, 'objective': info['objective'], 'outcomes': dict(outcomes),
               'full_candidate_audit_failures': dict(audit_failures),
               'route_profile_failures_including_repeated_repair_checks': dict(load_failures)}
        results.append(row)
        write(target / f'{instance}.json', {'e1_routes': x, 'e2_routes': y, 'info': info, 'diagnostic': row})
        write(target / 'rejections.json', results)
        print(json.dumps(row, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
