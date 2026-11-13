"""Publish a separate one-box release with heterogeneous, interleaved appointments.

Only the four appointment endpoints change. A fixed-seed physical construction
conditions the random windows on feasibility; it is deliberately not advertised
as independent real-world sampling. No optimization objective is consulted.
Existing releases, experiments and solver implementations are never modified.
"""
from __future__ import annotations

import argparse
from collections import defaultdict, deque
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import math
from pathlib import Path
import random
import zipfile

from tools.configuration.loader import load_routing_case, resolve_project_path
from tools.data.build_formal_benchmark27 import digest, read, reference, write_json
from tools.data.formal_benchmark27_bounds import productive_count_certificate
from tools.data.one_box_contract import CONTRACT, preserved_orders_hash, preserved_policy_hash, validate_orders
from tools.audit.audit_formal_benchmark27 import audit_dataset
from models.common.cycle import load_instance
from models.common.cycle.instance import MultiBatchInstance
from models.common.cycle.audit import audit_solution
from models.common.cycle.state import empty_result
from models.common.cycle.joint import lift_result
from models.common.cycle.uniform import attach_uniform_decisions
from models.common.cycle.calendar import attach_calendar_decisions
from models.routing.model_5.alns.state import state_from_result, clone_state
from models.routing.model_5.alns.evaluator import evaluate, _e1_returns


VERSION = 'formal_benchmark27_interleaved_v1'
DEFAULT_RULES = 'config/datasets/interleaved_windows_v1.json'
WINDOW_FIELDS = {'delivery_ready', 'delivery_due', 'pickup_ready', 'pickup_due'}


def keyed_rng(rules, case_id, stream, identifier=''):
    value = f"{rules['seed']}:{rules['design_version']}:{case_id}:{stream}:{identifier}"
    return random.Random(int(hashlib.sha256(value.encode()).hexdigest(), 16))


def non_window_orders(orders):
    return sorted(({k: v for k, v in o.items() if k not in WINDOW_FIELDS} for o in orders),
                  key=lambda o: o['order_id'])


def load_problem(path):
    config, fleet = load_routing_case(path)
    return load_instance(config, fleet)


def feasible(problem, state):
    # The decoder's objective value is intentionally ignored, including ties.
    _, result, violations = evaluate(problem, state)
    return result if not violations and audit_solution(problem, result)['valid'] else None


def mixed_state(problem, original, rules, case_id):
    state = state_from_result(problem, original)
    for route in state['e2']:
        route.pop('depart_after', None)
        route.pop('return_after', None)
    if feasible(problem, state) is None:
        raise ValueError(f'{case_id}: baseline construction is infeasible; no case is dropped')
    moves, attempts = [], 0
    ids = [o.id for o in problem.orders]
    keyed_rng(rules, case_id, 'pickup-proposal-order').shuffle(ids)
    for oid in ids:
        if keyed_rng(rules, case_id, 'pickup-relocation-selection', oid).random() >= rules['pickup_relocation_probability']:
            continue
        source = next(i for i, r in enumerate(state['e2']) if (oid, 'p') in r['tasks'])
        site = problem.order_map[oid].satellite
        destinations = [i for i, r in enumerate(state['e2'])
                        if i != source and r['satellite'] == site
                        and any(k == 'd' and other != oid for other, k in r['tasks'])]
        rng = keyed_rng(rules, case_id, 'pickup-destination', oid)
        rng.shuffle(destinations)
        proposals = []
        for target in destinations:
            tasks = state['e2'][target]['tasks']
            positions = [i for i in range(1, len(tasks) + 1)
                         if any(k == 'd' and other != oid for other, k in tasks[:i])]
            rng.shuffle(positions)
            proposals.extend((target, position) for position in positions)
        for target, position in proposals[:rules['pickup_move_attempts_per_order']]:
            attempts += 1
            candidate = clone_state(state)
            candidate['e2'][source]['tasks'].remove((oid, 'p'))
            candidate['e2'][target]['tasks'].insert(position, (oid, 'p'))
            if feasible(problem, candidate) is not None:
                moves.append(dict(order_id=oid, source_route=source, target_route=target,
                                  different_vehicle=state['e2'][source]['vehicle'] != state['e2'][target]['vehicle']))
                state = candidate
                break
    return state, moves, attempts


