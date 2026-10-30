"""Extend S01's K=2 route-relaxation certificate to the full input K domain.

This is an external mathematical certificate, not a native OPTIMAL status
for the direct arc MILP. All historical evidence is read without alteration.
"""
from copy import deepcopy
import hashlib
import importlib.util
import itertools
import json
import math
from pathlib import Path
import sys
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location('stock_prototype', HERE/'occupancy_probe.py')
stock = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stock)
trial = stock.trial


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def verify():
    begun = time.perf_counter()
    case = ROOT/'output/experiments/small7_m8_alns300_20261024_v1/cases/S01_m8.json'
    parent = trial.load_instance(*trial.load_routing_case(case, use_pool_config=False))
    assert list(parent.batch_count_candidates) == [1, 2, 3, 4, 5, 6, 7]
    problem = parent.for_count(1)
    assert problem.require_productive_batches
    historical_case = ROOT/'output/experiments/small7_m8_k1_4_exact1800_20261024_v1/cases/S01_m8_k1_4.json'
    historical = trial.load_instance(*trial.load_routing_case(historical_case, use_pool_config=False)).for_count(1)
    assert problem.orders == historical.orders
    assert vars(problem.fleet) == vars(historical.fleet)
    assert problem.cost_profile.rates == historical.cost_profile.rates
    for field in ('cycle_start', 'cycle_end', 'time_tick_seconds', 'time_tick_subdivision',
                  'cleaning_duration', 'e1_load_time', 'e2_dispatch_prep_time', 'e2_return_unload_time',
                  'depot_return_unload_time', 'e1_turnaround_time', 'e2_turnaround_time', 'e2_max_trips_per_vehicle'):
        assert getattr(problem.operating_policy, field) == getattr(historical.operating_policy, field), field
    assert set(problem.network.nodes) == set(historical.network.nodes)
    for a, b in itertools.product(problem.network.nodes, repeat=2):
        assert problem.travel(a, b) == historical.travel(a, b)
        assert problem.distance(a, b) == historical.distance(a, b)
    assert all(problem.service(s) == historical.service(s) for s in problem.satellites)
    previous = ROOT/'output/experiments/m8_s01_k2_valid_bounds_exact_20261024'
    old_certificate = read(previous/'optimality_certificate.json')
    for name, digest in old_certificate['evidence_hashes'].items():
        assert trial.sha(previous/name) == digest, name
    seed_path = ROOT/'output/experiments/m8_s01_fixed_k_minute_exact_20261024/K2_result.json'
    assert trial.sha(seed_path) == old_certificate['source_solution_sha256']
    seed = read(seed_path)
    seed = deepcopy(seed)
    original_info = deepcopy(seed['info'])
    trial.attach_calendar_decisions(parent, seed, 2, seed['batch_boundaries'])
    audit = trial.audit_solution(parent, seed)
    assert audit['valid'] and audit['periodic_replay']['valid']
    upper = audit['objective']
    assert math.isclose(upper, old_certificate['objective'], abs_tol=1e-7)

    assert len(problem.satellites) == 1 and len(problem.orders) == 7
    assert all(o.count == 1 for o in problem.orders)
    assert problem.fleet.e2_volume_cap == 3 and problem.fleet.container_volume == 1
    interval_bound = stock.occupancy_bound(problem)
    forced = stock.forced_first_batch(problem, interval_bound)
    first_ids = sorted(row['order'] for row in forced)
    assert len(first_ids) == 4
    reduction = read(previous/'reduction_certificate.json')
    assert first_ids == sorted(reduction['forced_first_batch_orders'])
    assert interval_bound['pool_lower_bound'] == 4

    # Independently establish the metric tour bound for N >= 5 dominance.
    site = problem.satellites[0]
    customers = sorted({o.customer for o in problem.orders})
    nodes = [problem.depot, site] + customers
    for a, b, c in itertools.product(nodes, repeat=3):
        assert problem.distance(a, c) <= problem.distance(a, b) + problem.distance(b, c) + 1e-8
    dp = {(1 << j, j): problem.distance(site, customers[j]) for j in range(len(customers))}
    for mask in range(1, 1 << len(customers)):
        for j in range(len(customers)):
            if (mask, j) not in dp:
                continue
            for k in range(len(customers)):
                if mask >> k & 1:
                    continue
                key = mask | (1 << k), k
                dp[key] = min(dp.get(key, math.inf), dp[mask, j]+problem.distance(customers[j], customers[k]))
    tour = min(dp[(1 << len(customers))-1, j]+problem.distance(customers[j], site)
               for j in range(len(customers)))
    assert math.isclose(tour, reduction['TSP_distance_lower_bound'], abs_tol=1e-7)
    rates = problem.cost_profile.rates
    assert rates['distance_scale_to_km'] == 1
    assert all(value >= 0 for value in rates.values() if isinstance(value, (int, float)))
    constant = sum(problem.cost_profile.constant_cost(problem.total).values())
    one_e1_cycle = problem.distance(problem.depot, site)+problem.distance(site, problem.depot)

    # Exact cover recheck of the exhaustive trip relaxation. Its columns
    # permit any subset of the late group on a trip, so also cover K>2:
    # separating that group across more batches only restricts possibilities.
    patterns = read(previous/'route_bound_patterns.json')
    gp = trial.gp
    with gp.Model('S01_full_K_route_relaxation_recheck') as model:
        model.Params.OutputFlag = 0
        model.Params.Threads = 1
        model.Params.MIPGap = 0
        model.Params.MIPGapAbs = 0
        model.Params.TimeLimit = 30
        x = model.addVars(len(patterns), vtype=gp.GRB.BINARY)
        for task in range(14):
            model.addConstr(gp.quicksum(x[j] for j, p in enumerate(patterns) if p['mask'] >> task & 1) == 1)
        model.setObjective(gp.quicksum(p['distance']*x[j] for j, p in enumerate(patterns)), gp.GRB.MINIMIZE)
        model.optimize()
        assert model.Status == gp.GRB.OPTIMAL
        route_bound = model.ObjVal
        assert math.isclose(route_bound, old_certificate['E2_distance_lower_bound'], abs_tol=1e-7)
        partition = dict(status='OPTIMAL', patterns=len(patterns), constraints=model.NumConstrs,
                         objective=model.ObjVal, lower_bound=model.ObjBound, seconds=model.Runtime)

    rows = []
    for count in parent.batch_count_candidates:
        pool_min = max(4, (problem.total+count-1)//count)
        loose = (pool_min*rates['container_pool_cost_per_period'] + count*one_e1_cycle*rates['e1_distance_cost_per_km']
                 + tour*rates['e2_distance_cost_per_km'] + constant)
        if count == 1:
            assert loose > upper + 1e-7
            rows.append(dict(K=count, status='DOMINATED', cost_lower_bound=loose, pool_lower_bound=pool_min))
            continue
        n5 = (5*rates['container_pool_cost_per_period'] + count*one_e1_cycle*rates['e1_distance_cost_per_km']
              + tour*rates['e2_distance_cost_per_km'] + constant)
        assert n5 > upper + 1e-7
        # N>=4 and N>=5 dominated, so only N=4 can challenge the witness.
        # The four forced first-batch orders fill this stock completely;
        # all other orders must be allocated to batches 2..K.
        if count > 1 + len(problem.orders) - len(first_ids):
            rows.append(dict(K=count, status='INFEASIBLE',
                             reason='Too few remaining indivisible orders for productive batches',
                             N_ge_5_cost_lower_bound=n5))
            continue
        value = (4*rates['container_pool_cost_per_period'] + count*one_e1_cycle*rates['e1_distance_cost_per_km']
                 + route_bound*rates['e2_distance_cost_per_km'] + constant)
        assert value >= upper - 1e-7
        rows.append(dict(K=count, status='CLOSED' if count == 2 else 'DOMINATED',
                         cost_lower_bound=value, N_ge_5_cost_lower_bound=n5, competitive_pool=4))

    certificate = dict(status='CERTIFIED_OPTIMAL', case='S01', model=8,
        complete_batch_count_domain=list(parent.batch_count_candidates),
        selected_K=2, pool=4, objective=upper, global_lower_bound=upper, gap=0,
        proof_method='Necessary time-order/stock deductions plus exhaustive feasible-trip relaxation certificate',
        native_full_MILP_status='No direct arc MILP run proved OPTIMAL; native statuses and logs are retained separately',
        audit_valid=True, two_cycle_replay_valid=True, K_rows=rows,
        mandatory_occupancy=interval_bound, forced_first_batch=forced,
        relaxed_TSP_distance=tour, E1_minimum_distance_per_productive_batch=one_e1_cycle,
        relaxed_route_partition=partition,
        previous_trip_enumeration_sha256=trial.sha(previous/'route_lower_bound.py'),
        previous_patterns_sha256=trial.sha(previous/'route_bound_patterns.json'),
        historical_physical_inputs_independently_compared=True,
        case_sha256=trial.sha(case), original_witness_sha256=trial.sha(seed_path),
        prototype_sha256=trial.sha(HERE/'occupancy_probe.py'),
        clock_scope='Necessary physical-time inequalities also cover each K-specific refined integer clock',
        verification_seconds=time.perf_counter()-begun)
    seed.update(optimality_certificate=certificate, certified_full_domain_optimal=True)
    # Retain the original native TIME_LIMIT rather than relabeling it OPTIMAL.
    seed['info'] = original_info
    seed['audit'] = audit
    return certificate, seed


def main():
    out = HERE/'full_domain_certificate'
    out.mkdir(exist_ok=True)
    certificate, witness = verify()
    trial.write(out/'certificate.json', certificate)
    trial.write(out/'audited_witness_with_certificate.json', witness)
    lines = ['# S01 模型八：完整 K=1～7 的最优性证明', '',
        f"最优成本 **{certificate['objective']:.12f}**，K=2，箱池4；原秒级物理输入及各K的细分时钟均被覆盖。", '',
        '这是一份业务必要条件与完整单趟松弛下界构成的外部数学证明。原直接MILP的TIME_LIMIT状态保留，未伪装成Gurobi原生OPTIMAL。', '',
        '1. 每单最晚领箱时刻，由配送截止减去最短供货及到客时间得到；最早回总仓时刻由最早配送、使用、回收和最短回运得到。旅行采用允许网络的最短路下界。',
        '2. 假设某单不在第一批，则第一批必须先回收另一单；如果所有其他单的最早回总仓都晚于该单的最晚领箱，就矛盾。S01的四笔早订单因此必须同属第一批，对每个K都成立。',
        '3. 第一批至少需要4箱。N≥5时，即使只计最低箱池、一级运输和最短客户巡回费用，K≥2也已比现有34.9051方案贵；K=1至少需要7箱，同样被排除。因此只需考虑N=4。',
        '4. 四笔早订单已填满第一批4箱，剩余三单只能分到后续批次。因此K≥5不能满足每批非空配送。',
        '5. 复用已核验的完整单趟枚举松弛：每趟配送不能混合第一批与其他批，但允许任意后续订单组合，故同时放松并覆盖K=2、3、4。独立Gurobi集合划分重算二级距离下界180.75854486747826。',
        '6. 每增加一个实际批次都至少增加一次一级往返。下表排除所有K≠2；K=2下界与经原业务审计及两周期重放的方案费用相等。', '',
        '| K | 结论 | 成本下界 |', '|---|---|---:|']
    for row in certificate['K_rows']:
        value = row.get('cost_lower_bound')
        lines.append(f"| {row['K']} | {row['status']} | {'—' if value is None else f'{value:.12f}'} |")
    lines += ['', '本次证明复用了此前完整枚举及其测试证据；新增独立复核与结构推导不代表通用算法端到端求解时间。',
              '本结论仅针对S01；其他算例尚未进行同类最优性证明。证书和原状态保留的完整方案见同目录JSON。', '']
    (out/'report.md').write_text('\n'.join(lines), encoding='utf-8')
    print(json.dumps(certificate, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
