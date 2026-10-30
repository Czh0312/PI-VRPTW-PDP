"""Compare saved M4 ALNS and network-MILP records without running a solver."""
import csv
import hashlib
import json
import math
from pathlib import Path
import statistics

FOLDER = Path(__file__).resolve().parent
ROOT = FOLDER.parents[2]
HISTORICAL = ROOT/'output/experiments/small7_exact600_alns60_20261023_v1'
SMALL = ROOT/'output/experiments/m4_other_small_network_20261030_v1'
S02 = ROOT/'output/experiments/m4_network_transfer_S02_20261030_v1/network'
DATASET = 'data/formal_benchmark27_uniform_7_20_50_v1'


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')


with (HISTORICAL/'tables/runs.csv').open(encoding='utf-8-sig', newline='') as stream:
    alns = {r['case']: r for r in csv.DictReader(stream) if r['model'] == '4' and r['method'] == 'alns'}
plan = read(SMALL/'plan.json')
s02_protocol = read(S02/'protocol.json')
rows, input_checks, evidence = [], [], []
for case in sorted(alns):
    old = alns[case]
    directory = S02 if case == 'S02' else SMALL/'runs'/case/'network'
    new = read(directory/'row.json')
    assert new['proven_optimal'] and new['valid'] and new['periodic_valid']
    assert new['solver_info']['solver_config']['seed'] == 42
    assert new['solver_info']['solver_config']['threads'] == 1
    assert old['valid'] == 'True' and old['proven_optimal'] == 'False' and old['budget'] == '60'
    assert math.isclose(new['objective'], float(old['reference_objective']), abs_tol=1e-6, rel_tol=1e-9)
    specs = (
        ('data_network.csv', f'{DATASET}/shared/small/{case}/network.csv', 'data.csv'),
        ('routing_config_fleet.json', f'{DATASET}/shared/small/{case}/fleet.json', 'routing_config.json'),
        ('orders_orders.json', f'{DATASET}/model_4/small/{case}/orders.json', 'orders.json'),
        ('operating_policy_policy.json', f'{DATASET}/model_4/small/{case}/policy.json', 'operating_policy.json'),
        ('economic_config_economic.json', f'{DATASET}/shared/small/{case}/economic.json', 'economic_config.json'),
    )
    for suffix, relative, s02_name in specs:
        historical_file = HISTORICAL/'inputs'/f'{case}_m4_{suffix}'
        exact_file = S02/'inputs'/s02_name if case == 'S02' else SMALL/'snapshot'/relative
        registered = (s02_protocol['input_sha256'][s02_name] if case == 'S02'
                      else plan['case_inputs'][case]['input_hashes'][relative])
        digest = sha(historical_file)
        assert digest == sha(exact_file) == registered, (case, suffix)
        input_checks.append(dict(case=case, field=suffix, sha256=digest,
            historical_file=historical_file.relative_to(ROOT).as_posix(),
            exact_file=exact_file.relative_to(ROOT).as_posix()))
    gap = max(0., 100*(float(old['objective'])-new['objective'])/new['objective'])
    hit = float(old['first_optimum_recorded_seconds']) if old['first_optimum_recorded_seconds'] else None
    assert (hit is not None) == (old['matches_known_optimum'] == 'True')
    if hit is not None:
        assert gap < 1e-7
    rows.append(dict(case=case, alns_objective=float(old['objective']), exact_optimum=new['objective'],
        alns_gap_percent=gap, alns_total_seconds=float(old['solve_wall_seconds']),
        alns_first_optimum_internal_seconds=hit,
        alns_first_feasible_internal_seconds=float(old['first_feasible_recorded_seconds']),
        exact_total_proof_seconds=new['total_seconds'], exact_proven_optimal=True,
        alns_matches_optimum=hit is not None, alns_proven_optimal=False))
    evidence.append(dict(case=case, exact_row=directory.relative_to(ROOT).as_posix()+'/row.json',
                         exact_row_sha256=sha(directory/'row.json')))