def randomized_schedule(problem, state, rules, case_id):
    """Sample bounded slack in a causal DAG; backward bounds preserve feasibility."""
    initial = feasible(problem, state)
    if initial is None:
        raise ValueError(f'{case_id}: mixed construction failed')
    lower, upper, edges, task_nodes = {}, {}, [], {}
    previous_vehicle = {}
    last_cutoff = problem.operating_policy.batches[-1].cutoff
    for ri, (route, trip) in enumerate(zip(state['e2'], initial['e2_trips'])):
        begin, end = ('start', ri), ('end', ri)
        lower[begin] = trip['departure']
        lower[end] = problem.operating_policy.cycle_start
        upper[end] = last_cutoff
        key = route['satellite'], route['vehicle']
        has_delivery = any(kind == 'd' for _, kind in route['tasks'])
        if key in previous_vehicle:
            edges.append((previous_vehicle[key], begin, problem.operating_policy.e2_turnaround_time
                          + (problem.operating_policy.e2_dispatch_prep_time if has_delivery else 0)))
        previous_vehicle[key] = end
        previous, site, service = begin, route['satellite'], 0
        for ei, (oid, kind) in enumerate(route['tasks']):
            node = (ri, ei)
            order = problem.order_map[oid]
            lower[node] = problem.operating_policy.cycle_start
            edges.append((previous, node, service + problem.travel(site, order.customer)))
            task_nodes[oid, kind] = node
            previous, site, service = node, order.customer, order.service_time
        edges.append((previous, end, service + problem.travel(site, route['satellite'])
                      + problem.operating_policy.e2_return_unload_time))
    for order in problem.orders:
        edges.append((task_nodes[order.id, 'd'], task_nodes[order.id, 'p'],
                      order.service_time + order.min_use_time))
    children, parents = defaultdict(list), defaultdict(list)
    indegree = {node: 0 for node in lower}
    for a, b, delta in edges:
        children[a].append((b, delta))
        parents[b].append((a, delta))
        indegree[b] += 1
    queue = deque(sorted((n for n, d in indegree.items() if not d), key=str))
    ordered = []
    while queue:
        node = queue.popleft()
        ordered.append(node)
        for child, _ in children[node]:
            indegree[child] -= 1
            if not indegree[child]:
                queue.append(child)
    if len(ordered) != len(lower):
        raise ValueError(f'{case_id}: causal cycle')
    latest = {n: upper.get(n, last_cutoff) for n in lower}
    for node in reversed(ordered):
        for child, delta in children[node]:
            latest[node] = min(latest[node], latest[child] - delta)
    chosen = {}
    for node in ordered:
        earliest = max([lower[node]] + [chosen[a] + delta for a, delta in parents[node]])
        if earliest > latest[node]:
            raise ValueError(f'{case_id}: no temporal slack for {node}')
        if node[0] == 'end':
            jitter = 0
        else:
            maximum = rules['maximum_departure_jitter_minutes'] if node[0] == 'start' else rules['maximum_event_jitter_minutes']
            limit = min(latest[node] - earliest, maximum * 60)
            jitter = keyed_rng(rules, case_id, 'event-slack', repr(node)).randint(0, limit)
        chosen[node] = earliest + jitter
    result = deepcopy(initial)
    for ri, trip in enumerate(result['e2_trips']):
        trip['departure'] = chosen['start', ri]
        trip['return_complete'] = chosen['end', ri]
        for ei, event in enumerate(trip['events']):
            event['start'] = chosen[ri, ei]
    return result


