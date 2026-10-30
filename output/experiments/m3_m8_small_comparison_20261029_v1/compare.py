"""Audit the nine existing M3 cost optima against the fresh nine-case M8 proof.

This is a comparison of saved, hash-checked optimization evidence, not a new
M3 benchmark. Historical cold M3 times and warm M8 proof times stay labelled.
"""
from dataclasses import asdict
from datetime import datetime, timezone
import csv
import hashlib
import json
from pathlib import Path
import statistics
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))
from models.common.cycle import load_instance
from models.common.cycle.audit import audit_solution
from models.cost.comparison import evaluate_comparison_cost
from tools.configuration.loader import load_routing_case

DATASET = ROOT / 'data/formal_benchmark27_uniform_7_20_50_v1'
M3 = ROOT / 'output/experiments/staged_comparison_27_seed42_20261024_v1'
M3_S01 = ROOT / 'output/experiments/m3_s01_matched_cost_exact_20261024'
M8 = ROOT / 'output/experiments/m8_all_small_rerun_20261029_v1'
CASES = [f'S{i:02}' for i in range(1, 10)]
TOL = 1e-6
PHYSICAL_ORDER_FIELDS = ['id', 'customer', 'satellite', 'count', 'goods_weight',
                        'delivery_window', 'pickup_window', 'service_time',
                        'min_use_time', 'release_time']
