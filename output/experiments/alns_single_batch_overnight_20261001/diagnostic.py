"""Descriptive replay: can real E1 moves / E2 vehicle packing rescue suffixes?

This reuses already-observed incumbents and is not a new quality comparison.
No local feasibility estimate or geometry delta is reported as an improvement.
Every accepted result requires complete fixed and lifted independent audits.
"""
from pathlib import Path
import importlib.util
import collections
import hashlib
import json
import os
import sys
import time

OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(OUT / 'before_source'))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.common.cycle.assignment import lift_result
from models.routing.common.cycle.audit import audit_solution
from models.routing.solvers.alns.evaluator import evaluate
from models.routing.solvers.alns.neighborhoods import clone_state, state_from_result, _goods_ready, _route_score
from models.routing.solvers.alns.single_batch_descent import task_distance

spec = importlib.util.spec_from_file_location('models.routing.solvers.alns._diagnostic_tail_helpers',
                                             OUT / 'archived_tail_helpers.py')
helpers = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helpers)

def e1_variants(original):
    """All single-site insertions and two-site swaps, without distance pruning."""
    seen = set()
    for batch, rows in original['deliveries'].items():
        for a, row in enumerate(rows):
            for pos, site in enumerate(row):
                shortened = [list(r) for r in rows]
                shortened[a].pop(pos)
                for b, target in enumerate(shortened):
                    for dest in range(len(target) + 1):
                        new = [list(r) for r in shortened]
                        new[b].insert(dest, site)
                        key = tuple(map(tuple, new))
                        if key in seen or key == tuple(map(tuple, rows)):
                            continue
                        seen.add(key)
                        state = clone_state(original)
                        state['deliveries'][batch] = new
                        yield state
                for b in range(a, len(rows)):
                    for other_pos in range(len(rows[b])):
                        if a == b and other_pos <= pos:
                            continue
                        new = [list(r) for r in rows]
                        new[a][pos], new[b][other_pos] = new[b][other_pos], site
                        key = tuple(map(tuple, new))
                        if key in seen:
                            continue
                        seen.add(key)
                        state = clone_state(original)
                        state['deliveries'][batch] = new
                        yield state

def pack_vehicles(problem, original):
    """Preserve every sequence; greedily assign routes to real available vehicles."""
    state = clone_state(original)
    p, f = problem.operating_policy, problem.fleet
    goods = _goods_ready(problem, state)
    packed = []
    for site in problem.satellites:
        ready = [p.cycle_start] * f.e2_per_sat_count
        slots = [0] * f.e2_per_sat_count
        routes = [r for r in state['e2'] if r['satellite'] == site]
        def latest_departure(route):
            latest, previous = p.cycle_end-p.e2_turnaround_time-p.e2_return_unload_time, site
            for oid, kind in reversed(route['tasks']):
                order = problem.order_map[oid]
                latest -= problem.travel(order.customer, previous) + order.service_time
                latest = min(latest, order.delivery_window[1],
                             problem.batch_map[order.batch].cutoff-order.service_time)
                previous = order.customer
            return latest - problem.travel(site, previous)
        for route in sorted(routes, key=latest_departure):
            distance = task_distance(problem, site, route['tasks'])
            options = []
            for vehicle in range(f.e2_per_sat_count):
                if slots[vehicle] >= p.e2_max_trips_per_vehicle:
                    continue
                score, finish = _route_score(problem, route, goods, ready[vehicle])
                options.append((score-distance, finish, vehicle))
            if not options:
                return None
            _, finish, vehicle = min(options)
            route['vehicle'] = vehicle
            ready[vehicle] = finish+p.e2_turnaround_time
            slots[vehicle] += 1
            packed.append(route)
    state['e2'] = packed
    return state