def appointments(problem, original_orders, schedule, rules, case_id):
    times = {(e['order_id'], e['kind']): e['start'] for trip in schedule['e2_trips'] for e in trip['events']}
    result = deepcopy(original_orders)
    for row in result:
        oid = row['order_id']
        delivered, picked = times[oid, 'd'], times[oid, 'p']
        rng = keyed_rng(rules, case_id, 'window-widths', oid)
        early = rng.randint(*rules['delivery_early_slack_minutes'])
        late = rng.randint(*rules['delivery_late_slack_minutes'])
        row['delivery_ready'] = max(problem.operating_policy.minutes(problem.operating_policy.cycle_start),
                                    math.floor(delivered / 60) - early)
        latest_delivery = math.floor(picked / 60) - row['service_time'] - row['min_use_time']
        row['delivery_due'] = min(math.ceil(delivered / 60) + late, latest_delivery)
        row['pickup_ready'] = max(math.floor(picked / 60) - rng.randint(*rules['pickup_early_slack_minutes']),
                                  row['delivery_due'] + row['service_time'] + row['min_use_time'])
        row['pickup_due'] = min(math.ceil(picked / 60) + rng.randint(*rules['pickup_late_slack_minutes']),
                                problem.operating_policy.minutes(problem.operating_policy.cycle_end))
        if not (row['delivery_ready'] * 60 <= delivered <= row['delivery_due'] * 60
                and row['pickup_ready'] * 60 <= picked <= row['pickup_due'] * 60):
            raise ValueError(f'{case_id}/{oid}: random window lost its witness')
    if non_window_orders(result) != non_window_orders(original_orders):
        raise ValueError('A non-window order field changed')
    return result


def bind_windows(problem, rows):
    mapping = {o['order_id']: o for o in rows}
    return MultiBatchInstance(problem.network, [replace(o,
        delivery_window=(mapping[o.id]['delivery_ready'] * 60, mapping[o.id]['delivery_due'] * 60),
        pickup_window=(mapping[o.id]['pickup_ready'] * 60, mapping[o.id]['pickup_due'] * 60),
        min_use_time=mapping[o.id]['min_use_time'] * 60) for o in problem.orders],
        problem.operating_policy, model=5)


def rebuild_e1(problem, schedule, state):
    quantities = {b.id: {s: 0 for s in problem.satellites} for b in problem.operating_policy.batches}
    for trip in schedule['e2_trips']:
        picked = sum(e['quantity'] for e in trip['events'] if e['kind'] == 'p')
        batch = next((b for b in problem.operating_policy.batches if trip['return_complete'] <= b.cutoff), None)
        if picked and batch is None:
            raise ValueError('A return misses the final cutoff')
        if picked:
            quantities[batch.id][trip['satellite_id']] += picked
    schedule['e1_trips'], violations, _ = _e1_returns(problem, state, quantities)
    if violations:
        raise ValueError(f'E1 construction failed: {violations}')
    schedule['info']['objective'] = None
    audited = audit_solution(problem, schedule)
    if not audited['valid']:
        raise ValueError(f'Physical construction failed: {audited["errors"]}')
    schedule['info'].update(status='FEASIBLE', objective=audited['objective'], proven_optimal=False,
                             proof_scope='Feasible construction only; no optimization claim')
    schedule['solver'] = 'feasibility_witness'
    schedule['validation_only'] = True
    return schedule


def design_case(root, base, archive, rules):
    problem = load_problem(base['models']['5'])
    original = read(root/'shared'/base['size']/base['id']/'reference_orders.json')
    broad = [replace(o, delivery_window=(problem.operating_policy.cycle_start, problem.operating_policy.cycle_end),
                     pickup_window=(problem.operating_policy.cycle_start, problem.operating_policy.cycle_end),
                     min_use_time=o.min_use_time + rules['construction_use_buffer_minutes'] * 60)
             for o in problem.orders]
    construction = MultiBatchInstance(problem.network, broad, problem.operating_policy, model=5)
    source_witness = json.loads(archive.read(f'{base["id"]}/model_5.json'))
    state, moves, attempts = mixed_state(construction, source_witness, rules, base['id'])
    schedule = randomized_schedule(construction, state, rules, base['id'])
    rows = appointments(problem, original, schedule, rules, base['id'])
    final_problem = bind_windows(problem, rows)
    schedule = rebuild_e1(final_problem, schedule, state)
    validate_orders(rows, base['customer_count'])
    return rows, schedule, dict(accepted_pickup_relocations=moves, feasibility_proposals=attempts)


def remap(value, source, target):
    if isinstance(value, dict):
        return {k: remap(v, source, target) for k, v in value.items()}
    if isinstance(value, list):
        return [remap(v, source, target) for v in value]
    if isinstance(value, str) and (value == source or value.startswith(source + '/')
                                   or value.startswith(source + '_')):
        return target + value[len(source):]
    return value


