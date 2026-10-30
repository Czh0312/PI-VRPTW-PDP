"""Read Windows cProfile paths correctly; keep this a diagnostic, not a benchmark."""
import json
from pathlib import Path
import pstats
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))
from models.common.cycle import load_instance
from models.common.cycle.audit import audit_solution
from tools.configuration.loader import load_routing_case


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


rows = []
for case in ['S02', 'S03', 'S09']:
    folder = HERE / 'candidate' / f'm8_{case}'
    stats = pstats.Stats(str(folder / 'solve.prof'))
    functions = []
    for (filename, line, name), (primitive, calls, own, cumulative, callers) in stats.stats.items():
        normalized = filename.replace('\\', '/')
        if '/models/' in normalized:
            functions.append(dict(file=normalized, line=line, name=name, calls=calls,
                                  own_seconds=own, cumulative_seconds=cumulative))
    joint = next(r for r in functions if r['name'] == '__call__'
                 and r['file'].endswith('model_8/alns/evaluator.py') and r['line'] > 380)
    raw = next(r for r in functions if r['name'] == 'evaluate' and r['file'].endswith('model_8/alns/evaluator.py'))
    audit = next(r for r in functions if r['name'] == '_audit' and r['file'].endswith('cycle/audit.py'))
    result = read(folder / 'result.json')
    p = load_instance(*load_routing_case(ROOT / f'data/formal_benchmark27_uniform_7_20_50_v1/model_8/small/{case}/case.json', use_pool_config=False))
    verification = audit_solution(p, result)
    assert verification['valid'] and verification['periodic_replay']['valid']
    optimum = read(ROOT / f'output/experiments/m8_all_small_rerun_20261029_v1/runs/{case}/s42/summary.json')['audited_upper_bound']
    rows.append(dict(case=case, objective=verification['objective'], offline_reference_optimum=optimum,
        offline_gap_percent=100*(verification['objective']-optimum)/optimum,
        total_profile_seconds=stats.total_tt, joint_evaluator_calls=joint['calls'],
        joint_evaluator_cumulative_seconds=joint['cumulative_seconds'],
        joint_evaluator_fraction_of_profile=joint['cumulative_seconds']/stats.total_tt,
        raw_decoder_calls=raw['calls'], physical_audit_calls=audit['calls'],
        physical_audit_cumulative_seconds=audit['cumulative_seconds'],
        independently_audited_original_domain=True, two_cycle_replay_valid=True))
    (folder / 'normalized_functions.json').write_text(json.dumps(sorted(functions,
        key=lambda r: -r['cumulative_seconds']), ensure_ascii=False, indent=2), encoding='utf-8')
(HERE / 'diagnosis.json').write_text(json.dumps(dict(kind='three_case_10s_profile_not_performance_comparison',
    rows=rows, exact_references_used_only_after_search=True, alns_modified=False,
    full_suite_run=False, path_handling='Read .prof with Windows paths normalized; original helper filters with POSIX separators.'),
    ensure_ascii=False, indent=2), encoding='utf-8')
lines = ['# 模型八 ALNS 与精确加速的关系：初步检查', '',
    '2026-10-29。仅检查现有实现并运行三个每例 10 秒的剖析样本，未修改 ALNS 或生产默认参数。', '',
    '新增精确主问题约束并未改变业务问题：完整模型原本已要求同一趟回库的空箱同批回收。'
    'ALNS 解码器也将同一 E2 trip 的全部回收量，按共同 return_complete 对应的第一个 cutoff 归批；'
    '有配送的车次从实际供货后发车。因此需要同步核对语义，但无需简单复制主问题割。', '',
    '## 已观察的热点', '',
    '| 算例 | 剖析秒 | JointEvaluator 累计秒 | 占比 | 解码次数 | 物理审计次数 |',
    '|---|---:|---:|---:|---:|---:|']
for row in rows:
    lines.append(f'| {row["case"]} | {row["total_profile_seconds"]:.3f} | '
                 f'{row["joint_evaluator_cumulative_seconds"]:.3f} | '
                 f'{100*row["joint_evaluator_fraction_of_profile"]:.1f}% | '
                 f'{row["raw_decoder_calls"]} | {row["physical_audit_calls"]} |')
lines += ['', '累计调用时间相互包含，不能把 JointEvaluator、解码、审计的时间再相加。'
    'cProfile 本身会降低迭代速度，这三个短样本只用于定位热点，不能作为正式求解质量或加速倍数。'
    '原分析工具使用 POSIX 分隔符过滤函数名，其 Windows decoder_calls/physical_audits 字段不适用；'
    '本报告直接从原始 solve.prof 归一化路径后重新统计。', '',
    '三个最终方案均在原始 K=1…7 实例上重新审计、费用复算和两周期回放通过。'
    '没有运行 pytest；不把三次方案审计称为测试套件通过。', '',
    '## 后续优化应验证的内容', '',
    '1. 评价成本：针对相同固定分批/截点和相同车次输入复用可证明等价的解码、投影与中间计算；'
    '保留最终独立业务审计，验证缓存隔离、失效及结果一致性。',
    '2. 搜索质量：联合调整分批、车次与截点，增加清洗后空箱能在下一批装载前释放的机会；'
    '在真实总成本下评估箱池节省与新增运输的取舍。',
    '3. 验收：在相同总预算、多个新种子下配对比较原版与候选，记录首次可行、'
    '达到给定误差或参考最优的时间、最终误差和可行率。原小例精确最优值仅用于事后评分，'
    '不作为 ALNS 初解、下界或停止条件。', '',
    'ALNS 达到已知最优成本表示本次命中最优方案；全局最优性的证明仍来自独立精确证书。'
    '本次尚未实现或验证候选加速版本，不宣称已经提高 ALNS 效率。', '']
(HERE / 'report.md').write_text('\n'.join(lines), encoding='utf-8')
print(json.dumps(rows, ensure_ascii=False, indent=2))
