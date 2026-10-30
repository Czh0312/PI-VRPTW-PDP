"""Verify the two S02 records and publish their measured comparison."""
import csv
import hashlib
import json
import math
from pathlib import Path
import xml.etree.ElementTree as ET
import zipfile

FOLDER = Path(__file__).resolve().parent
ROOT = FOLDER.parents[2]


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')


def sha(data):
    return hashlib.sha256(data).hexdigest()


rows, protocols, evidence = [], [], []
for mode in ('baseline', 'network'):
    directory = FOLDER/mode
    row, protocol = read(directory/'row.json'), read(directory/'protocol.json')
    assert row['case'] == 'S02' and row['seed'] == 42 and row['budget'] == 300
    assert row['status'] == 'OPTIMAL' and row['proven_optimal'] and row['valid'] and row['periodic_valid']
    assert row['solver_info']['solver_config']['threads'] == 1
    assert not row['solver_info']['mip_start_supplied']
    assert not row['solver_info']['solver_config']['custom_callback']
    assert row['solver_info']['proof_scope'] == 'full_model_4_integer_time_MILP'
    assert row['gap'] <= 1e-7
    for name, digest in protocol['input_sha256'].items():
        assert sha((directory/'inputs'/name).read_bytes()) == digest, name
    with zipfile.ZipFile(directory/'source.zip') as archive:
        for name, digest in protocol['source_sha256'].items():
            assert sha(archive.read(name)) == digest, name
    rows.append(row)
    protocols.append(protocol)
    evidence.append(dict(mode=mode, source_archive_sha256=sha((directory/'source.zip').read_bytes()),
                         result_sha256=sha((directory/'result.json').read_bytes()),
                         audit_sha256=sha((directory/'audit.json').read_bytes())))

assert protocols[0]['input_sha256'] == protocols[1]['input_sha256']
assert math.isclose(rows[0]['objective'], rows[1]['objective'], abs_tol=1e-7, rel_tol=1e-9)
changed = sorted(name for name in protocols[1]['source_sha256']
                 if protocols[0]['source_sha256'].get(name) != protocols[1]['source_sha256'][name])
for name, digest in protocols[1]['source_sha256'].items():
    if name.startswith('models/routing/model_4/exact/'):
        assert sha((ROOT/name).read_bytes()) == digest, name
case = read(FOLDER/'network/inputs/case.json')
for name, key in [('data.csv', 'data'), ('routing_config.json', 'routing_config'),
                  ('economic_config.json', 'economic_config'), ('dataset_metadata.json', 'dataset_metadata'),
                  ('orders.json', 'orders'), ('operating_policy.json', 'operating_policy'),
                  ('parameter_contract.json', 'parameter_contract')]:
    if key in case:
        # The network snapshot extension is csv; its descriptive key is data.
        snapshot = 'data.csv' if key == 'data' else name
        assert sha((ROOT/case[key]).read_bytes()) == protocols[1]['input_sha256'][snapshot]
suite = ET.parse(FOLDER/'tests.xml').getroot().find('testsuite')
tests = {key: int(suite.attrib.get(key, 0)) for key in ('tests', 'failures', 'errors', 'skipped')}
assert tests == dict(tests=38, failures=0, errors=0, skipped=0)
speedup = rows[0]['total_seconds']/rows[1]['total_seconds']
saved = 100*(1-rows[1]['total_seconds']/rows[0]['total_seconds'])
verification = dict(case='S02', dataset=case['dataset_version'], performance_runs=2,
    distinct_cases=1, experiment_seeds=[42], tests=tests,
    selected_test_modules=['tests/routing/test_model4_network_strengthening.py',
                           'tests/routing/test_model4_strengthening.py'],
    identical_inputs=True, matching_native_optima=True, independent_two_cycle_audits=True,
    source_archives_verified=True, network_m4_source_matches_workspace=True,
    changed_source_paths=changed, evidence=evidence,
    concurrent_shared_changes_review=dict(
        evidence='concurrent_changes.patch',
        finding='Shared-file differences add M9 support; inspected changes do not alter the M4 execution branches. '
                'The MultiBatchInstance class, M4 orders, physical audit, precheck and timing implementations are unchanged.'),
    speedup=speedup, time_reduction_percent=saved)
