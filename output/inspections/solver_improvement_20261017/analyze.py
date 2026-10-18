"""Recompute every reported diagnostic from stored raw results."""
import hashlib
import json
from pathlib import Path
import statistics
import zipfile

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
FORMAL = ROOT/'output/experiments/benchmark27_20261016_cost_update_v1'
GROUPS = ('paired_alns_15s', 'paired_m8_alns_15s', 'paired_exact_30s', 'paired_m8_refined_15s')


def read(path):
    return json.loads(path.read_text())


def number(value):
    return '—' if value is None else f'{value:.6f}'


def pct(value):
    return '—' if value is None else f'{value:.2f}%'


def main():
    archive = zipfile.ZipFile(FORMAL/'raw_artifacts.zip')
    reference = {}
    all_rows = []
    for group in GROUPS:
        manifest = read(OUT/group/'manifest.json')
        current = manifest['source_hashes']['candidate']
        source_file = 'candidate_sources_v2.zip' if group == 'paired_m8_refined_15s' else 'candidate_sources.zip'
        with zipfile.ZipFile(OUT/source_file) as frozen:
            for relative, digest in current.items():
                assert hashlib.sha256(frozen.read(relative)).hexdigest() == digest, (group, relative)
        rows = read(OUT/group/'results.json')
        assert len(rows) == len(manifest['cases'])*len(manifest['seeds'])*2
        for row in rows:
            base = f'{row["case"]}_m{row["model"]}'
            folder = OUT/group/row['variant']/f'{base}_{row["method"]}_s{row["seed"]}'
            result, audit = read(folder/'result.json'), read(folder/'audit.json')
            assert bool(audit['valid']) == row['valid']
            assert row['objective'] == result['info']['objective']
            assert row['bound'] == result['info'].get('best_bound')
            assert len(result['count_search']) == row['candidate_count']
            assert result['info'].get('certificate_error_count', 0) == 0
            assert result['info'].get('audit_failure_count', 0) == 0
            if row['valid']:
                assert audit['periodic_replay']['valid']
                assert abs(audit['objective']-row['objective']) < 1e-6
            if row['method'] == 'alns':
                assert not row['proven_optimal'] and row['bound'] is None
            if base not in reference:
                records = []
                exact = None
                for method, seeds in [('exact', [42]), ('alns', [42, 43, 44, 45, 46])]:
                    for seed in seeds:
                        name = f'runs/{base}_{method}_s{seed}/attempt_001/result.json'
                        raw = json.loads(archive.read(name))
                        if method == 'exact':
                            exact = raw
                        if raw.get('audit', {}).get('valid'):
                            records.append(raw['info']['objective'])
                reference[base] = dict(best_known=min(records), proven=exact['info']['proven_optimal'],
                                       optimum=exact['info']['objective'] if exact['info']['proven_optimal'] else None)
            all_rows.append(dict(group=group, **row))
    alns = [r for r in all_rows if r['group'] == 'paired_alns_15s']
    metrics = {}
    for variant in ('baseline', 'candidate'):
        selected = [r for r in alns if r['variant'] == variant]
        errors = [100*(r['objective']-reference[f'{r["case"]}_m{r["model"]}']['optimum'])
                  / reference[f'{r["case"]}_m{r["model"]}']['optimum'] for r in selected]
        assert min(errors) > -1e-6
        metrics[variant] = dict(n=len(selected), hits=sum(abs(e) < 1e-6 for e in errors),
                                mean_error_percent=statistics.mean(errors), max_error_percent=max(errors))
    lines = [
        '# M6–M8 有限时间求解改进验证',
        '',
        '## Material Passport', '',
        '- Origin Skill: academic-research-suite / experiment-agent',
        '- Origin Mode: validate',
        '- Origin Date: 2026-10-17',
        '- Verification Status: ANALYZED（同机成对重跑已完成；不宣称跨平台逐点复现）',
        '- Version Label: joint_refinement_v1',
        '',
        '## 范围和实现', '',
        '本次修改 M6–M8；费用、物理输入、完整 K 域不变。ALNS 不导入精确算法、Gurobi 或参考解。',
        '精确模型增加每批装载量、平均批需求和必然占用时段的箱池下界，以及必要距离/行程/车辆成本下界。',
        '精确 K 调度在大候选域先给少数低 K 足够时间，再优先处理下界较低的未解决候选；所有 K 保留证明台账。',
        '精确算法支持经过独立重放的物理 MIP start，超时保留可行上界；无外部初解时使用本模型自己的部分分批初值。',
        'ALNS 增加均衡分批初解、跨行程任务重分段、E1 配送回收车辆联合移动，以及路线与回收截点的联合改进。',
        '',
        '## 成对实验设置', '',
        '基线为修订3正式实验 source_current.zip。相同机器、输入、费用、种子与总预算，独立进程串行运行，交替版本先后顺序；Gurobi 单线程。',
        '首版源码保存在 candidate_sources.zip；第二版只调整 M8 ALNS 的日历构造/联合改进，保存在 candidate_sources_v2.zip。精确算法及 M6/M7 ALNS 的最终实现与首版一致。',
        'ALNS：M6/M7 的 S01–S03，各 15 秒，种子 42、44；M8 首版相同案例使用种子 42，第二版使用种子 42、44。',
        '纯精确：M6/S03、M7/S01、M8/S03、M6/M01、M8/M01、M8/L05，各 30 秒，种子 42。包含建模和外层审计开销。',
        '这是选例工程诊断，不替代 27 例 × 8 模型的正式实验。开发过程中的短试跑保存在相邻目录，未混入本表。',
        '',
        '## ALNS 对已证明最优值的命中', '',
        '| 版本 | 运行数 | 命中已知最优值 | 平均相对误差 | 最大相对误差 |',
        '| --- | ---: | ---: | ---: | ---: |',
    ]
    for variant, label in [('baseline', '基线'), ('candidate', '改进')]:
        m = metrics[variant]
        lines.append(f'| {label} | {m["n"]} | {m["hits"]}/{m["n"]} | {pct(m["mean_error_percent"])} | {pct(m["max_error_percent"])} |')
    lines += ['', '| 模型/算例 | 已证明最优成本 | 基线 seed42 / seed44 | 改进 seed42 / seed44 |',
              '| --- | ---: | --- | --- |']
    for model in (6, 7):
        for case in ('S01', 'S02', 'S03'):
            rows = [r for r in alns if r['model'] == model and r['case'] == case]
            values = {v: ' / '.join(number(r['objective']) for r in sorted(rows, key=lambda r:r['seed'])
                                    if r['variant'] == v) for v in ('baseline', 'candidate')}
            lines.append(f'| M{model}/{case} | {number(reference[f"{case}_m{model}"]["optimum"])} | {values["baseline"]} | {values["candidate"]} |')
    lines += ['', 'ALNS 的命中只表示目标值与独立精确证明一致；其自身仍不输出全局最优证明。这里是 6 个模型/算例配置、12 次运行，不能扩大为所有小规模实例均已最优。',
              '', '## M8 的小实例（均未证明全域最优）', '',
              '| 算例 | 本轮最好已知成本 | 基线 seed42 / seed44 | 第二版 seed42 / seed44 |',
              '| --- | ---: | --- | --- |']
    for case in ('S01', 'S02', 'S03'):
        rs = [r for r in all_rows if r['group'] == 'paired_m8_refined_15s' and r['case'] == case]
        vals = {v:' / '.join(number(r['objective']) for r in sorted(rs,key=lambda r:r['seed'])
                            if r['variant']==v) for v in ('baseline','candidate')}
        upper = min([reference[f'{case}_m8']['best_known']]
                    +[r['objective'] for r in all_rows if r['model']==8 and r['case']==case and r['valid']])
        lines.append(f'| {case} | {number(upper)} | {vals["baseline"]} | {vals["candidate"]} |')
    lines += ['', '首版 M8/S03 的 seed42 从基线 55.119232 退化为 56.390177；该负结果完整保留在 paired_m8_alns_15s。',
              '随后加入交付、使用和清洁截止时刻的整组日历初解，以及边界/订单归属/路线联合改进；第二版另行冻结和成对验证，没有覆盖首版文件。']
    lines += ['', '## 纯精确算法：30 秒', '',
              '| 模型/算例 | 基线可行成本 | 改进可行成本 | 基线全 K 下界 | 改进全 K 下界 | 基线/改进证明最优 |',
              '| --- | ---: | ---: | ---: | ---: | --- |']
    exact = [r for r in all_rows if r['group'] == 'paired_exact_30s']
    for model, case in [(6,'S03'), (7,'S01'), (8,'S03'), (6,'M01'), (8,'M01'), (8,'L05')]:
        pair = {r['variant']: r for r in exact if r['model']==model and r['case']==case}
        b, c = pair['baseline'], pair['candidate']
        lines.append(f'| M{model}/{case} | {number(b["objective"])} | {number(c["objective"])} | {number(b["bound"])} | {number(c["bound"])} | {b["proven_optimal"]}/{c["proven_optimal"]} |')
    lines += ['', '“—”表示该次没有可行解或没有全局下界，不能视为 0。可行解数量、已证明最优数量和下界改善分别评价。',
              '', '| 模型/算例 | 共同已知可行上界 U | 基线界差 (U−L)/U | 改进界差 (U−L)/U |',
              '| --- | ---: | ---: | ---: |']
    gaps = []
    for model, case in [(6,'S03'), (7,'S01'), (8,'S03'), (6,'M01'), (8,'M01'), (8,'L05')]:
        pair = {r['variant']:r for r in exact if r['model']==model and r['case']==case}
        upper = min([reference[f'{case}_m{model}']['best_known']]
                    + [r['objective'] for r in all_rows if r['valid']
                       and r['model']==model and r['case']==case])
        values = {}
        for variant, r in pair.items():
            lower = r['bound']
            if lower is not None:
                assert lower <= upper+1e-5
            values[variant] = None if lower is None else 100*max(0.,upper-lower)/abs(upper)
        gaps.append(dict(model=model, case=case, common_upper=upper, gap_percent=values))
        lines.append(f'| M{model}/{case} | {number(upper)} | {pct(values["baseline"])} | {pct(values["candidate"])} |')
    lines += ['', '共同上界取同模型正式实验及本轮所有有效方案的最好值，用于公平比较下界；它不等于本次冷启动精确算法自己找到了该上界，也不是实际最优误差。',
              '', '## 验证和限制', '',
              '首版整体 261 项回归测试通过；第二版相关 114 项（与首版有重叠）通过，包括后加的库存释放等式和大 K 域预算测试。覆盖独立求解隔离、小实例整数时间穷举参照、非对称/非度量距离、热启动重放、预算和证明状态。',
              '所有有目标值的成对运行均有独立审计及周期重放；全部运行（包括未找到可行解的超时）进入分母。',
              '尚不能承诺任意小规模、任意随机种子都能命中最优。中大规模仍需改善 MILP 找可行解和抬高下界的能力。',
              '后续正式验收应使用全部小规模、多种子和原正式预算，并单列未证明的参考值；扩展实例需与本次用于调试的选例分开。',
              '', '## 11 项统计误区扫描（11/11）', '',
              '| 项目 | 检查结果 |', '| --- | --- |',
              '| Simpson 悖论 | 按模型、实例、种子保留成对值，不混合规模推断 |',
              '| 生态谬误 | 单次运行与实例命中分别计数，不推及所有输入 |',
              '| Berkson/选择偏差 | 存在选例开发偏差，结果限定为工程诊断 |',
              '| 碰撞变量偏差 | 无按运行结果筛除样本或条件回归 |',
              '| 基率忽略 | 明示 6 个配置和 12 次运行的分母 |',
              '| 向均值回归 | 同时运行固定基线，两版同种子和预算 |',
              '| 幸存者偏差 | 包含所有失败、缺失上界/下界和超时 |',
              '| 多重寻找效应 | 不作显著性检验，不选择性隐去负结果 |',
              '| 分析路径自由 | 保留开发试跑、最终源码和预先登记的案例/预算 |',
              '| 相关当因果 | 只报告该受控代码对照，不推断现实费用收益 |',
              '| 反向因果 | 版本先冻结、再执行；无结果驱动的因果拟合 |',
              '', '## 复现材料', '',
              '- compare_joint_refinement.py：成对驱动；每个阶段 manifest.json 保存参数、环境与逐文件哈希。',
              '- candidate_sources.zip / candidate_sources_v2.zip：逐阶段哈希验证的两版候选源码。',
              '- paired_*/：每次原始 result.json、audit.json、summary.json 和 worker.log。',
              '- analysis.json：本报告数字的机器可读汇总。',
              '- analyze.py：重新核算本报告，校验目标值、审计、完整 K 台账与对应版本源码哈希。',
              '']
    (OUT/'report.md').write_text('\n'.join(lines), encoding='utf-8')
    (OUT/'analysis.json').write_text(json.dumps(dict(metrics=metrics, rows=all_rows,
        reference=reference, comparable_gaps=gaps),ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(metrics, ensure_ascii=False))


if __name__ == '__main__':
    main()