PHYSICAL_POLICY_FIELDS = ['cycle_start', 'cycle_end', 'time_tick_seconds', 'time_tick_subdivision',
                         'cleaning_duration', 'e1_load_time', 'e2_dispatch_prep_time',
                         'e2_return_unload_time', 'depot_return_unload_time',
                         'e1_turnaround_time', 'e2_turnaround_time', 'e2_max_trips_per_vehicle',
                         'terminal_policy']


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def save(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def problem(path):
    return load_instance(*load_routing_case(path, use_pool_config=False))


def physical_orders(p):
    return [{key: getattr(o, key) for key in PHYSICAL_ORDER_FIELDS} for o in p.orders]


def main():
    rows, detail, used = [], [], {Path(__file__)}
    manifest3 = read(M3 / 'manifest.json')
    # Check the preserved source snapshot and its zip against the original
    # registration rather than assuming current code is the historical code.
    for relative, digest in manifest3['source_hashes'].items():
        assert sha(M3 / relative) == digest, relative
    for relative, digest in read(M8 / 'evidence_manifest.json')['files'].items():
        assert sha(M8 / relative) == digest, relative
    used.update([M3 / 'manifest.json', M8 / 'evidence_manifest.json', M8 / 'certificates.json'])
    for case in CASES:
        p3_path = DATASET / f'model_3/small/{case}/case.json'
        p8_path = DATASET / f'model_8/small/{case}/case.json'
        p3, p8 = problem(p3_path), problem(p8_path)
        assert p3.cost_objective and p3.cost_profile.hash == p8.cost_profile.hash
        assert physical_orders(p3) == physical_orders(p8)
        assert vars(p3.fleet) == vars(p8.fleet)
        assert list(p3.network.nodes) == list(p8.network.nodes)
        for a in p3.network.nodes:
            for b in p3.network.nodes:
                assert p3.distance(a, b) == p8.distance(a, b)
                assert p3.travel(a, b) == p8.travel(a, b)
        assert all(getattr(p3.operating_policy, key) == getattr(p8.operating_policy, key)
                   for key in PHYSICAL_POLICY_FIELDS)
        assert len(p3.operating_policy.batches) == 1
        assert p3.operating_policy.container_pool_size == 7
        assert list(p8.batch_count_candidates) == list(range(1, 8))
        if case == 'S01':
            result3_path = M3_S01 / 'expanded_result.json'
            summary3_path = M3_S01 / 'expanded_summary.json'
            frozen_case3 = M3_S01 / 'case.json'
            completion_path = M3_S01 / 'expanded_completion.json'
            for name, digest in read(completion_path)['artifacts'].items():
                assert sha(M3_S01 / name) == digest, name
            for name, digest in read(M3_S01 / 'manifest.json')['inputs'].items():
                assert sha(M3_S01 / name) == digest, name
            used.add(M3_S01 / 'manifest.json')
        else:
            folder = M3 / f'runs/A_{case}_m3_cost_exact_300s_s42/attempt_001'
            result3_path = folder / 'result.json'
            summary3_path = folder / 'row.json'
            frozen_case3 = M3 / f'cases/{case}_m3_cost.json'
            completion_path = folder / 'completion.json'
            for name, digest in read(completion_path)['hashes'].items():
                assert sha(folder / name) == digest, name
        frozen3 = problem(frozen_case3)
        assert asdict(frozen3.operating_policy) == asdict(p3.operating_policy)
        assert [asdict(o) for o in frozen3.orders] == [asdict(o) for o in p3.orders]
        assert vars(frozen3.fleet) == vars(p3.fleet)
        assert frozen3.cost_profile.hash == p3.cost_profile.hash
        for a in p3.network.nodes:
            for b in p3.network.nodes:
                assert frozen3.distance(a, b) == p3.distance(a, b)
                assert frozen3.travel(a, b) == p3.travel(a, b)
        result3, native3 = read(result3_path), read(summary3_path)
        assert result3['info']['status'] == 'OPTIMAL' and result3['info']['proven_optimal']
        assert native3['status'] == 'OPTIMAL' and native3['proven_optimal']
        assert result3['objective_mode'] == 'cost'
        result8_path = M8 / f'runs/{case}/s42/best_audited_result.json'
        summary8_path = M8 / f'runs/{case}/s42/summary.json'
        result8, native8 = read(result8_path), read(summary8_path)
        assert native8['proven_optimal']
        audit3, audit8 = audit_solution(p3, result3), audit_solution(p8, result8)
        assert all(a['valid'] and a['periodic_replay']['valid'] for a in [audit3, audit8])
        cost3 = evaluate_comparison_cost(p3, result3, p3.cost_profile.config, audit=audit3)
        cost8 = evaluate_comparison_cost(p8, result8, p8.cost_profile.config, audit=audit8)
        c3, c8 = cost3['total'], cost8['total']
        assert abs(c3 - result3['info']['best_bound']) <= TOL
        assert abs(c8 - native8['global_lower_bound']) <= TOL
        pieces3, pieces8 = audit3['cost']['components'], audit8['cost']['components']
        n3, n8 = result3['container_pool_size'], result8['container_pool_size']
        d13, d23 = audit3['distances']['e1'], audit3['distances']['e2']
        d18, d28 = audit8['distances']['e1'], audit8['distances']['e2']
        # The common economic contract here has no extra working/fixed charges.
        assert abs(c3 - (.1*d13 + .05*d23 + 5*n3 + .02*p3.total)) <= TOL
        assert abs(c8 - (.1*d18 + .05*d28 + 5*n8 + .02*p8.total)) <= TOL
        pool_saving = pieces3['container_pool'] - pieces8['container_pool']
        transport_saving = (pieces3['e1_distance'] + pieces3['e2_distance']
                            - pieces8['e1_distance'] - pieces8['e2_distance'])
        assert abs(c3 - c8 - pool_saving - transport_saving) <= TOL
        row = dict(case=case, m3_optimal_cost=c3, m8_optimal_cost=c8,
                   cost_saving=c3-c8, saving_percent=(c3-c8)/c3*100,
                   m3_N=n3, m8_N=n8, m3_K=1, m8_K=result8['cycle_batch_count'],
                   pool_cost_saving=pool_saving, transport_cost_saving=transport_saving,
                   m3_e1_cost=pieces3['e1_distance'], m3_e2_cost=pieces3['e2_distance'],
                   m8_e1_cost=pieces8['e1_distance'], m8_e2_cost=pieces8['e2_distance'],
                   m3_historical_cold_wall_seconds=native3['solve_wall_seconds'],
                   m8_warm_proof_wall_seconds=native8['total_wall_seconds'],
                   m3_proven_optimal=True, m8_proven_optimal=True,
                   economic_profile_hash=p3.cost_profile.hash, independent_pair_audit_valid=True)
        assert row['cost_saving'] > 0
        rows.append(row)
        detail.append(dict(case=case, m3_result_path=str(result3_path), m3_summary_path=str(summary3_path),
                           m8_result_path=str(result8_path), m8_summary_path=str(summary8_path),
                           m3_audit=audit3, m8_audit=audit8, m3_repriced=cost3, m8_repriced=cost8,
                           m3_input_scope_matches_frozen_proof=True, shared_physical_fields_match=True,
                           m3_native_status='OPTIMAL', m8_global_certificate=True))
        for cfg_path in [p3_path, p8_path, frozen_case3]:
            cfg, _ = load_routing_case(cfg_path, use_pool_config=False)
            used.add(cfg_path)
            for key in ['data', 'orders', 'routing_config', 'operating_policy', 'economic_config',
                        'dataset_metadata', 'parameter_contract', 'batch_count_certificate']:
                if isinstance(cfg.get(key), str):
                    path = Path(cfg[key])
                    used.add(path if path.is_absolute() else ROOT / path)
        used.update([result3_path, summary3_path, completion_path, result8_path, summary8_path])
    total3 = sum(r['m3_optimal_cost'] for r in rows)
    total8 = sum(r['m8_optimal_cost'] for r in rows)
    statistics_row = dict(cases=9, m3_optimal=9, m8_optimal=9, m8_lower_cost=9,
        total_m3_cost=total3, total_m8_cost=total8, total_saving=total3-total8,
        aggregate_saving_percent=(total3-total8)/total3*100,
        mean_case_saving_percent=statistics.mean(r['saving_percent'] for r in rows),
        minimum_case_saving_percent=min(r['saving_percent'] for r in rows),
        maximum_case_saving_percent=max(r['saving_percent'] for r in rows),
        total_pool_cost_saving=sum(r['pool_cost_saving'] for r in rows),
        total_transport_cost_saving=sum(r['transport_cost_saving'] for r in rows),
        comparison_uses_existing_solver_evidence=True, new_optimization_runs=0,
        independent_original_plan_audits=18, time_comparison_is_controlled_speed_benchmark=False)
    save(HERE / 'rows.json', rows)
    save(HERE / 'statistics.json', statistics_row)
    save(HERE / 'independent_pair_audits.json', detail)
    with (HERE / 'comparison.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    lines = ['# 模型三与模型八：九个小规模算例的同成本目标对照', '',
        '2026-10-29。重新审计既有的两模型方案、成本与证据后，九例中模型八的已证最优成本均低于模型三的已证最优成本。'
        '本次没有新增正式优化运行，模型三采用已保存的原生最优证明，模型八采用本日九例重跑证据。', '',
        '共同目标函数为 `F = 0.1 D1 + 0.05 D2 + 5 N + 0.02 Q`，每例 Q=7。'
        '这是真正直接优化同一成本函数，不是给距离最优路线事后计价。'
        '两模型经济 profile hash、网络距离/行驶时间、车辆和订单物理字段均核对一致。', '',
        '| 算例 | M3 最优成本 | M8 最优成本 | 节省 | 节省比例 | M3/M8 箱池 | M8 批数 |',
        '|---|---:|---:|---:|---:|---:|---:|']
    for r in rows:
        lines.append(f'| {r["case"]} | {r["m3_optimal_cost"]:.6f} | {r["m8_optimal_cost"]:.6f} | '
                     f'{r["cost_saving"]:.6f} | {r["saving_percent"]:.2f}% | {r["m3_N"]}/{r["m8_N"]} | {r["m8_K"]} |')
    lines += ['', f'九例成本合计：M3={total3:.9f}，M8={total8:.9f}，合计节省 {total3-total8:.9f}，'
        f'按总成本计算降低 {statistics_row["aggregate_saving_percent"]:.2f}%。'
        f'逐例节省比例的算术平均为 {statistics_row["mean_case_saving_percent"]:.2f}%；两种平均口径分别列出。', '',
        '## 收益来源', '',
        '模型三保留固定单批、原固定日历和 N=7；模型八在原始 K=1…7 域内选择批数、批长和截点，并优化 N。'
        '这是不同运营决策域在相同经济系数下的成本比较。未通过交换调度来假设其离散日历允许域严格包含。', '',
        f'九例合计箱池成本减少 {statistics_row["total_pool_cost_saving"]:.6f}；'
        f'运输成本增加 {-statistics_row["total_transport_cost_saving"]:.6f}；清洗和损耗成本相同。'
        '因此本组总成本收益来自箱池投资下降超过额外运输成本，不能表述成模型八同时减少所有运输费用。', '',
        '| 算例 | 箱池费用减少 | 运输费用变化（M8−M3） | 总成本减少 |',
        '|---|---:|---:|---:|']
    for r in rows:
        lines.append(f'| {r["case"]} | {r["pool_cost_saving"]:.6f} | {-r["transport_cost_saving"]:.6f} | {r["cost_saving"]:.6f} |')
    lines += ['', '## 耗时记录及其适用范围', '',
        '**下表不是受控求解速度实验，不能据此计算两模型的公平加速倍数。**'
        'M3 为 2026-10-24 的冷启动直接 Gurobi、expanded、单线程、种子 42；'
        'S01 采用单独补跑成功证明最优的一次记录，其他八例采用阶段实验记录。'
        'M8 为 2026-10-29 的加强主问题及完整子问题、单线程、种子 42，复用已审计可行上界，重新枚举车次和计算所有下界。'
        '历史运行时间、数学表达和可用信息都不同。', '',
        '| 算例 | M3 历史冷启动总秒 | M8 本日带可行上界重证总秒 |', '|---|---:|---:|']
    for r in rows:
        lines.append(f'| {r["case"]} | {r["m3_historical_cold_wall_seconds"]:.3f} | {r["m8_warm_proof_wall_seconds"]:.3f} |')
    lines += ['', '成本最优值可以按上述输入匹配比较。要评估求解速度优劣，需要另行统一表达、启动信息、计时和机器负载；'
        '本次只核对已有结果，不把时间并列表解释为该结论。', '',
        '## 证据核验', '',
        '本次对 18 份原业务方案重新审计、复算成本与两周期回放，全部通过。'
        'M3 九份方案均有原生 OPTIMAL 与成本一致的下界；S01 的首轮阶段结果原为 TIME_LIMIT，'
        '本表明确采用其单独 expanded 补跑的 OPTIMAL 证据，未更改任何历史状态。'
        '其余八例 completion.json、S01 expanded_completion.json 的结果哈希、冻结源码与输入快照均核验。'
        'M8 本日证据清单全部通过 SHA256 核验，原始域为 K=1…7，绝对证明容差 1e-6。', '',
        '没有新增单元测试，也未运行全仓库测试；本次统计的是 18 份方案审计，不将它们称为 18 项 pytest 测试。'
        '运行器预检 READY，Gurobi 11.0.3 的实际整数优化成功。', '',
        'comparison.csv / rows.json：逐例成本、节省、分项、箱池/批数、各自耗时。'
        'independent_pair_audits.json：原证据路径与本次完整复核。statistics.json：汇总口径。'
        'manifest.json：所引用数据、结果、证据和分析程序的哈希。', '']
    (HERE / 'report.md').write_text('\n'.join(lines), encoding='utf-8')
    save(HERE / 'manifest.json', dict(created_at=datetime.now(timezone.utc).isoformat(),
        comparison='existing_M3_cost_optima_vs_20261029_M8_global_certificates',
        cases=CASES, new_optimization_runs=0, source_and_evidence_hashes={str(p): sha(p) for p in sorted(used, key=str)},
        derived_hashes={p.name: sha(p) for p in HERE.iterdir() if p.is_file() and p.name != 'manifest.json'}))
    print(json.dumps(dict(statistics=statistics_row, rows=rows), ensure_ascii=False, indent=2), flush=True)


if __name__ == '__main__':
    main()