write(FOLDER/'verification.json', verification)
fields = ['mode', 'case', 'seed', 'budget', 'status', 'objective', 'bound', 'gap',
          'total_seconds', 'build_seconds', 'mip_seconds', 'nodes', 'mip_work', 'variables', 'constraints']
with (FOLDER/'summary.csv').open('w', encoding='utf-8-sig', newline='') as stream:
    writer = csv.DictWriter(stream, fieldnames=fields)
    writer.writeheader()
    for row in rows:
        info = row['solver_info']
        writer.writerow({**{key: row[key] for key in fields[:9]},
            'build_seconds': info['build_time'], 'mip_seconds': info['mip_runtime'],
            'nodes': info['node_count'], 'mip_work': info['mip_work'],
            'variables': info['variables'], 'constraints': info['constraints']})
lines = ['# M4参考M3/M8的网络表达加速：S02单例对照', '',
    '2026-10-30。当前统一数据formal_benchmark27_uniform_7_20_50_v1，小规模S02，7顾客。',
    '只测试这一个正式算例；两版各从原始输入重新求解，种子42、单线程、每版总预算300秒，串行执行。', '',
    '| 版本 | 最优距离 | 总耗时/秒 | 建模/秒 | 主MILP/秒 | 分支节点 | 状态 |',
    '| --- | ---: | ---: | ---: | ---: | ---: | --- |']
for label, row in zip(('原默认expanded冷启动', 'compact＋载荷流＋先后关系＋车辆排序'), rows):
    info = row['solver_info']
    lines.append(f"| {label} | {row['objective']:.9f} | {row['total_seconds']:.3f} | "
                 f"{info['build_time']:.3f} | {info['mip_runtime']:.3f} | {int(info['node_count'])} | 已证明最优 |")
lines += ['', f'本次总时间比为{speedup:.3f}倍，耗时减少{saved:.2f}%。两版目标相同，GAP约0，独立完整业务及连续两周期重放均通过。', '',
    '总时间从输入加载前开始，包含加载、预检条件、建模、优化、提取、归一化和独立审计；解释器启动与项目环境/许可预检另计。',
    '没有输入历史可行方案、外部下界、参考最优值、MIP start或求解回调。最优性来自完整原业务整数时间MILP的原生OPTIMAL状态。', '',
    '迁移M3的配送/回收弧流和送取因果冲突。针对自由选仓，载荷流图跨全部候选仓，物理边界弧仍按车辆和趟次保留。',
    'M8的首任务排序只用于M4趟数相同的车辆，避免与已有的趟数排序冲突。没有使用M3固定分仓路线下界，或M8批次数/箱池缩域。',
    '新版同时改变压缩表达、流表达及有效约束，不能从本组数据拆分各组件的速度贡献。', '',
    '两次源码快照之间工作区另有M9支持的并行改动。逐项核对concurrent_changes.patch：共享文件变化只增加M9分支，',
    'M4所用的MultiBatchInstance、原订单/物理审计/预检/时间归一化实现保持；M4加载和作用域判断分支行为保持。',
    '完整差异路径保存在verification.json，没有将两版全部非M4源码宣称为逐字节一致。', '',
    '这是一个算例、一个实验种子的实测，不宣称九个小规模算例或其他种子均有相同收益。',
    '定向回归仅选择test_model4_network_strengthening.py与test_model4_strengthening.py：38通过、0失败、0错误、0跳过；没有运行全仓库套件。', '',
    '[逐次数据](summary.csv) · [核验与源码差异](verification.json) · [原版完整结果](baseline/result.json) · [新版完整结果](network/result.json)', '']
(FOLDER/'report.md').write_text('\n'.join(lines), encoding='utf-8')
print(json.dumps(dict(original_seconds=rows[0]['total_seconds'], network_seconds=rows[1]['total_seconds'],
                     speedup=speedup, time_reduction_percent=saved, tests=tests), ensure_ascii=False))
