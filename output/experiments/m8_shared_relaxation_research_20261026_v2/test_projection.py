"""Map audited M8 witnesses into the relaxation, fixing their physical decisions."""
import json
from pathlib import Path

import pytest

from experiments.runs.research_model8_shared_relaxation import ROOT, build_relaxation
from models.common.cycle import load_instance
from models.common.cycle.audit import audit_solution
from tools.configuration.loader import load_routing_case

HERE = Path(__file__).resolve().parent


@pytest.mark.parametrize('case', [f'S{i:02d}' for i in range(1, 10)])
def test_audited_witness_projects_to_no_more_expensive_relaxation(case):
    row = json.loads((HERE / f'{case}.json').read_text(encoding='utf-8'))
    selected = min((a for a in row['witness_audits'] if a['valid'] and a['periodic']), key=lambda a: a['objective'])
    result = json.loads((ROOT / selected['path']).read_text(encoding='utf-8-sig'))
    p = load_instance(*load_routing_case(ROOT / f'data/formal_benchmark27_uniform_7_20_50_v1/model_8/small/{case}/case.json', use_pool_config=False))
    audit = audit_solution(p, result)
    assert audit['valid'] and audit['periodic_replay']['valid']
    library = json.loads((HERE / f'{case}_trip_library.json').read_text(encoding='utf-8'))
    model, ctx = build_relaxation(p, library)
    n, K, B = len(p.orders), result['cycle_batch_count'], max(ctx['selectors'])
    factor = p.count_clock(K)[2]
    orders = list(p.orders)
    positions = {o.id: i for i, o in enumerate(orders)}
    events = {event['order_id'] + event['kind']: event for trip in result['e2_trips'] for event in trip['events']}
    returned = {event['order_id']: trip['return_complete'] for trip in result['e2_trips'] for event in trip['events'] if event['kind'] == 'p'}
    batches = [b['id'] for b in result['batches']]

    def fix(var, value):
        var.LB = value
        var.UB = value

    try:
        for k, var in ctx['selectors'].items():
            fix(var, int(k == K))
        fix(ctx['pool'], result['container_pool_size'])
        for b in range(B + 1):
            physical = result['batch_boundaries'][b] / factor if b <= K else p.operating_policy.cycle_end
            fix(model.getVarByName(f'boundary[{b}]'), physical)
        for b in range(B):
            value = result['batch_cutoffs'][batches[b]] / factor if b < K else p.operating_policy.cycle_end
            fix(model.getVarByName(f'cutoff[{b}]'), value)
        for i, o in enumerate(orders):
            recovery = next(b for b in range(K) if returned[o.id] <= result['batch_cutoffs'][batches[b]])
            e1 = next(trip for trip in result['e1_trips'] if trip['batch_id'] == batches[recovery]
                      and any(e['kind'] == 'p' and e['site_id'] == o.satellite and e['quantity'] > 0 for e in trip['events']))
            fix(model.getVarByName(f'clean[{i}]'), e1['return_complete'] / factor + p.operating_policy.cleaning_duration)
            fix(model.getVarByName(f'delivery_{i}'), events[o.id + 'd']['start'] / factor)
            fix(model.getVarByName(f'pickup_{i}'), events[o.id + 'p']['start'] / factor)
            for b in range(B):
                fix(ctx['assignments'][i, b], int(b < K and result['order_batch_assignment'][o.id] == batches[b]))
                fix(ctx['recovery'][i, b], int(b == recovery))
        selected_columns = set()
        for trip in result['e2_trips']:
            task_bits = [positions[e['order_id']] + (n if e['kind'] == 'p' else 0) for e in trip['events']]
            mask = sum(1 << bit for bit in task_bits)
            r = next(r for r, col in enumerate(library) if col['mask'] == mask)
            route_nodes = [trip['satellite_id']] + [p.order_map[e['order_id']].customer for e in trip['events']] + [trip['satellite_id']]
            actual_distance = sum(p.distance(a, b) for a, b in zip(route_nodes, route_nodes[1:]))
            assert library[r]['distance'] <= actual_distance + 1e-7
            selected_columns.add(r)
        for r, var in ctx['route'].items():
            fix(var, int(r in selected_columns))
        model.Params.TimeLimit = 10
        model.optimize()
        assert model.Status == 2
        assert model.ObjVal <= audit['objective'] + 1e-6
    finally:
        model.dispose()
