"""Record the bounded exploratory trial without altering frozen evidence."""
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent


def read(name):
    return json.loads((HERE / name / 'summary.json').read_text(encoding='utf-8'))


control = read('M03_affordable1')
candidate = read('M03_affordable2')
assert control['independent_audit_valid'] and candidate['independent_audit_valid']
assert not control.get('experimental_affordable_trips')
pruning = candidate['experimental_affordable_trips']
assert pruning['applied'] and pruning['sites']['S1']['native_status'] == 2
assert control['status'] == candidate['status'] == 'TIME_LIMIT'
files = ['try_case.py', 'affordable_trips.py']
for folder in ('M03_affordable1', 'M03_affordable2'):
    files += [str(p.relative_to(HERE)) for p in (HERE / folder).glob('*') if p.is_file()]
evidence = {
    'scope': 'exploratory_only_not_merged_into_production',
    'seed': 42, 'threads': 1, 'budget_each_seconds': 180,
    'control': {key: control[key] for key in ('objective', 'best_bound', 'mip_gap', 'wall_seconds')},
    'candidate': {key: candidate[key] for key in ('objective', 'best_bound', 'mip_gap', 'wall_seconds')},
    'pruning': pruning,
    'hashes': {name: hashlib.sha256((HERE / name).read_bytes()).hexdigest() for name in files},
}
(HERE / 'affordable_trip_evaluation.json').write_text(
    json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')
lines = [
    '# 当前可行解推导的趟位削减：探索性短试验', '',
    '未合并到默认生产求解入口。默认版本仍为已完成19例跨规模验证的网络加强版。', '',
    '范围：仅M03，种子42、单线程、每次180秒；不是预先登记的正式版本对照，也没有多种子验证。',
    '第一次试验因读取尚未提交的NumStart而没有实际启用削减，因此仅记录为未削减控制；'
    '提交模型更新后第二次确实应用削减。保留这两次运行，不删除不利结果。', '',
    '| 运行 | 总秒数 | 可行成本 | 全局下界 | GAP |',
    '| --- | ---: | ---: | ---: | ---: |',
]
for label, row in [('未削减', control), ('应用削减', candidate)]:
    lines.append(f'| {label} | {row["wall_seconds"]:.3f} | {row["objective"]:.6f} | '
                 f'{row["best_bound"]:.6f} | {100*row["mip_gap"]:.4f}% |')
lines += ['',
    '辅助LP从本次新生成且独立审计通过的可行解出发，得到最大趟数松弛值12.325662，'
    '保守向上取整为13。结合已有车辆趟数排序固定31个候选趟位；'
    '预处理后由28696行、13391列、8446个二元变量降为18323行、9414列、4624个二元变量。',
    '根LP求解时间由19.34秒降为6.62秒，但180秒末可行成本和GAP没有改善。'
    '当前证据支持缩小表达规模，尚不支持启用后整体求解更快，因此没有继续扩大该试验。', '',
    '两次完整解均通过独立成本和两周期审计，两次均未证明最优。'
    '辅助LP及完整主模型日志、原结果、削减元数据和文件哈希均保留。', '',
    '该削减只能声称保留原最优解：使用当前可行成本作为上界，可能移除更差的原可行解。'
    '它不属于保留全部原可行域的等价表达；若后续采用，需要单独的最优性保持证明和边界测试。', '',
]
(HERE / 'affordable_trip_evaluation.md').write_text('\n'.join(lines), encoding='utf-8')
print(json.dumps({key: evidence[key] for key in ('scope', 'control', 'candidate')}, ensure_ascii=False))