def task_summary(rows, schedule):
    by_id = {o['order_id']: o for o in rows}
    tasks, owners = defaultdict(list), {}
    for trip in schedule['e2_trips']:
        for event in trip['events']:
            oid, kind = event['order_id'], event['kind']
            tasks[by_id[oid]['satellite_id']].append((event['start'], oid, kind))
            owners[oid, kind] = (trip['satellite_id'], trip['vehicle_id'], trip['trip_index'])
    crossing = []
    overlaps = []
    for a in rows:
        for b in rows:
            if a['order_id'] == b['order_id'] or a['satellite_id'] != b['satellite_id']:
                continue
            if max(a['pickup_ready'], b['delivery_ready']) <= min(a['pickup_due'], b['delivery_due']):
                overlaps.append([a['order_id'], b['order_id']])
    for site, events in sorted(tasks.items()):
        events.sort()
        for a, b, c in zip(events, events[1:], events[2:]):
            if tuple(e[2] for e in (a, b, c)) == ('d', 'p', 'd'):
                crossing.append(dict(satellite=site, events=[list(e) for e in (a, b, c)]))
    return dict(window_overlap_pickup_delivery_pairs=overlaps,
        chronological_delivery_pickup_delivery_examples=crossing,
        different_trip_orders=sum(owners[o['order_id'], 'd'] != owners[o['order_id'], 'p'] for o in rows),
        different_vehicle_orders=sum(owners[o['order_id'], 'd'][:2] != owners[o['order_id'], 'p'][:2] for o in rows),
        note='These are properties of an existence witness and its windows, not optimal-route results.')


def witnesses_for_base(base, m5_schedule):
    fixed = {5: deepcopy(m5_schedule)}
    p3 = load_problem(base['models']['3'])
    raw = empty_result(p3, 'feasibility_witness')
    raw['e2_trips'] = deepcopy(m5_schedule['e2_trips'])
    state = dict(deliveries={'b1': [[] for _ in range(p3.fleet.e1_count)]},
                 pickup_order={'b1': list(p3.satellites)}, pickup_vehicle={'b1': {}}, e2=[])
    for vehicle, site in enumerate(p3.satellites):
        if vehicle >= p3.fleet.e1_count:
            raise ValueError('A dedicated E1 feasibility route is unavailable')
        state['deliveries']['b1'][vehicle].append(site)
        state['pickup_vehicle']['b1'][site] = vehicle
    fixed[3] = rebuild_e1(p3, raw, state)
    p1 = load_problem(base['models']['1'])
    one = empty_result(p1, 'feasibility_witness')
    for layer in ('e1_trips', 'e2_trips'):
        one[layer] = deepcopy(fixed[3][layer])
        for trip in one[layer]:
            trip['events'] = [e for e in trip['events'] if e['kind'] == 'd']
    fixed[1] = one
    results, report = {}, []
    for model in range(1, 9):
        problem = load_problem(base['models'][str(model)])
        if model in fixed:
            result = deepcopy(fixed[model])
        elif model in (2, 4):
            result = deepcopy(fixed[model - 1])
            result['model'] = model
            result['assignment'] = {o.customer: o.satellite for o in problem.orders}
        else:
            child = problem.for_count(1) if model != 8 else problem.fixed_calendar(1)
            result = lift_result(child, fixed[3], {o.id: 'b1' for o in child.orders}, problem.total,
                                 'feasibility_witness', {'b1': fixed[3]['batches'][0]['cutoff']})
            if model == 6:
                attach_uniform_decisions(problem, result, 1)
            else:
                attach_calendar_decisions(problem, result, 1, problem.balanced_boundaries(1))
            result.pop('audit', None)
        if model <= 5:
            result['info']['objective'] = None
        audited = audit_solution(problem, result)
        if not audited['valid'] or not audited.get('periodic_replay', {'valid': True})['valid']:
            raise ValueError(f'{base["id"]}/M{model}: {audited["errors"]}')
        result['info'].update(status='FEASIBLE', objective=audited['objective'], proven_optimal=False,
                             proven_infeasible=False, best_bound=None, mip_gap=None,
                             proof_scope='Existence of a feasible physical schedule only')
        result.update(solver='feasibility_witness', validation_only=True)
        results[f'{base["id"]}/model_{model}.json'] = result
        report.append(dict(base_case_id=base['id'], model=model, valid=True,
                           periodic_replay=audited.get('periodic_replay', {}).get('valid'),
                           total_container_uses=problem.total))
    return results, report


