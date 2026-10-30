"""Offline scoring after every cold-start worker has finished; no route references."""
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import statistics

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def metrics(rows):
    valid = [r for r in rows if r['valid']]
    gaps = [r['gap_percent'] for r in valid]
    return dict(cases=len(rows), feasible=len(valid), infeasible_proven=sum(r['proven_infeasible'] for r in rows),
        native_optimal_proofs=sum(r['proven_optimal'] for r in rows),
        known_optimum_hits=sum(r['known_optimum_hit'] for r in rows),
        within_one_percent=sum(r['gap_percent'] <= 1.+1e-9 for r in valid),
        mean_gap_all_cases_percent=statistics.mean(gaps) if len(valid) == len(rows) else None,
        mean_gap_feasible_cases_percent=statistics.mean(gaps) if gaps else None,
        worst_gap_feasible_cases_percent=max(gaps) if gaps else None,
        mean_wall_seconds=statistics.mean(r['solver_wall_seconds'] for r in rows),
        wall_seconds_range=[min(r['solver_wall_seconds'] for r in rows), max(r['solver_wall_seconds'] for r in rows)],
        mean_cpu_seconds=statistics.mean(r['cpu_seconds'] for r in rows),
        mean_process_wall_seconds=statistics.mean(r['worker_process_wall_seconds'] for r in rows),
        statuses=dict(Counter(r['status'] for r in rows)))


def main():
    plan = read(HERE/'plan.json')
    assert read(HERE/'completion.json')['jobs'] == len(plan['jobs']) == 36
    assert sha(HERE/'bench.py') == plan['driver_sha256']
    assert {p.relative_to(HERE/'source').as_posix(): sha(p) for p in (HERE/'source').rglob('*.py')} == plan['source_hashes']
    assert all(sha(Path(p)) == digest for p,digest in plan['input_hashes'].items())
    references, reference_hashes = {}, {}
    # Only scalar optimum costs and proof metadata are read. Never open a historical route.
    for case in plan['cases']:
        path = ROOT/f'output/experiments/m8_all_small_rerun_20261029_v1/runs/{case}/s42/summary.json'
        row = read(path)
        assert row['proven_optimal'] and abs(row['global_lower_bound']-row['audited_upper_bound']) <= 1e-6
        references[case] = row['audited_upper_bound']
        reference_hashes[path.relative_to(ROOT).as_posix()] = sha(path)
    rows = []
    for job in plan['jobs']:
        folder = HERE/'runs'/f"b{job['seconds']:02}"/job['case']/job['method']
        row, result = read(folder/'summary.json'), read(folder/'result.json')
        audit, events = read(folder/'audit.json'), read(folder/'events.json')
        assert row['seed'] == 42 and row['budget_seconds'] == job['seconds']
        assert row['original_K'] == list(range(1,8))
        assert not any(row[k] for k in ('external_initial_solution','historical_data_read','exact_reference_used','read_guard_blocked_count'))
        for path in row['input_read_paths']:
            assert Path(path).suffix in ('.json','.csv') and '/output/' not in path.replace('\\','/')
        if row['valid']:
            assert audit['valid'] and audit['periodic_replay']['valid']
            assert math.isclose(audit['objective'],row['objective'],abs_tol=1e-6)
        else:
            assert row['objective'] is None and not row['proven_optimal']
        if row['method'] == 'exact':
            assert not result['info'].get('mip_start_supplied',False)
            assert all(call['NumStart'] == 0 and call['threads'] == 1 and call['seed'] == 42 for call in row['optimize_calls'])
            options = result['info']['solver_config']['formulation_options']
            assert options['preprocessing'] and options['vehicle_symmetry'] and options['strengthening'] == 'physical_time'
            if row['proven_optimal']:
                assert result['info']['complete_count_domain']
        else:
            assert result['info']['initial_incumbent_cost'] is None and not result['info']['proven_optimal']
        reference = references[row['case']]
        assert not row['proven_infeasible'], ('infeasibility contradicts independently certified feasible cost',row)
        assert row['objective'] is None or row['objective'] >= reference-1e-5
        if row['best_bound'] is not None:
            assert row['best_bound'] <= reference+1e-5, ('bound exceeds known feasible cost',row)
        if row['proven_optimal']:
            assert abs(row['objective']-reference) <= 1e-5, ('inconsistent optimum',row)
        row.update(reference_optimum=reference,
            gap_percent=100.*(row['objective']/reference-1.) if row['valid'] else None,
            known_optimum_hit=bool(row['valid'] and abs(row['objective']-reference) <= 1e-6))
        known_hits = [e['elapsed'] for e in events if abs(e['objective']-reference) <= 1e-6]
        row['recorded_time_to_known_optimum_seconds'] = min(known_hits) if known_hits else None
        by_deadline = [e['objective'] for e in events if e['elapsed'] <= row['budget_seconds']]
        row['recorded_cost_by_strict_deadline'] = min(by_deadline) if by_deadline else None
        rows.append(row)
    budgets = {}
    for seconds in plan['budgets_seconds']:
        subset = [r for r in rows if r['budget_seconds'] == seconds]
        summary = {m:metrics([r for r in subset if r['method'] == m]) for m in ('alns','exact')}
        paired = []
        for case in plan['cases']:
            a = next(r for r in subset if r['case'] == case and r['method'] == 'alns')
            e = next(r for r in subset if r['case'] == case and r['method'] == 'exact')
            common = a['valid'] and e['valid']
            winner = ('alns_only_feasible' if a['valid'] and not e['valid'] else
                      'exact_only_feasible' if e['valid'] and not a['valid'] else
                      'neither_feasible' if not common else
                      'tie' if abs(a['objective']-e['objective']) <= 1e-6 else
                      'alns_lower_cost' if a['objective'] < e['objective'] else 'exact_lower_cost')
            paired.append(dict(case=case, reference=references[case], alns_cost=a['objective'], exact_cost=e['objective'],
                alns_gap_percent=a['gap_percent'], exact_gap_percent=e['gap_percent'],
                alns_status=a['status'], exact_status=e['status'], exact_proven_optimal=e['proven_optimal'],
                alns_wall_seconds=a['solver_wall_seconds'],exact_wall_seconds=e['solver_wall_seconds'], outcome=winner))
        budgets[str(seconds)] = dict(metrics=summary, pairs=paired,
            outcomes=dict(Counter(p['outcome'] for p in paired)),
            mean_gap_common_feasible_cases_percent={m:statistics.mean(p[m+'_gap_percent'] for p in paired
                if p['alns_cost'] is not None and p['exact_cost'] is not None) for m in ('alns','exact')}
                if any(p['alns_cost'] is not None and p['exact_cost'] is not None for p in paired) else None)
    result = dict(root_seeds=[42], replicates=1, cold_start=True, historical_solutions_read=False,
        references_used_only_after_all_search_for_scalar_scoring=True, reference_hashes=reference_hashes,
        budgets=budgets, rows=rows)
    save(HERE/'analysis.json',result)
    for seconds, data in budgets.items():
        print(json.dumps(dict(seconds=seconds, metrics=data['metrics'],outcomes=data['outcomes']),ensure_ascii=False))


if __name__ == '__main__':
    main()
