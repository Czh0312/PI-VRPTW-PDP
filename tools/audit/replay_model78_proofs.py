"""Replay archived certificates through final aggregation, without a new search."""
from copy import deepcopy
import json
from pathlib import Path
import sys


def main(root):
    sys.path.insert(0, str(root/'final/source'))
    from importlib import import_module
    source = root/'final/source'
    independent = (source/'models/routing/model_7').is_dir()
    method_first = (source/'models/routing/exact').is_dir()
    common = 'models.common.cycle' if (source/'models/common').is_dir() else 'models.routing.common.cycle'
    load_instance = import_module(common+'.instance').load_instance
    joint = import_module(common+'.joint')
    joint_empty_result, STRATEGY = joint.joint_empty_result, joint.STRATEGY
    records = []
    for number in (7, 8):
        if independent:
            folder = source/f'models/routing/model_{number}/exact'
            current = (folder/'enhanced_solver.py').is_file()
            organized = (folder/'search').is_dir()
            entry = 'solver' if current or organized else 'calendar_search'
            child = 'enhanced_solver' if current else ('search.fixed_count' if organized else 'joint_solver')
            solve_counts = import_module(f'models.routing.model_{number}.exact.{entry}').solve_counts
            solver = import_module(f'models.routing.model_{number}.exact.{child}')
        else:
            search = 'models.routing.exact.common.calendar_search' if method_first else 'models.routing.variants.calendar_search'
            child = 'models.routing.exact.model_6.solver' if method_first else 'models.routing.variants.model_6.exact.solver'
            solve_counts = import_module(search).solve_counts
            solver = import_module(child)
        name = 'zhou_Set1_7-2-1__repeat_orders'
        origin = root/f'diagnostic_long_budget/candidate/results/{name}__m{number}_exact_42.json'
        archived = json.loads(origin.read_text())['result']
        payload = json.loads((root/f'cases/{name}.json').read_text())
        config = payload['config']
        config['model'] = number
        config['joint_batch_options']['schedule_mode'] = {7: 'uniform_independent_cutoffs', 8: 'flexible_cycle'}[number]
        problem = load_instance(config, payload['fleet'])
        rows = {row['batch_count']: row for row in archived['count_search']}

        def archived_certificate(child, **kwargs):
            count = len(child.operating_policy.batches)
            row = rows[count]
            if count == archived['cycle_batch_count']:
                candidate = deepcopy(archived)
                candidate.update(model=6, strategy=STRATEGY)
                candidate['info'].update(status=row['status'], proven_optimal=row['proven_optimal'],
                                         proven_infeasible=row['proven_infeasible'], best_bound=row['best_bound'])
            else:
                # Other counts' full incumbents were not exported. Their
                # lower-bound certificates suffice to check global dominance;
                # never fabricate a physical solution for them.
                candidate = joint_empty_result(child, 'exact')
                candidate['info'].update(status=row['status'], proven_optimal=False,
                                         proven_infeasible=row['proven_infeasible'], best_bound=row['best_bound'])
            return candidate

        original_solver = solver.solve_exact
        solver.solve_exact = archived_certificate
        try:
            result = solve_counts(problem, method='exact', budget=5, seed=42)
        finally:
            solver.solve_exact = original_solver
        assert result['audit']['valid']
        assert result['info']['objective'] == archived['info']['objective']
        assert result['info']['proven_optimal'] == (number == 7)
        records.append(dict(model=number, origin=str(origin), original_status=archived['info']['status'],
                            result=result, certificate_only_replay=True,
                            note='Uses archived solver lower bounds and the audited selected incumbent; no new MILP search or performance measurement.'))
    destination = root/'final/proof_certificate_replay.json'
    assert not destination.exists()
    destination.write_text(json.dumps(records, ensure_ascii=False, indent=2)+'\n')
    print([(r['model'], r['original_status'], r['result']['info']['status']) for r in records])


if __name__ == '__main__':
    main(Path(sys.argv[1]).resolve())