def readme_text(rules, source):
    return f'''# 一客一箱：顾客间交错送取时间窗

27个基础算例、216份模型配置；小中大各9例，选例、坐标、货重、每客1箱、
分仓、车队、最低使用时间、服务时间、释放、参考分批、日历和费用保持不变。
仅改变每位顾客的 delivery_ready、delivery_due、pickup_ready、pickup_due。
旧版 `{source}` 及其求解结果未修改。

窗口规则见 window_rules.json，固定种子 {rules['seed']}。每位顾客严格先送后取：
pickup_ready >= delivery_due + service_time + min_use_time。
顾客之间分别扰动服务时刻和窗口宽度，回收任务可与其他顾客的配送任务交错。
不要求所有顾客先全部配送再统一回收，不绑定同车或同趟。

这是以显式可行调度为条件的合成预约场景，不是相互独立的实测时间窗。
可行构造会随机把回收安排到含其他顾客配送的路线中，再以因果图的最迟界约束
随机时间扰动。窗口宽度使用独立哈希流。构造过程只检查可行性，不比较距离、
成本、运行时间、gap或模型排名，不丢弃难例，不改变车队，不更换种子挑结果。
参考调度与模型优化相互独立：见证不是订单约束，也不会自动用作求解初始解。

所有模型在同一基础算例上使用相同物理订单。批次数证书按新窗口重新计算，
未加入人为K<=3限制。7顾客算例仍为每站1辆二级车；本次不增加车辆。
费用仍为0.1D1+0.05D2+5N+0.02Q，等待与工时费用仍为0。
因此新数据提供交错服务的可行空间，不承诺所有最优解都没有等待或都换车取送。

216项入口见 case_index.csv。validation/witnesses.zip 保存可行见证，
validation/feasibility.json 保存逐项独立重放记录，validation/windows.json
保存随机窗口、交错关系和非窗口字段保持检查。可行性不等于最优性，旧求解结果
不能作为本版本的求解结果。

生成：tools/run.ps1 python -Arguments @('-m','tools.data.build_interleaved_benchmark27')
审计：tools/run.ps1 python -Arguments @('-m','tools.audit.audit_formal_benchmark27',
  '--dataset','data/{VERSION}')
重放：tools/run.ps1 python -Arguments @('-m','tools.audit.certify_formal_benchmark27',
  '--dataset','data/{VERSION}','--verify')
'''


