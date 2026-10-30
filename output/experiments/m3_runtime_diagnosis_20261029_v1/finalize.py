"""Verify diagnostic evidence and summarize observed M3 runtime causes."""
from __future__ import annotations

from collections import Counter
import hashlib
import importlib.util
import json
from pathlib import Path
import xml.etree.ElementTree as ET

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


manifest = read(OUT / 'manifest.json')
for relative, expected in manifest['source_hashes'].items():
    assert sha(ROOT / relative) == expected, relative
baseline = read(ROOT / 'output/experiments/staged_comparison_27_seed42_20261024_v1/manifest.json')
matches = {}
for relative, expected in manifest['source_hashes'].items():
    normalized = Path(relative).as_posix()
    if normalized.startswith('models/'):
        frozen_key = 'sources/current/' + normalized
        if frozen_key in baseline['source_hashes']:
            matches[normalized] = expected == baseline['source_hashes'][frozen_key]
assert matches and all(matches.values())
rows = read(OUT / 'rows.json')
bounds = read(OUT / 'route_partition_rows.json')
assert len(rows) == 9 and len(bounds) == 9
for row in bounds:
    for field in ['source_hashes', 'input_hashes']:
        for relative, expected in row[field].items():
            assert sha(ROOT / relative) == expected, relative
    assert sha(ROOT / row['witness_path']) == row['witness_sha256']
    assert row['independent_original_audit_valid'] and row['periodic_replay_valid']
    assert not row['history_supplied_to_solver']
    assert row['lower_bound'] <= row['audited_upper_bound'] + 1e-6

spec = importlib.util.spec_from_file_location('m3_route_partition_probe', OUT / 'route_partition_probe.py')
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)
reseed = [probe.run(case, seed=101) for case in ['S01', 'S06', 'S08']]
assert all(row['closes_with_historical_audited_plan'] for row in reseed)

suites = ET.parse(OUT / 'validation.xml').getroot().findall('testsuite')
counts = {name: sum(int(s.attrib.get(name, 0)) for s in suites)
          for name in ['tests', 'failures', 'errors', 'skipped']}
assert counts == dict(tests=17, failures=0, errors=0, skipped=0)
validation = dict(selected_pytest_counts=counts,
    selected_scope=['tests/routing/test_model3_cost_objective.py',
                    'tests/routing/test_model8_shared_relaxation_research.py::test_complete_trip_masks_equal_independent_permutation_oracle'],
    native_diagnostic_calls=len(rows),
    native_diagnostic_statuses=dict(Counter(row['info']['status'] for row in rows)),
    native_audited_feasible=sum(row['independent_audit_valid'] for row in rows),
    fresh_input_only_partition_bounds=len(bounds),
    closed_with_audited_original_plan=[r['case'] for r in bounds if r['closes_with_historical_audited_plan']],
    reseed_101_checks=reseed, production_sources_unchanged=True,
    matches_historical_frozen_M3_sources=matches, entire_pytest_suite_run=False)
(OUT / 'validation.json').write_text(json.dumps(validation, ensure_ascii=False, indent=2), encoding='utf-8')

lines = ['# 模型3小规模求解长尾：代码、日志与受控表达诊断', '',
    '2026-10-29。主要原因是旧逐弧、车辆趟次索引表达产生了较大的搜索空间与偏弱的下界；'
    '模型八近期秒级记录则使用完整车次枚举、加强主问题和已有审计可行方案。两组历史时间不属于同条件的速度实验。', '',
    '**原生慢例证据。** S01 的 7 个订单均在 S0，产生 14 个配送/回收任务、26 个二级候选趟位、'
    '4,030 个二级弧变量，完整模型为 4,780 个变量，其中 4,599 个二进制变量。最终方案只有 3 趟二级车。'
    '这 26 个候选趟位是合法性所需的安全上限，不能直接按已知最优方案砍成 3 趟。', '',
    '历史 S01 建模 0.3048 秒；Gurobi 根松弛目标约 42.00239，原问题最优值 45.378165。'
    '第 53 秒找到最终最优方案，之后继续搜索约 114 秒，第 167 秒才闭合最优性证明。'
    '因此该慢例同时存在初解搜索和证明负担，建模不是主要耗时。S06、S08 也是全部 7 单集中在一个仓；'
    '其余 6 例均按 4+3 或 3+4 分在两仓。相同总订单数下，单仓 14 任务带来更多组合。', '',
    '**本轮冷启动表达对照。** 原始模型3、成本、订单、整数时间与物理约束均保留；'
    '单线程、种子42、串行，每臂20秒覆盖建模和优化；下表墙钟另含提取与独立审计。'
    '未使用历史上下界、初解、变量提示或回调。当前模型3源码与阶段实验冻结版本逐文件一致。', '',
    '| 算例 | 表达 | 二进制变量 | 根松弛目标 | 总秒 | 状态 | 可行解成本 | 最终下界 |',
    '|---|---|---:|---:|---:|---|---:|---:|']