def run():
    jobs = json.loads((OUT / 'diagnostic_jobs.json').read_text())['jobs']
    rows = []
    for index, job in enumerate(jobs):
        config, fleet = load_routing_case(job['case'])
        problem = load_instance({**config, 'model': 2}, fleet)
        incumbent = json.loads((OUT / 'baseline_results' / ('%03d.json' % index)).read_text())['result']
        prior_audit = audit_solution(problem, incumbent)
        assert prior_audit['valid'] and prior_audit['periodic_replay']['valid']
        started = time.perf_counter()
        deadline = started+30
        original = state_from_result(problem, incumbent)
        assignment = incumbent['assignment']
        options = helpers._tail_options(problem, assignment, original, deadline)
        assert options is not None, 'Incomplete geometry search; do not silently treat as zero'
        row = dict(index=index, family=job['family'], prior_seed=job['seed'], prior_budget=job['budget'],
                   incumbent_objective=incumbent['info']['objective'], geometric_options=len(options),
                   direct_issues={}, checks=collections.Counter(), valid=collections.Counter(),
                   improvements=[], timed_out=False, load_average=os.getloadavg())
        direct_issues = collections.Counter()
        for rank, option in enumerate(options[:3]):
            moved, fixed, direct = helpers._tail_state(problem, assignment, original, option)
            # Baseline direct proposal, then vehicle packing; all E1 alternatives
            # are real resource schedules and are freshly decoded in both modes.
            variants = [('original_e1', direct)]
            variants.extend(('changed_e1', state) for state in e1_variants(direct))
            for e1_mode, variant in variants:
                for vehicle_mode in ('original_e2', 'packed_e2'):
                    if time.perf_counter() >= deadline:
                        row['timed_out'] = True
                        break
                    state = variant if vehicle_mode == 'original_e2' else pack_vehicles(fixed, variant)
                    mode = e1_mode+'/'+vehicle_mode
                    if state is None:
                        continue
                    score, result, issues = evaluate(fixed, state)
                    row['checks'][mode] += 1
                    if e1_mode == 'original_e1' and vehicle_mode == 'original_e2':
                        direct_issues.update(issues)
                    if issues:
                        continue
                    audit = audit_solution(fixed, result)
                    if not audit['valid'] or not audit['periodic_replay']['valid']:
                        continue
                    lifted = lift_result(problem, result, moved)
                    if not lifted['audit']['valid'] or not lifted['audit']['periodic_replay']['valid']:
                        continue
                    row['valid'][mode] += 1
                    if score < incumbent['info']['objective']-1e-9:
                        row['improvements'].append(dict(option_rank=rank, mode=mode, objective=score,
                          gain=incumbent['info']['objective']-score, geometry_delta=option[0]))
                if row['timed_out']:
                    break
            if row['timed_out']:
                break
        row['direct_issues'] = dict(direct_issues)
        row['wall_seconds'] = time.perf_counter()-started
        rows.append(row)
        (OUT / 'diagnostic_results.json').write_text(json.dumps(rows, indent=2))
        print(index, job['family'], 'options', len(options), 'improvements', len(row['improvements']),
              'checks', sum(row['checks'].values()), 'timeout', row['timed_out'], flush=True)
    checks = {}
    for base, manifest in [('before_source', 'before_source_manifest.json'),
                           ('benchmark_inputs', 'input_hashes.json'),
                           ('baseline_results', 'baseline_result_hashes.json')]:
        current = {str(p.relative_to(OUT/base)): hashlib.sha256(p.read_bytes()).hexdigest()
                   for p in sorted((OUT/base).rglob('*')) if p.is_file() and '__pycache__' not in p.parts}
        checks[base] = current == json.loads((OUT/manifest).read_text())
    (OUT / 'diagnostic_verification.json').write_text(json.dumps(checks, indent=2))
    assert all(checks.values())
    checkpoint = json.loads((OUT / 'checkpoint.json').read_text())
    checkpoint.update(status='diagnosis_complete', active_process=None,
                      next_action='Review descriptive replay before preregistering a new candidate')
    (OUT / 'checkpoint.json').write_text(json.dumps(checkpoint, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    run()