def publish(rules_path=DEFAULT_RULES):
    rules_path = resolve_project_path(rules_path)
    rules = read(rules_path)
    source = resolve_project_path(rules['source_dataset'])
    out = resolve_project_path(rules['output_dataset'])
    if out.exists():
        raise ValueError(f'Refusing to overwrite existing dataset: {out}')
    audit_dataset(source)
    source_manifest_hash = digest(source/'manifest.json')
    original_manifest = read(source/'manifest.json')
    designs = {}
    with zipfile.ZipFile(source/'validation/witnesses.zip') as archive:
        for base in original_manifest['base_cases']:
            designs[base['id']] = design_case(source, base, archive, rules)
            print(f'constructed {base["id"]}: {base["customer_count"]} customers', flush=True)
    source_ref, out_ref = reference(source), reference(out)
    # Copy inputs only. Old validation, status and historical results are not inherited.
    for path in sorted(source.rglob('*')):
        relative = path.relative_to(source)
        if not path.is_file() or relative.parts[0] == 'validation' or relative.as_posix() == 'manifest.json':
            continue
        target = out/relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix == '.json':
            value = remap(read(path), source_ref, out_ref)
            value = remap(value, original_manifest['dataset_version'], VERSION)
            write_json(target, value)
        elif path.suffix == '.csv':
            target.write_bytes(path.read_bytes().replace(source_ref.encode(), out_ref.encode()))
        else:
            target.write_bytes(path.read_bytes())
    manifest = remap(deepcopy(original_manifest), source_ref, out_ref)
    manifest.pop('feasibility_certification', None)
    manifest.update(dataset_version=VERSION, dataset_revision=5, generator=reference(Path(__file__)),
        generator_sha256=digest(__file__), source_dataset=source_ref,
        source_dataset_manifest_sha256=source_manifest_hash, window_rules='window_rules.json',
        scenario_label='一客一箱、顾客间交错送取预约', demand_contract=CONTRACT,
        limitations=[rules['dependence_disclosure'],
                     'Feasibility witnesses are not optimization results or algorithm success claims.',
                     'Zero waiting cost remains unchanged; interleaved windows do not eliminate all waiting.'])
    selection = read(out/'selection.json')
    selection.update(design_id='benchmark27_interleaved_v1', dataset_version=VERSION,
        source_dataset=source_ref, source_manifest=reference(source/'manifest.json'),
        source_manifest_sha256=source_manifest_hash, source_dataset_manifest_sha256=source_manifest_hash,
        window_design=rules, status='physically_validated_new_windows')
    selection['review_protocol'].update(dataset_revision=5, scenario_label=manifest['scenario_label'],
        formal_run_status='New time windows; previous solver results do not validate this release.',
        batch_domain_change='Input-derived certificates recomputed for the new windows.')
    selected_records = {r['id']: r for r in selection['cases']}
    window_report, all_witnesses, witness_rows = [], {}, []
    for base in manifest['base_cases']:
        rows, schedule, diagnostic = designs[base['id']]
        common = out/'shared'/base['size']/base['id']
        old_common = source/'shared'/base['size']/base['id']
        write_json(common/'reference_orders.json', rows)
        windows = {o['order_id']: {k: o[k] for k in WINDOW_FIELDS} for o in rows}
        for model in range(1, 9):
            cfg_path = resolve_project_path(base['models'][str(model)])
            cfg = read(cfg_path)
            order_path = resolve_project_path(cfg['orders'])
            orders = read(order_path)
            for order in orders:
                order.update(windows[order['order_id']])
            write_json(order_path, orders)
            contract_path = resolve_project_path(cfg['parameter_contract'])
            contract = read(contract_path)
            contract.update(window_contract=rules['design_version'])
            write_json(contract_path, contract)
        certificate = productive_count_certificate(load_problem(base['models']['5']))
        write_json(common/'batch_count_certificate.json', certificate)
        for model in (6, 7, 8):
            cfg_path = resolve_project_path(base['models'][str(model)])
            cfg = read(cfg_path)
            cfg['joint_batch_options']['batch_count_candidates'] = certificate['candidates_by_model'][str(model)]
            write_json(cfg_path, cfg)
            contract_path = resolve_project_path(cfg['parameter_contract'])
            contract = read(contract_path)
            contract['decision_bounds'] = cfg['joint_batch_options']
            write_json(contract_path, contract)
        original_rows = read(old_common/'reference_orders.json')
        preserved = non_window_orders(rows) == non_window_orders(original_rows)
        unchanged = {name: digest(common/name) == digest(old_common/name)
                     for name in ('network.csv', 'fleet.json', 'economic.json', 'reference_policy.json')}
        if not preserved or not all(unchanged.values()):
            raise ValueError(f'{base["id"]}: an unrelated input changed')
        summary = task_summary(rows, schedule)
        report = dict(base_case_id=base['id'], customer_count=len(rows),
            non_window_order_fields_preserved=preserved, unchanged_files=unchanged,
            one_box_valid=True, own_windows_strictly_ordered=all(
                o['pickup_ready'] >= o['delivery_due'] + o['service_time'] + o['min_use_time'] for o in rows),
            **summary, **diagnostic)
        window_report.append(report)
        write_json(common/'window_design.json', report)
        write_json(common/'provenance.json', dict(base_case_id=base['id'],
            family_id=base['family_id'], source_block=base['source_block'],
            customer_count=base['customer_count'], total_container_uses=base['total_container_uses'],
            source_dataset=source_ref, source_dataset_manifest_sha256=source_manifest_hash,
            source_reference_orders_sha256=digest(old_common/'reference_orders.json'),
            source_scenario_label='一客一箱、旧预约窗口', scenario_label=manifest['scenario_label'],
            demand_contract=CONTRACT, preserved_orders_sha256=preserved_orders_hash(rows),
            preserved_orders_hash_scope='Current derived reference integrity; old windows intentionally replaced.',
            preserved_policy_sha256=preserved_policy_hash(read(common/'reference_policy.json')),
            non_window_order_fields_preserved=True, window_design='window_design.json',
            transformation='Only four appointment endpoints change; no optimization-based filtering.'))
        record = selected_records[base['id']]
        record.update(common_reference_files={k: reference(common/name) for k, name in (
            ('data', 'network.csv'), ('orders', 'reference_orders.json'), ('routing_config', 'fleet.json'),
            ('operating_policy', 'reference_policy.json'), ('economic_config', 'economic.json'))},
            preserved_orders_sha256=preserved_orders_hash(rows),
            preserved_policy_sha256=preserved_policy_hash(read(common/'reference_policy.json')),
            batch_count_certificate_sha256=digest(common/'batch_count_certificate.json'),
            adaptation_status='four_window_endpoints_only; seeded_feasibility_conditioned_interleaving')
        record['reference_sha256'] = {k: digest(resolve_project_path(p))
                                      for k, p in record['common_reference_files'].items()}
        witnesses, audited_rows = witnesses_for_base(base, schedule)
        all_witnesses.update(witnesses)
        witness_rows.extend(audited_rows)
        print(f'validated {base["id"]}: 8 model configurations', flush=True)
    write_json(out/'selection.json', selection)
    write_json(out/'comparison_protocol.json', selection['review_protocol'])
    write_json(out/'window_rules.json', rules)
    (out/'README.md').write_text(readme_text(rules, source_ref), encoding='utf-8')
    validation = out/'validation'
    validation.mkdir()
    witness_hashes = {}
    with zipfile.ZipFile(validation/'witnesses.zip', 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, result in sorted(all_witnesses.items()):
            payload = (json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode()
            entry = zipfile.ZipInfo(name, date_time=(2026, 10, 19, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(entry, payload)
            witness_hashes[name] = hashlib.sha256(payload).hexdigest()
    write_json(validation/'feasibility.json', dict(valid=True, base_case_count=27, model_config_count=216,
        archive_sha256=digest(validation/'witnesses.zip'), witness_sha256=witness_hashes,
        method='Seeded cross-customer physical construction; independently replayed public model projections.',
        solver_optimization_calls=0, not_automatically_used_as_solver_start=True,
        proof_scope='216 feasible schedules; no optimality or algorithm-success claim', cases=witness_rows))
    write_json(validation/'windows.json', dict(valid=True, seed=rules['seed'], base_cases=27,
        customer_count=sum(r['customer_count'] for r in window_report),
        non_window_fields_preserved=True, rules_sha256=digest(rules_path),
        interleaving_definition='A pickup window overlaps another customer delivery window at the same satellite.',
        bases_with_window_overlap=sum(bool(r['window_overlap_pickup_delivery_pairs']) for r in window_report),
        bases_with_witness_dpd=sum(bool(r['chronological_delivery_pickup_delivery_examples']) for r in window_report),
        cases=window_report))
    manifest.update(feasibility_certification='validation/feasibility.json',
        selection_source=reference(out/'selection.json'), selection_sha256=digest(out/'selection.json'))
    manifest['artifact_sha256'] = {p.relative_to(out).as_posix(): digest(p)
        for p in sorted(out.rglob('*')) if p.is_file() and p != out/'manifest.json'}
    write_json(out/'manifest.json', manifest)
    audit_dataset(out)
    from tools.audit.certify_formal_benchmark27 import verify_witnesses
    verified = verify_witnesses(out)
    if digest(source/'manifest.json') != source_manifest_hash:
        raise ValueError('The source release changed during construction')
    audit_dataset(source)
    return dict(dataset=reference(out), valid=True, base_cases=27, model_configurations=216,
                independently_replayed=verified['audited'], source_unchanged=True,
                window_summary=reference(validation/'windows.json'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rules', default=DEFAULT_RULES)
    parser.add_argument('--probe', action='store_true', help='Construct and replay all bases without publishing files')
    args = parser.parse_args()
    if args.probe:
        rules = read(resolve_project_path(args.rules))
        root = resolve_project_path(rules['source_dataset'])
        manifest = read(root/'manifest.json')
        with zipfile.ZipFile(root/'validation/witnesses.zip') as archive:
            for base in manifest['base_cases']:
                rows, schedule, diagnostic = design_case(root, base, archive, rules)
                print(json.dumps(dict(case=base['id'], customers=len(rows),
                    moved=len(diagnostic['accepted_pickup_relocations']), valid=True)), flush=True)
    else:
        print(json.dumps(publish(args.rules), ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
