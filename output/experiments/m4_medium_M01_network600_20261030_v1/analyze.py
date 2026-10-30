"""Verify and report the one-case M4 medium cold-start experiment."""
import hashlib
import json
import math
from pathlib import Path
import zipfile

FOLDER = Path(__file__).resolve().parent
RUN = FOLDER/'network'
ROOT = FOLDER.parents[2]


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def digest(data):
    return hashlib.sha256(data).hexdigest()


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')


protocol, row, result, audit = [read(RUN/name) for name in ('protocol.json', 'row.json', 'result.json', 'audit.json')]
info = row['solver_info']
config = info['solver_config']
assert row['case'] == 'M01' and row['scale'] == 'medium' and row['mode'] == 'network'
assert row['customer_count'] == row['order_count'] == 20
assert row['budget'] == 600 and row['seed'] == 42
assert config['seed'] == 42 and config['threads'] == 1 and config['formulation'] == 'compact'
assert config['network_strengthening']['applied']
assert config['network_strengthening']['resource_flows']['applied']
assert not config['mip_start'] and not info['mip_start_supplied']
assert not config['custom_callback'] and not config['external_certificate']
assert info['proof_scope'] == 'full_model_4_integer_time_MILP'
assert result['info']['status'] == row['status'] and result['info']['proven_optimal'] == row['proven_optimal']
assert audit.get('valid', False) == row['valid']
if row['valid']:
    assert audit['periodic_replay']['valid']
    assert math.isclose(audit['objective'], row['objective'], abs_tol=1e-6, rel_tol=1e-9)
    if row['bound'] is not None:
        assert row['bound'] <= row['objective']+1e-5
if row['proven_optimal']:
    assert row['status'] == 'OPTIMAL' and row['valid']
    assert row['gap'] <= 1e-7
for name, value in protocol['snapshot_sha256'].items():
    assert digest((RUN/'snapshot'/name).read_bytes()) == value, name
with zipfile.ZipFile(RUN/'source_inputs.zip') as archive:
    for name, value in protocol['snapshot_sha256'].items():
        assert digest(archive.read(name)) == value, name
assert digest((RUN/'source_inputs.zip').read_bytes()) == protocol['source_inputs_zip_sha256']
prior = read(ROOT/'output/experiments/m4_network_transfer_S02_20261030_v1/network/protocol.json')
m4_hashes = {name: value for name, value in protocol['source_sha256'].items()
             if name.startswith('models/routing/model_4/exact/')}
assert all(prior['source_sha256'][name] == value for name, value in m4_hashes.items())
loaded = read(RUN/'loaded_sources.json')
assert 'models.routing.model_4.exact.formulation' in loaded
verification = dict(case='M01', scale='medium', customers=20, formal_calls=1, experiment_seeds=[42],
    runtime_preflight='READY; Gurobi 11.0.3; actual integer smoke solve passed',
    source_inputs_verified=True, frozen_module_paths_verified=True, m4_source_matches_small_trials=True,
    cold_start=True, supplied_initial_solution=False, historical_bound=False, callback=False,
    status=row['status'], proven_optimal=row['proven_optimal'], audited_feasible=row['valid'],
    periodic_audit=row['periodic_valid'],
    audit_state='not_run_no_incumbent' if info.get('sol_count', 0) == 0 else 'completed',
    source_inputs_zip_sha256=protocol['source_inputs_zip_sha256'],
    result_sha256=digest((RUN/'result.json').read_bytes()), audit_sha256=digest((RUN/'audit.json').read_bytes()),
    scope='One medium case, one seed, one 600-second call; no extra cases or regression reruns')
write(FOLDER/'verification.json', verification)
fmt = lambda value: '—' if value is None else f'{value:.6f}'
gap = '—' if row['gap'] is None else f"{100*row['gap']:.4f}%"
outcome = ('已找到并证明原问题最优解' if row['proven_optimal']
           else '已返回审计通过的可行方案，但未证明最优' if row['valid']
           else '本次预算内未返回可行方案；不能据此判定不可行')
