"""Deterministic randomized calendar-domain nesting checks, retaining time limits."""
from dataclasses import replace
import argparse
import json
from pathlib import Path
import random

from models.routing.common.cycle.policy import Batch
from models.routing.common.cycle.calendar import CalendarCycleInstance
from models.routing.common.cycle.uniform import UniformCycleInstance
from models.routing.service import solve_routing
from tests.routing.test_model6_joint import joint_fixture


def run(output):
    rows = json.loads(output.read_text()) if output.exists() else []
    existing = {(r['seed'], r['model']): r for r in rows}
    checks = []
    for seed in range(24):
        rng = random.Random(seed)
        s = joint_fixture(pool_rate=rng.choice([0, 5, 30]), cleaning=rng.randrange(5), zero=seed % 3 == 0)
        t0 = 10 if seed % 2 else 0
        policy = replace(s.operating_policy, cycle_start=t0, cycle_end=t0+36,
            batches=tuple(Batch('b'+str(i+1), t0+12*i, t0+12*i+8, t0+12*(i+1)) for i in range(3)),
            e1_load_time=rng.randrange(2), e2_dispatch_prep_time=rng.randrange(2),
            e2_return_unload_time=rng.randrange(2), depot_return_unload_time=rng.randrange(2),
            e1_turnaround_time=rng.randrange(2), e2_turnaround_time=rng.randrange(2))
        orders = []
        for o in s.orders:
            d0 = t0+rng.randrange(12)
            d1 = min(t0+25, d0+rng.randrange(6, 19))
            p0 = d0+rng.randrange(4, 11)
            p1 = min(t0+34, p0+rng.randrange(5, 16))
            orders.append(replace(o, allowed_batch_ids=('b1', 'b2', 'b3'), delivery_window=(d0, d1), pickup_window=(p0, p1),
                                 release_time=t0+rng.choice([0, 0, 0, 10, 18]), service_time=rng.randrange(2), min_use_time=rng.randrange(3)))
        results = {}
        for number in (6, 7, 8):
            if (seed, number) in existing:
                result = existing[seed, number]['result']
            else:
                options = dict(batch_count_candidates=[1, 2, 3], pool_max=1 if seed % 5 == 0 else 2)
                problem = (UniformCycleInstance(s.network, orders, policy, dict(options, schedule_mode='uniform_cycle'), s.cost_profile.config)
                           if number == 6 else CalendarCycleInstance(s.network, orders, policy, options, s.cost_profile.config, model=number))
                result = solve_routing(model=number, method='exact', problem=problem, exact_time=3)
                info = result['info']
                row = dict(seed=seed, model=number, status=info['status'], cost=info['objective'],
                           optimal=info['proven_optimal'], infeasible=info['proven_infeasible'],
                           valid=result.get('audit', {}).get('valid', False), policy=policy.as_dict(),
                           orders=[o.__dict__ for o in orders], result=result)
                rows.append(row)
                output.write_text(json.dumps(rows, ensure_ascii=False, indent=2)+'\n')
            assert result['info']['objective'] is None or result['audit']['valid'], (seed, number)
            results[number] = result
        for left, right in ((6, 7), (7, 8)):
            a, b = results[left]['info'], results[right]['info']
            if a['objective'] is not None:
                assert not b['proven_infeasible'], (seed, left, right, 'lost feasible domain')
                if b['best_bound'] is not None:
                    assert b['best_bound'] <= a['objective']+1e-6, (seed, left, right, 'invalid expanded lower bound')
                if b['proven_optimal']:
                    assert b['objective'] <= a['objective']+1e-6, (seed, left, right, 'invalid optimum order')
            checks.append(dict(seed=seed, left=left, right=right,
                               both_resolved=all(r['info']['proven_optimal'] or r['info']['proven_infeasible']
                                                 for r in (results[left], results[right]))))
        print('micro', seed, 'checked', flush=True)
    output.with_name('random_micro_checks.json').write_text(json.dumps(checks, indent=2)+'\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    run(parser.parse_args().output)