assert len(rows) == 9 and len(input_checks) == 45
hits = [r for r in rows if r['alns_matches_optimum']]
summary = dict(cases=9, seed=42, new_experiment_calls=0,
    alns_budget_seconds=60, exact_budget_seconds=300,
    alns_matches_optimum=len(hits), alns_native_optimal_proofs=0, exact_native_optimal_proofs=9,
    alns_mean_full_call_seconds=statistics.mean(r['alns_total_seconds'] for r in rows),
    exact_mean_full_proof_seconds=statistics.mean(r['exact_total_proof_seconds'] for r in rows),
    alns_mean_gap_percent=statistics.mean(r['alns_gap_percent'] for r in rows),
    alns_max_gap_percent=max(r['alns_gap_percent'] for r in rows),
    alns_mean_first_optimum_internal_seconds_on_hits=statistics.mean(r['alns_first_optimum_internal_seconds'] for r in hits),
    alns_hit_case_count_in_time_mean=len(hits),
    exact_mean_full_proof_seconds_on_alns_hits=statistics.mean(r['exact_total_proof_seconds'] for r in hits),
    exact_shorter_full_call_cases=[r['case'] for r in rows if r['exact_total_proof_seconds'] < r['alns_total_seconds']],
    all_45_physical_input_files_identical=True,
    alns_experiment_date='2026-10-23', exact_experiment_date='2026-10-30',
    alns_dataset_label='formal_benchmark27_spacetime_v4_1', exact_dataset_label='formal_benchmark27_uniform_7_20_50_v1',
    timing_limits='Historical records, fixed-budget ALNS full calls versus proof-stopped MILP; first-hit timestamps are internal search time only.')
write(FOLDER/'summary.json', summary)
write(FOLDER/'verification.json', dict(physical_input_checks=input_checks, evidence=evidence,
    alns_csv_sha256=sha(HISTORICAL/'tables/runs.csv'), historical_reference_values_match_new_optima=True,
    no_new_experiment_calls=True, first_hit_means_exclude_unmatched_cases=True))
with (FOLDER/'comparison.csv').open('w', encoding='utf-8-sig', newline='') as stream:
    writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
lines = ['# M4小规模：历史ALNS与当前强化精确模型', '',
    '仅整理已保存记录，没有新增算例实验。ALNS为2026-10-23的60秒预算，精确为2026-10-30的300秒上限，均种子42、串行、从原始输入冷启动。',
    '两版数据目录名称不同；逐例核对网络、订单、车队、日历和费用共45份冻结物理文件，SHA256全部一致。元数据和入口路径不重标为同一历史版本。', '',
    '| 算例 | ALNS完整调用/秒 | ALNS最终距离 | ALNS误差 | ALNS内部首次达最优/秒 | 精确证优完整调用/秒 | 最优距离 |',
    '| --- | ---: | ---: | ---: | ---: | ---: | ---: |']
for r in rows:
    first = '60秒内未达到' if r['alns_first_optimum_internal_seconds'] is None else f"{r['alns_first_optimum_internal_seconds']:.3f}"
    lines.append(f"| {r['case']} | {r['alns_total_seconds']:.3f} | {r['alns_objective']:.6f} | "
                 f"{r['alns_gap_percent']:.3f}% | {first} | {r['exact_total_proof_seconds']:.3f} | {r['exact_optimum']:.6f} |")
lines += ['',
    f"ALNS完整调用平均{summary['alns_mean_full_call_seconds']:.3f}秒，当前精确平均{summary['exact_mean_full_proof_seconds']:.3f}秒。",
    f"按已保存完整调用时间，精确在{len(summary['exact_shorter_full_call_cases'])}/9例更短；ALNS运行到固定60秒预算，精确会在完成证明后提前返回，两种停止条件不同。",
    f"ALNS达到已知最优值6/9，平均误差{summary['alns_mean_gap_percent']:.3f}%，最大{summary['alns_max_gap_percent']:.3f}%；精确9/9原生证明最优且审计通过。", '',
    f"ALNS在命中最优值的6例中，内部首次达到平均{summary['alns_mean_first_optimum_internal_seconds_on_hits']:.3f}秒；另外3例在60秒内未达到，不计入该时间均值。",
    '内部首次命中时间从ALNS搜索入口计时；完整调用仍按60秒预算运行，没有读取参考值提前停机。该时间也不包含原生最优性证明。',
    '精确完整调用时间含加载、建模、求解及最终审计，两个时钟起点不同，不能把首次命中时间比直接当成同口径加速倍数。',
    '历史ALNS与当前精确记录来自不同日期和代码版本，未控制当时系统负载；这是一组历史参考，不是本次同步性能试验，也不是多种子稳定性结论。', '',
    '[逐例CSV](comparison.csv) · [汇总](summary.json) · [输入和证据核验](verification.json)', '']
(FOLDER/'report.md').write_text('\n'.join(lines), encoding='utf-8')
print(json.dumps(summary, ensure_ascii=False, indent=2))