audit_text = str(row['valid']) if info.get('sol_count', 0) else '未执行（无整数可行解）'
periodic_text = str(row['periodic_valid']) if info.get('sol_count', 0) else '未执行（无整数可行解）'
lines = ['# M4强化版：M01中规模单例冷启动', '',
    '2026-10-30。formal_benchmark27_uniform_7_20_50_v1，M01，20顾客。',
    '只运行强化版一次，种子42、单线程、600秒总预算，compact＋自由选仓载荷流＋因果冲突／时间联系＋车辆对称约束。',
    '源码与小规模测试的M4完全相同，所有源码与原始输入冻结后独立执行。未输入历史路线、最优值、外部界、ALNS初解或求解回调。', '',
    f'结论：**{outcome}**。', '',
    '| 指标 | 结果 |', '| --- | ---: |',
    f"| 原生状态 | {row['status']} |", f"| 最优性证明 | {row['proven_optimal']} |",
    f"| 可行总距离 | {fmt(row['objective'])} |", f"| 全局下界 | {fmt(row['bound'])} |",
    f'| GAP | {gap} |', f"| 总耗时/秒 | {row['total_seconds']:.3f} |",
    f"| 输入加载/秒 | {row['load_seconds']:.3f} |", f"| 建模及预处理/秒 | {info.get('build_time', 0):.3f} |",
    f"| 原生主MILP/秒 | {info.get('mip_runtime', 0):.3f} |", f"| 分支节点 | {int(info.get('node_count', 0))} |",
    f"| 变量 | {info.get('variables')} |", f"| 约束（含一般约束） | {info.get('constraints')} |",
    f"| 完整业务审计 | {audit_text} |", f"| 两周期审计 | {periodic_text} |", '',
    '总耗时计入输入加载、建模、主MILP及返回处理；有可行解时还计入日程归一化和独立审计。解释器启动、环境预检、源码冻结与事后哈希核验另计。',
    '准备工作从600秒搜索预算扣除，提取与审计可能带来少量尾部开销。TIME_LIMIT的实际耗时不等于证优所需时间。',
    '最优性标记仅来自完整原业务整数时间MILP原生状态和独立审计；无可行解或限时均不解释为不可行。',
    '本轮仅运行M01、种子42的一次600秒独立冷启动，未在求解中修改参数或数学模型。', '',
    '[完整结果](network/result.json) · [运行摘要](network/row.json) · [协议与冻结哈希](network/protocol.json) · [核验](verification.json) · [原生日志](run.console.log)', '']
(FOLDER/'report.md').write_text('\n'.join(lines), encoding='utf-8')
print(json.dumps(dict(case='M01', status=row['status'], optimal=row['proven_optimal'],
    valid=row['valid'], objective=row['objective'], bound=row['bound'], gap=row['gap'],
    seconds=row['total_seconds']), ensure_ascii=False))

# Compare only after solving; no historical solution or bound enters the solver.
previous_folder = ROOT/'output/experiments/m4_medium_M01_network300_20261030_v1'
previous_protocol = read(previous_folder/'network/protocol.json')
previous_row = read(previous_folder/'network/row.json')
assert previous_protocol['source_sha256'] == protocol['source_sha256']
assert previous_protocol['input_sha256'] == protocol['input_sha256']
import re
fingerprint = re.search(r'Model fingerprint: (\S+)', (FOLDER/'run.console.log').read_text(encoding='utf-8-sig'))
previous_fingerprint = re.search(r'Model fingerprint: (\S+)', (previous_folder/'run.console.log').read_text(encoding='utf-8-sig'))
assert fingerprint and previous_fingerprint and fingerprint.group(1) == previous_fingerprint.group(1)
comparison = dict(case='M01', seed=42, threads=1, independent_cold_starts=True,
    identical_source_hashes=True, identical_input_hashes=True, model_fingerprint=fingerprint.group(1),
    runs=[{key: item[key] for key in ('budget', 'status', 'proven_optimal', 'valid', 'objective',
                                    'bound', 'gap', 'total_seconds')}
          for item in (previous_row, row)])
write(FOLDER/'comparison.json', comparison)
verification.update(identical_source_and_inputs_to_300s=True, same_model_fingerprint=fingerprint.group(1),
                    continuation_of_300s=False)
write(FOLDER/'verification.json', verification)
with (FOLDER/'report.md').open('a', encoding='utf-8') as stream:
    stream.write('\n## 与300秒试验对照\n\n')
    stream.write('600秒从头独立冷启动，未续接此前300秒的搜索树、初解或下界。两次源码、输入哈希及完整MILP指纹均相同，仅预算改变。\n\n')
    stream.write('| 预算/秒 | 实际耗时/秒 | 状态 | 可行距离 | 下界 | GAP |\n| ---: | ---: | --- | ---: | ---: | ---: |\n')
    for item in (previous_row, row):
        item_gap = '—' if item['gap'] is None else f"{100*item['gap']:.4f}%"
        stream.write(f"| {item['budget']:.0f} | {item['total_seconds']:.3f} | {item['status']} | "
                     f"{fmt(item['objective'])} | {fmt(item['bound'])} | {item_gap} |\n")

