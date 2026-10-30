"""Isolated necessary occupancy-bound prototype; no changes to running ablation."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from experiments.runs import probe_model8_physical_bounds as trial


def occupancy_bound(problem):
    """Each order holds its boxes throughout [latest_loading, earliest_clean).

    Use shortest-path relaxations of possibly nonmetric travel matrices.
    Ignore capacity, waiting, other orders, cutoffs and vehicle competition;
    these relaxations can only move loading later and clean release earlier.
    Every feasible schedule holds distinct containers for overlapping
    mandatory intervals, irrespective of batch count or order assignment.
    """
    policy = problem.operating_policy
    outward = problem.travel_bounds['outward']
    inward = problem.travel_bounds['inward']
    rows = []
    for order in problem.orders:
        site = order.satellite
        supply = (policy.e1_load_time + outward[problem.depot][site] + problem.service(site)
                  + policy.e2_dispatch_prep_time + outward[site][order.customer])
        latest_loading = order.delivery_window[1] - supply
        first_delivery = max(order.delivery_window[0], order.release_time+supply,
                             policy.cycle_start+supply)
        first_pickup = max(order.pickup_window[0], first_delivery+order.service_time+order.min_use_time)
        earliest_clean = (first_pickup + order.service_time + inward[site][order.customer]
                          + policy.e2_return_unload_time + problem.service(site)
                          + outward[site][problem.depot] + policy.depot_return_unload_time
                          + policy.cleaning_duration)
        rows.append(dict(order=order.id, count=order.count,
                         latest_loading=latest_loading, earliest_clean=earliest_clean))
    events = {}
    for row in rows:
        lo, hi = row['latest_loading'], row['earliest_clean']
        if lo < hi:
            events[lo] = events.get(lo, 0) + row['count']
            events[hi] = events.get(hi, 0) - row['count']
    active, bound, witness = 0, 0, None
    for tick, delta in sorted(events.items()):
        active += delta
        if active > bound:
            bound, witness = active, tick
    return dict(pool_lower_bound=bound, witness_tick=witness, intervals=rows,
                proof='mandatory_half_open_occupancy_intervals_with_shortest_path_relaxations')


def forced_first_batch(problem, bounds=None):
    """A later-batch order cannot supply the first batch's mandatory recovery.

    If i is loaded after batch 1, some different order j must return to the
    depot no later than the first batch end (and hence before i's loading).
    A lower bound for every such j exceeding i's latest loading is a
    contradiction. This proof uses no incumbent, pool choice or fixed K.
    """
    bounds = bounds or occupancy_bound(problem)
    rows = bounds['intervals']
    forced = []
    for row in rows:
        alternatives = [r['earliest_clean'] - problem.operating_policy.cleaning_duration
                        for r in rows if r['order'] != row['order']]
        if alternatives and min(alternatives) > row['latest_loading']:
            forced.append(dict(order=row['order'], latest_loading=row['latest_loading'],
                               earliest_other_order_depot_return=min(alternatives)))
    return forced


original_builder = trial.build_model


def build_with_occupancy(problem, *, strengthening='physical_occupancy', **kwargs):
    if strengthening not in ('physical_occupancy', 'physical_time'):
        raise ValueError(strengthening)
    model, ctx = original_builder(problem, strengthening='physical', **kwargs)
    try:
        bound = occupancy_bound(problem)
        ctx['pool'].LB = max(ctx['pool'].LB, bound['pool_lower_bound'])
        ctx['physical_bounds'].update(profile=strengthening, occupancy=bound)
        if strengthening == 'physical_time':
            forced = forced_first_batch(problem, bound)
            first = problem.operating_policy.batches[0].id
            for row in forced:
                variable = ctx['assignments'].get((row['order'], first))
                if variable is None:
                    model.addConstr(prototype_impossible_expression() == 1,
                                    name=f'physical_no_possible_first_batch_{row["order"]}')
                else:
                    model.addConstr(variable == 1, name=f'physical_time_first_{row["order"]}')
            ctx['physical_bounds']['forced_first_batch'] = forced
        model.update()
        return model, ctx
    except BaseException:
        model.dispose()
        raise


def prototype_impossible_expression():
    return trial.gp.LinExpr()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--seconds', type=float, default=300)
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--formulation', default='compact', choices=('compact', 'expanded'))
    parser.add_argument('--time-bounds', action='store_true')
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    problem = trial.load_instance(*trial.load_routing_case(args.case, use_pool_config=False))
    hashes = trial.source_hashes()
    prototype_hash = trial.sha(Path(__file__))
    profile = 'physical_time' if args.time_bounds else 'physical_occupancy'
    trial.write(output/'manifest.json', dict(case=str(args.case.resolve()), case_sha256=trial.sha(args.case),
                K=list(problem.batch_count_candidates), source_hashes=hashes,
                prototype_sha256=prototype_hash, profile=profile,
                seconds=args.seconds, threads=1, seed=args.seed, formulation=args.formulation,
                time_budget_scope='optimization_only', external_starts=False))
    trial.build_model = build_with_occupancy
    row = trial.run(args, problem, profile, output)
    assert trial.source_hashes() == hashes
    assert trial.sha(Path(__file__)) == prototype_hash
    trial.write(output/'comparison.json', [row])


if __name__ == '__main__':
    main()
