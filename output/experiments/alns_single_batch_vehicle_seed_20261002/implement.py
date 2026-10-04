"""Build a measured prototype from the archived audited-tail code, not an adoption."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PRIOR = ROOT / 'output/experiments/alns_single_batch_audited_tail_20261001'
PATH = ROOT / 'models/routing/solvers/alns/partitioned_assignment.py'
assert PATH.read_bytes() == (Path(__file__).resolve().parent/'before_source'/PATH.relative_to(ROOT)).read_bytes()
source = (PRIOR/'after_source'/PATH.relative_to(ROOT)).read_text()
source = source.replace('from .neighborhoods import clone_state, state_from_result',
    'from .neighborhoods import clone_state, state_from_result, _goods_ready, _route_score')
start = source.index('def _audited_tail_seed(')
end = source.index('\ndef solve_alns(', start)
replacement = '''def _pack_delivery_vehicles(problem, original, deadline, *, score_route=None):
    """Propose a real vehicle schedule, preserving every delivery sequence.

    Latest-departure bounds only rank routes. Local scores choose vehicles;
    the complete decoder/audit must still check supply, capacity and time.
    Interruption returns no partial state and never changes the input.
    """
    if problem.reusable or len(problem.operating_policy.batches) != 1:
        return None
    if time.perf_counter() >= deadline:
        return None
    if any(route['satellite'] not in problem.satellites or
           any(kind != 'd' or oid not in problem.order_map for oid,kind in route['tasks'])
           for route in original['e2']):
        return None
    state = clone_state(original)
    p, f = problem.operating_policy, problem.fleet
    goods, packed = _goods_ready(problem,state), []
    score_route = score_route or _route_score
    for site in problem.satellites:
        ready = [p.cycle_start]*f.e2_per_sat_count
        slots = [0]*f.e2_per_sat_count
        routes = [r for r in state['e2'] if r['satellite'] == site and r['tasks']]
        def latest_departure(route):
            latest = p.cycle_end-p.e2_turnaround_time-p.e2_return_unload_time
            previous = site
            for oid,kind in reversed(route['tasks']):
                order = problem.order_map[oid]
                latest -= problem.travel(order.customer,previous)+order.service_time
                latest = min(latest,order.delivery_window[1],
                             problem.batch_map[order.batch].cutoff-order.service_time)
                previous = order.customer
            return latest-problem.travel(site,previous)
        for route in sorted(routes,key=latest_departure):
            if time.perf_counter() >= deadline:
                return None
            nodes = [site]+[problem.order_map[oid].customer for oid,kind in route['tasks']]+[site]
            distance = sum(problem.distance(a,b) for a,b in zip(nodes,nodes[1:]))
            options = []
            for vehicle in range(f.e2_per_sat_count):
                if time.perf_counter() >= deadline:
                    return None
                if slots[vehicle] >= p.e2_max_trips_per_vehicle:
                    continue
                score,finish = score_route(problem,route,goods,ready[vehicle])
                options.append((score-distance,finish,vehicle))
            if not options:
                return None
            _,finish,vehicle = min(options)
            route['vehicle'] = vehicle
            ready[vehicle] = finish+p.e2_turnaround_time
            slots[vehicle] += 1
            packed.append(route)
    if time.perf_counter() >= deadline:
        return None
    state['e2'] = packed
    return state


def _audited_tail_seed(problem, current, incumbent, deadline, statistics):
    """Compare direct/projected sequences, also packing each onto real vehicles.

    Three geometric options, four representations each, one common deadline.
    Only fresh complete fixed and lifted audits may supply an improving seed.
    """
    statistics.update(geometric_options=0,decoded_candidates=0,audited_candidates=0,
                      improving_candidates=0,timed_out=0,pack_calls=0,pack_completed=0,
                      pack_timeouts=0,lifted_audits=0)
    if problem.reusable or len(problem.operating_policy.batches) != 1:
        return None
    if time.perf_counter() >= deadline:
        statistics['timed_out'] = 1
        return None
    original = state_from_result(problem,incumbent)
    options = _tail_options(problem,current,original,deadline)
    if options is None:
        statistics['timed_out'] = 1
        return None
    statistics['geometric_options'] = len(options)
    best,best_cost = None,incumbent['info']['objective']
    for option in options[:3]:
        if time.perf_counter() >= deadline:
            statistics['timed_out'] = 1
            break
        assignment,fixed,direct = _tail_state(problem,current,original,option)
        context = SingleBatchCache(fixed)
        for representation in ('direct','repaired'):
            if time.perf_counter() >= deadline:
                statistics['timed_out'] = 1
                break
            state = (direct if representation == 'direct' else
                     reassigned_state(fixed,incumbent,deadline,scorer=context.route_scorer))
            if state is None:
                continue
            for packed in (False,True):
                if time.perf_counter() >= deadline:
                    statistics['timed_out'] = 1
                    break
                mode = representation+('_packed' if packed else '')
                if packed:
                    statistics['pack_calls'] += 1
                    proposal = _pack_delivery_vehicles(fixed,state,deadline,score_route=context.score_route)
                    if proposal is None:
                        statistics['pack_timeouts'] += int(time.perf_counter() >= deadline)
                        continue
                    statistics['pack_completed'] += 1
                else:
                    proposal = state
                score,result,issues = context(proposal)
                statistics['decoded_candidates'] += 1
                if issues or score >= best_cost-1e-9:
                    continue
                audit = audit_solution(fixed,result)
                statistics['audited_candidates'] += 1
                if not audit['valid'] or not audit.get('periodic_replay',{}).get('valid'):
                    continue
                lifted = lift_result(problem,result,assignment)
                statistics['lifted_audits'] += 1
                if not lifted['audit']['valid'] or not lifted['audit'].get('periodic_replay',{}).get('valid'):
                    continue
                statistics['improving_candidates'] += 1
                best_cost = score
                result['audit'] = audit
                best = dict(assignment=assignment,fixed=fixed,context=context,result=result,
                            geometry_delta=option[0],gain=incumbent['info']['objective']-score,mode=mode)
    return best

'''
source = source[:start]+replacement+source[end:]
source = source.replace('seed_gains = []','seed_gains, seed_modes, packed_starts = [], [], 0')
source = source.replace("'improving_candidates','timed_out')}",
    "'improving_candidates','timed_out','pack_calls','pack_completed','pack_timeouts','lifted_audits')}")
source = source.replace("seed_gains.append(route_seed['gain'])", "seed_gains.append(route_seed['gain'])\n            seed_modes.append(route_seed['mode'])\n            packed_starts += int(route_seed['mode'].endswith('_packed'))")
source = source.replace('assignment_route_seed_gains=seed_gains,',
    "assignment_route_seed_gains=seed_gains, assignment_route_seed_modes=seed_modes,\n                          assignment_route_seed_packed_starts=packed_starts,\n                          assignment_route_seed_pack_calls=seed_totals['pack_calls'],\n                          assignment_route_seed_pack_completed=seed_totals['pack_completed'],\n                          assignment_route_seed_pack_timeouts=seed_totals['pack_timeouts'],\n                          assignment_route_seed_lifted_audits=seed_totals['lifted_audits'],")
PATH.write_text(source)
tests = (PRIOR/'rejected_tests/test_single_batch_audited_tail.py').read_text()
tests = tests.replace('_tail_route_cost, solve_alns)', '_tail_route_cost, _pack_delivery_vehicles, solve_alns)')
tests = tests.replace("statistics['decoded_candidates'] == 2", "statistics['decoded_candidates'] == 4")
tests = tests.replace("stats['decoded_candidates'] == 2", "stats['decoded_candidates'] == 4")
tests = tests.replace("stats['decoded_candidates'] == 1", "stats['decoded_candidates'] == 2")
tests = tests.replace('test_at_most_three_geometric_options_and_six_full_decodes',
                      'test_at_most_three_geometric_options_and_twelve_full_decodes')
tests = tests.replace("stats['decoded_candidates'] == 6", "stats['decoded_candidates'] == 12")
(ROOT/'tests/routing/test_single_batch_vehicle_seed.py').write_text(tests)
print('Prototype source and inherited correctness tests written')
