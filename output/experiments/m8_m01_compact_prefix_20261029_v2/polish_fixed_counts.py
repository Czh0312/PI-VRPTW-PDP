"""Same M01, seed 42: two bounded upper-bound searches during the exact run.

Only independently audited physical plans are accepted. These searches do not
change the running exact model, its lower bound, or its optimality status.
"""
from datetime import datetime, timezone
import time

from study import HERE, CASE, read, save, load_instance, load_routing_case
from models.routing.model_8.alns.search import solve_variable_count
from models.common.cycle.audit import audit_solution
from models.common.cycle.calendar import attach_calendar_decisions


if __name__ == '__main__':
    begun = time.perf_counter()
    outer = datetime.fromisoformat(read(HERE/'started.json')['at'])
    remaining = 1185-(datetime.now(timezone.utc)-outer).total_seconds()
    deadline = begun+max(0., min(240., remaining))
    p = load_instance(*load_routing_case(CASE, use_pool_config=False))
    best = read(HERE/'initial_result.json')
    best_audit = audit_solution(p, best)
    assert best_audit['valid'] and best_audit['periodic_replay']['valid']
    rows = []
    for k in (2, 3):
        budget = min(120., max(0., deadline-time.perf_counter()))
        if budget < 1:
            break
        started = time.perf_counter()
        initial = best if best['cycle_batch_count'] == k else None
        result = solve_variable_count(p.for_count(k), max_time=budget,
            max_iterations=1000000000, seed=42, initial_result=initial)
        row = dict(K=k, seed=42, budget_seconds=budget,
                   seconds=time.perf_counter()-started,
                   warm_start_supplied=initial is not None,
                   solver_status=result['info']['status'],
                   iterations=result['info'].get('iterations'),
                   audit_valid=False)
        if result.get('audit', {}).get('valid'):
            boundaries = [b['start'] for b in result['batches']]+[p.operating_policy.cycle_end]
            attach_calendar_decisions(p, result, k, boundaries)
            audit = audit_solution(p, result)
            row.update(audit_valid=audit['valid'],
                       periodic_replay_valid=audit.get('periodic_replay', {}).get('valid', False),
                       audited_cost=audit.get('objective'))
            if row['audit_valid'] and row['periodic_replay_valid']:
                save(f'heuristic_K{k}_result.json', result)
                save(f'heuristic_K{k}_audit.json', audit)
                if audit['objective'] < best_audit['objective']:
                    best, best_audit = result, audit
        rows.append(row)
        save('heuristic_progress.json', rows)
        print(row, flush=True)
    save('heuristic_best_result.json', best)
    save('heuristic_summary.json', dict(kind='AUDITED_UPPER_BOUND_ONLY',
        case='M01', seed=42, concurrent_with_complete_model=True,
        searches=rows, seconds=time.perf_counter()-begun,
        audited_cost=best_audit['objective'], audit_valid=best_audit['valid'],
        periodic_replay_valid=best_audit['periodic_replay']['valid'],
        used_for_global_lower_bound=False, proves_optimality=False))
