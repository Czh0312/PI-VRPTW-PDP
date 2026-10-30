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
assert row['budget'] == 300 and row['seed'] == 42
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
    scope='One medium case, one seed, one 300-second call; no extra cases or regression reruns')
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
    '只运行强化版一次，种子42、单线程、300秒总预算，compact＋自由选仓载荷流＋因果冲突／时间联系＋车辆对称约束。',
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
    '总耗时计入输入加载、建模、主MILP及返回处理；有可行解时还计入日程归一化和独立审计。本次无整数解，未执行路线审计。解释器启动、环境预检、源码冻结与事后哈希核验另计。',
    '准备工作从300秒搜索预算扣除，提取与审计可能带来少量尾部开销。TIME_LIMIT的实际耗时不等于证优所需时间。',
    '最优性标记仅来自完整原业务整数时间MILP原生状态和独立审计；无可行解或限时均不解释为不可行。',
    '这是一个算例、一个种子的一次结果，没有追加预算、案例或种子，也没有在测试中修改数学模型。', '',
    '[完整结果](network/result.json) · [运行摘要](network/row.json) · [协议与冻结哈希](network/protocol.json) · [核验](verification.json) · [原生日志](run.console.log)', '']
(FOLDER/'report.md').write_text('\n'.join(lines), encoding='utf-8')
print(json.dumps(dict(case='M01', status=row['status'], optimal=row['proven_optimal'],
    valid=row['valid'], objective=row['objective'], bound=row['bound'], gap=row['gap'],
    seconds=row['total_seconds']), ensure_ascii=False))