for row in rows:
    info, stats = row['info'], row['model_stats']
    cost = f"{info['objective']:.6f}" if info['objective'] is not None else '无可行解'
    lines.append(f"| {row['case']} | {row['variant']} | {info['binary_variables']} | "
                 f"{stats['root_relaxation']:.6f} | {info['wall_seconds']:.3f} | {info['status']} | {cost} | {info['best_bound']:.6f} |")
lines += ['', '9 次原生调用：4 次最优、5 次限时，8 次有独立审计可行方案，1 次限时无可行解。'
          '所有本轮已证最优值都与历史最优值一致。expanded_indicators 仅将二级线性联系改回指示表达，'
          '三例均未在20秒内证明；这组结果不支持靠改指示约束或调参数消除长尾。', '',
          'compact 把 S01 二进制变量降为1,297个，矩阵非零数从35,556降为18,961，'
          '但根松弛只从42.00239升为42.11931，20秒仍未闭合。S06/S08 本次均能证明最优，'
          'compact 的观察耗时优于 expanded，但这里只做单种子单次对照，不宣称稳定加速倍数。', '',
          '**完整车次下界诊断。** 复用模型八已经有独立排列枚举校验的物理车次枚举器，'
          '对模型3从原始输入重新枚举全部放松车次任务集合，再求整数集合划分。一级运输使用'
          '有向距离最短路闭包上的访问集合回路下界。固定箱池和本组费用按原口径保留。'
          '此松弛释放供货同步、回收截止和车辆日程；任何原可行方案都可投影到它，所以只能给下界。'
          '保存每个原方案的车次集合投影，并核对每列距离不超过原车次距离。', '',
          '每个下界求解结束后才读取并独立审计历史原业务方案，未把历史最优值、上界、下界或方案提供给求解器。'
          '三例上下界闭合，直接定位到旧逐弧表达的下界短板。下表耗时含原始加载、重新枚举、建模、'
          '下界优化和历史方案的末端独立审计，未包含从零寻找原可行方案。', '',
          '| 算例 | 车次列数 | 原始输入下界 | 审计可行成本 | 差额 | 本轮秒 | 闭合 |',
          '|---|---:|---:|---:|---:|---:|---|']
for row in bounds:
    lines.append(f"| {row['case']} | {row['route_columns']} | {row['lower_bound']:.9f} | "
                 f"{row['audited_upper_bound']:.9f} | {max(0,row['gap']):.9f} | "
                 f"{row['total_including_loading_and_witness_audit_seconds']:.3f} | "
                 f"{'是' if row['closes_with_historical_audited_plan'] else '否'} |")
lines += ['', '新种子101重新生成三个慢例的车次库和下界，三例再次闭合。其余六例这个最小松弛仍有正差额，'
          '不能据此报告九例都已通过新方法证明最优，也不能把松弛解当作原问题可行调度。', '',
          '**改进方向。** 模型3优先接入完整车次层的强下界或等价车次主问题；对未闭合的组合保留完整原调度检验。'
          '寻找原可行方案的冷启动耗时应单独实测，并与模型八统一启动信息、时间范围、输入和机器负载。'
          '本轮没有接入生产默认求解器，只在独立诊断目录生成脚本和证据。', '',
          '**验证范围。** 项目运行器预检 READY，实际 Gurobi 11.0.3 整数求解通过。'
          '选定 tests/routing/test_model3_cost_objective.py 全模块及车次枚举独立排列 oracle 单项：'
          '17 passed、0 failed、0 errors、0 skipped；没有运行全仓库测试。'
          '另有上述9次原生诊断、9次下界与3次新种子复核，未把这些求解或方案审计计作 pytest 测试。', '',
          '证据：rows.json、route_partition_rows.json、validation.json、validation.xml；'
          '每臂原生日志与解见对应 Sxx_*/ 子目录；可复核脚本为 diagnose.py、route_partition_probe.py、finalize.py。', '']
(OUT / 'report.md').write_text('\n'.join(lines), encoding='utf-8')
files = {p.relative_to(OUT).as_posix(): sha(p) for p in OUT.rglob('*')
         if p.is_file() and '__pycache__' not in p.parts and p.name != 'evidence_manifest.json'}
(OUT / 'evidence_manifest.json').write_text(json.dumps(dict(files=files), ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(dict(pytest=counts, closed_cases=validation['closed_with_audited_original_plan'],
                      sources_verified=len(matches), native_statuses=validation['native_diagnostic_statuses']), ensure_ascii=False))
