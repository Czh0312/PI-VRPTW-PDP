"""Input-only M3 route-partition lower bounds; not a complete scheduling solver.

Reuse M8's exhaustive relaxed physical trip enumerator, retaining the minimum
distance for every possible task set. Every original M3 solution maps into this
partition master. Supply, return-cutoff and vehicle timing are relaxed; only a
separately audited original plan can turn a matching lower bound into a proof.
"""
from __future__ import annotations

import hashlib
import itertools
import json
import math
from pathlib import Path
import sys
import time

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
sys.path.insert(0, str(ROOT))

from tools.configuration.loader import load_routing_case
from models.common.cycle import load_instance
from models.common.cycle.audit import audit_solution
from experiments.runs.research_model8_shared_relaxation import (
    enumerate_trip_library, shortest_distance_closure)


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def save(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2,
                                    allow_nan=False), encoding='utf-8')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run(case, seed=42):
    import gurobipy as gp

    target = OUT / f'{case}_route_partition_s{seed}'
    target.mkdir()
    started = time.perf_counter()
    case_path = ROOT / f'data/formal_benchmark27_uniform_7_20_50_v1/model_3/small/{case}/case.json'
    cfg, fleet = load_routing_case(case_path, use_pool_config=False)
    p = load_instance(cfg, fleet)
    assert p.model_number == 3 and p.cost_objective
    assert all(o.count == 1 for o in p.orders) and len(p.orders) <= 9
    rates = p.cost_profile.rates
    assert all(v >= 0 for v in rates.values() if isinstance(v, (int, float)))
    for key in ('e1_work_cost_per_minute', 'e2_work_cost_per_minute',
                'e1_vehicle_cost_per_cycle', 'e2_vehicle_cost_per_cycle',
                'e1_trip_cost', 'e2_trip_cost'):
        assert rates[key] == 0
    enum_start = time.perf_counter()
    library, prefixes = enumerate_trip_library(p)
    enumeration_seconds = time.perf_counter() - enum_start
    d = shortest_distance_closure(p)
    active_sites = sorted({o.satellite for o in p.orders})
    e1_lower = min(sum(d[a, b] for a, b in zip((p.depot,) + seq, seq + (p.depot,)))
                   for seq in itertools.permutations(active_sites))
    m = gp.Model('diagnostic_M3_relaxed_route_partition')
    m.Params.OutputFlag = 0
    m.Params.Threads = 1
    m.Params.Seed = seed
    m.Params.MIPGap = 0
    m.Params.MIPGapAbs = 0
    m.Params.TimeLimit = 10
    m.Params.LogFile = (target.relative_to(ROOT) / 'gurobi.log').as_posix()
    try:
        x = m.addVars(len(library), vtype=gp.GRB.BINARY, name='trip')
        for task in range(2 * len(p.orders)):
            m.addConstr(gp.quicksum(x[c] for c, col in enumerate(library)
                                   if col['mask'] & (1 << task)) == 1)
        constant = (rates['container_pool_cost_per_period'] * p.operating_policy.container_pool_size
                    + sum(p.cost_profile.constant_cost(p.total).values()))
        m.setObjective(constant + rates['distance_scale_to_km'] * (
            rates['e1_distance_cost_per_km'] * e1_lower
            + rates['e2_distance_cost_per_km'] * gp.quicksum(
                x[c] * col['distance'] for c, col in enumerate(library))), gp.GRB.MINIMIZE)
        m.update()
        assert m.NumStart == 0
        m.optimize()
        lower = float(m.ObjBound) if math.isfinite(m.ObjBound) else None
        # Only after the bound solve, load the original-domain witness.
        path = (ROOT / 'output/experiments/m3_s01_matched_cost_exact_20261024/expanded_result.json'
                if case == 'S01' else ROOT / (
                    'output/experiments/staged_comparison_27_seed42_20261024_v1/runs/'
                    f'A_{case}_m3_cost_exact_300s_s42/attempt_001/result.json'))
        witness = read(path)
        audit = audit_solution(p, witness)
        assert audit['valid'] and audit['periodic_replay']['valid']
        ids = {o.id: i for i, o in enumerate(p.orders)}
        by_mask = {col['mask']: col for col in library}
        projection = []
        for trip in witness['e2_trips']:
            mask = sum(1 << (ids[e['order_id']] + (len(ids) if e['kind'] == 'p' else 0))
                       for e in trip['events'])
            assert mask in by_mask
            sites = [trip['satellite_id']] + [p.order_map[e['order_id']].customer for e in trip['events']] + [trip['satellite_id']]
            distance = sum(p.distance(a, b) for a, b in zip(sites, sites[1:]))
            assert by_mask[mask]['distance'] <= distance + 1e-8
            projection.append(dict(mask=mask, original_distance=distance,
                                   column_distance=by_mask[mask]['distance']))
        upper = audit['objective']
        assert lower is None or lower <= upper + 1e-6
        row = dict(case=case, seed=seed,
            kind='RELAXED_ROUTE_PARTITION_LOWER_BOUND_NOT_COMPLETE_M3_SOLVER',
            native_relaxation_status=m.Status, lower_bound=lower,
            audited_upper_bound=upper,
            gap=upper-lower if lower is not None else None,
            closes_with_historical_audited_plan=lower is not None and abs(upper-lower) <= 1e-6,
            enumeration_seconds=enumeration_seconds, optimization_seconds=m.Runtime,
            total_including_loading_and_witness_audit_seconds=time.perf_counter()-started,
            route_columns=len(library), enumerated_prefixes=prefixes,
            binary_variables=m.NumBinVars, constraints=m.NumConstrs,
            work=m.Work, nodes=m.NodeCount, e1_distance_lower_bound=e1_lower,
            history_supplied_to_solver=False, original_plan_projection=projection,
            witness_path=str(path.relative_to(ROOT)), witness_sha256=sha(path),
            independent_original_audit_valid=True, periodic_replay_valid=True,
            input_hashes={str(q.relative_to(ROOT)): sha(q) for q in [case_path] + [ROOT / cfg[k] for k in (
                'data', 'orders', 'routing_config', 'operating_policy', 'economic_config')]},
            source_hashes={str(q.relative_to(ROOT)): sha(q) for q in [Path(__file__), ROOT / 'experiments/runs/research_model8_shared_relaxation.py']})
        save(target / 'summary.json', row)
        save(target / 'library.json', library)
        print(json.dumps({k: v for k, v in row.items() if k not in (
            'source_hashes', 'input_hashes', 'original_plan_projection')}, ensure_ascii=False), flush=True)
        return row
    finally:
        m.dispose()


if __name__ == '__main__':
    rows = []
    cases = sys.argv[1:] or [f'S{i:02}' for i in range(1, 10)]
    for case in cases:
        rows.append(run(case))
        save(OUT / 'route_partition_rows.json', rows)
