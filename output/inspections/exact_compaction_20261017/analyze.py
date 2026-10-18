"""Validate source hashes, raw audits, full-K ledgers and paired diagnostics."""
import hashlib
import json
from pathlib import Path
import statistics
import sys
import zipfile

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
sys.path.insert(0, str(ROOT))


def read(path):
    return json.loads(path.read_text())


def n(value):
    return '—' if value is None else f'{value:.6f}'


def gap(upper, lower):
    return None if upper is None or lower is None else 100*max(0, upper-lower)/abs(upper)


def pct(value):
    return '—' if value is None else f'{value:.2f}%'


def verify(group):
    from tools.configuration.loader import load_routing_case
    from models.common.cycle import load_instance
    from models.common.cycle.audit import audit_solution
    folder = OUT/group
    manifest = read(folder/'manifest.json')
    rows = read(folder/'results.json')
    assert len(rows) == 2*len(manifest['cases'])*len(manifest['seeds'])
    assert hashlib.sha256((ROOT/'data/formal_benchmark27_v1/manifest.json').read_bytes()).hexdigest() == manifest['dataset_sha256']
    with zipfile.ZipFile(folder/'candidate_sources.zip') as archive:
        for relative, digest in manifest['source_hashes']['candidate'].items():
            assert hashlib.sha256(archive.read(relative)).hexdigest() == digest
    with zipfile.ZipFile(OUT/'baseline_sources.zip') as archive:
        for relative, digest in manifest['source_hashes']['baseline'].items():
            assert hashlib.sha256(archive.read(relative)).hexdigest() == digest
    for row in rows:
        path = folder/row['variant']/f'{row["case"]}_m{row["model"]}_exact_s{row["seed"]}'
        result, audit = read(path/'result.json'), read(path/'audit.json')
        size = dict(S='small', M='medium', L='large')[row['case'][0]]
        source = ROOT/f'data/formal_benchmark27_v1/model_{row["model"]}/{size}/{row["case"]}/case.json'
        problem = load_instance(*load_routing_case(source, use_pool_config=False))
        assert sorted(r['batch_count'] for r in result['count_search']) == list(problem.batch_count_candidates)
        assert row['valid'] == bool(audit['valid'])
        assert row['objective'] == result['info'].get('objective')
        assert row['bound'] == result['info'].get('best_bound')
        assert row['candidate_count'] == len(result['count_search'])
        assert not result['info'].get('certificate_error_count', 0)
        assert not result['info'].get('audit_failure_count', 0)
        if row['valid']:
            replay = audit_solution(problem, result)
            assert replay['valid'] and replay['periodic_replay']['valid']
            assert audit['periodic_replay']['valid']
            assert abs(audit['objective']-row['objective']) < 1e-6
            assert row['bound'] <= row['objective']+1e-6
        if row['proven_optimal']:
            assert row['valid'] and abs(row['bound']-row['objective']) < 1e-5
    return rows


def main():
    pilot = verify('pilot_30s')
    hints_trial = verify('final_30s')
    rows = verify('validated_30s')
    manifest = read(OUT/'validated_30s/manifest.json')
    for relative, digest in manifest['source_hashes']['candidate'].items():
        if relative.startswith('models/'):
            assert hashlib.sha256((ROOT/relative).read_bytes()).hexdigest() == digest, relative
    builds = read(OUT/'build_comparison/results.json')
    build_manifest = read(OUT/'build_comparison/manifest.json')
    for relative, digest in manifest['source_hashes']['candidate'].items():
        if relative.startswith('models/'):
            assert build_manifest['source_hashes']['candidate'][relative] == digest
    reference = {}
    with zipfile.ZipFile(ROOT/'output/experiments/benchmark27_20261016_cost_update_v1/raw_artifacts.zip') as archive:
        for number, case in {(r['model'], r['case']) for r in rows}:
            costs = []
            for method, seeds in [('exact', [42]), ('alns', [42, 43, 44, 45, 46])]:
                for seed in seeds:
                    raw = json.loads(archive.read(f'runs/{case}_m{number}_{method}_s{seed}/attempt_001/result.json'))
                    if raw.get('audit', {}).get('valid'):
                        costs.append(raw['info']['objective'])
            reference[number, case] = min(costs) if costs else None
    grouped = {}
    for row in rows:
        grouped.setdefault((row['model'], row['case']), {})[row['variant']] = row
    metrics = {v: dict(runs=sum(r['variant'] == v for r in rows),
                      feasible=sum(r['variant'] == v and r['valid'] for r in rows),
                      proven=sum(r['variant'] == v and r['proven_optimal'] for r in rows))
               for v in ('baseline', 'candidate')}
    lines = ['# 精确求解模型压缩与短预算验证', '', '## Material Passport', '',
             '- Origin Skill: academic-research-suite / experiment-agent',
             '- Origin Mode: validate', '- Origin Date: 2026-10-17',
             '- Verification Status: ANALYZED（同机重跑与逐结果审计；不宣称统计推广）',
             '- Version Label: exact_compaction_v3', '', '## 修改和等价性', '',
             'M6–M8 的压缩形式各自共享二级任务的服务后载重、客户连接弧及对应时间/因果约束；内部弧副本改为连续变量。二元全局弧、唯一任务访问与逐行程流平衡保证这些副本仍取整数值。分批 AND 辅助变量改为直接连接不等式。详细论证见 formulation.md。',
             '当预计二级内部弧副本数超过 4,000 时使用压缩形式及软分批提示；较小模型保留展开形式和部分初解。该阈值只选择等价表达与搜索方式，不裁剪可行域。完整且审计通过的物理初解继续作为 MIP start。M7 延迟装载、费用、完整 K 域和物理规则保持原定义；三个模型无交叉编号导入，ALNS 本轮未更改。', '',
             '## 实验设置', '',
             '基线是本轮开始前的本地改进版 baseline_sources.zip，并非更早的正式实验源码。两版在同一机器、同一 Python/Gurobi 环境、同一输入与种子下，以独立进程串行交替运行；每次总预算 30 秒，Gurobi 单线程。案例为开发选例，不能替代全量正式实验。最终候选在运行前冻结，哈希见 validated_30s/manifest.json。', '',
             '## 建模开销', '',
             '以下为无 cProfile 的三次独立建模中位数。约束数包含线性约束与指示约束；该计时不含优化搜索。', '',
             '| 模型/算例/K | 旧/新建模秒数 | 旧/新二元变量数 | 旧/新约束数 |',
             '| --- | ---: | ---: | ---: |']
    build_summary = []
    for key in dict.fromkeys((r['model'], r['case'], r['k']) for r in builds):
        pair = {}
        for version in ('baseline', 'candidate'):
            selected = [r for r in builds if (r['model'], r['case'], r['k']) == key and r['version'] == version]
            assert len(selected) == 3
            assert len({(r['variables'], r['binary'], r['linear'], r['indicators']) for r in selected}) == 1
            pair[version] = dict(seconds=statistics.median(r['build_seconds'] for r in selected),
                                 binary=selected[0]['binary'],
                                 constraints=selected[0]['linear']+selected[0]['indicators'])
        a, b = pair['baseline'], pair['candidate']
        lines.append(f'| M{key[0]}/{key[1]}/{key[2]} | {a["seconds"]:.3f} / {b["seconds"]:.3f} | {a["binary"]:,} / {b["binary"]:,} | {a["constraints"]:,} / {b["constraints"]:,} |')
        build_summary.append(dict(model=key[0], case=key[1], k=key[2], **pair))
    lines += ['', '## 30 秒全 K 精确求解', '',
              '| 模型/算例 | 旧/新可行成本 | 旧/新全局下界 | 旧/新自身 GAP |',
              '| --- | ---: | ---: | ---: |']
    paired = []
    for key, pair in grouped.items():
        a, b = pair['baseline'], pair['candidate']
        upper = min((v for v in (reference.get(key), a['objective'], b['objective']) if v is not None), default=None)
        lines.append(f'| M{key[0]}/{key[1]} | {n(a["objective"])} / {n(b["objective"])} | {n(a["bound"])} / {n(b["bound"])} | {pct(gap(a["objective"], a["bound"]))} / {pct(gap(b["objective"], b["bound"]))} |')
        paired.append(dict(model=key[0], case=key[1], common_upper=upper,
                           baseline_common_gap=gap(upper, a['bound']),
                           candidate_common_gap=gap(upper, b['bound'])))
    lines += ['', f'基线可行 {metrics["baseline"]["feasible"]}/7，证明最优 {metrics["baseline"]["proven"]}/7；候选可行 {metrics["candidate"]["feasible"]}/7，证明最优 {metrics["candidate"]["proven"]}/7。未找到可行解的运行仍计入分母，不能把缺失 GAP 当作零。', '',
              '下表使用同一有效可行上界比较下界。上界可来自以前的精确或 ALNS 结果；它不表示本次冷启动求解器找到了该方案，也不代表已知最优值。', '',
              '| 模型/算例 | 共同可行上界 | 旧/新界差 |', '| --- | ---: | ---: |']
    for r in paired:
        lines.append(f'| M{r["model"]}/{r["case"]} | {n(r["common_upper"])} | {pct(r["baseline_common_gap"])} / {pct(r["candidate_common_gap"])} |')
    lines += ['', '## 开发过程与限制', '',
              'pilot_30s 保留仅共享约束、尚未取消内部弧冗余整数性及部分初解补全的首版。M8/S03 的成本相同，但界差由约 21.69% 退化到 25.54%，没有隐藏此负结果。',
              'final_30s 是对全部规模统一使用压缩形式和软提示的第二版：M8/S03 成本退化到约 48.02；随后只改回小模型部分初解的试跑成本约 52.95（adaptive_start_smoke）。最终根据重复弧规模选择等价形式；这些开发结果全部保留，最终表仅来自另行冻结、完整成对重跑的 validated_30s。',
              'medium_verbose.txt 与 medium_hints_verbose.txt 是固定 K=2 的 20 秒诊断，不属于全 K 对照：部分 MIP start 找到约 209.70 的可行解但几乎耗尽补全预算；软提示未找到可行解，下界则达到约 142.80。两者展示上界与下界的取舍，不能把固定 K 下界当作全局下界。',
              '构建加速、可行解数量、解质量、最优性证明是不同指标。结果不保证所有小实例都最优，也不保证大实例在短预算内有可行解。完整 K 证明仍受最弱未解决候选的下界限制。', '',
              '变量提示的用途依据 [Gurobi 官方说明](https://docs.gurobi.com/projects/optimizer/en/current/reference/attributes/variable.html#varhintval)。提示不固定变量，也不提供可行性或最优性证明。', '',
              '## 检查与复现', '',
              '最终回归测试 311 项通过（65.45 秒），日志见 tests_final.txt；包括已知最优值/独立穷举、M7 延迟装载、共享载重、纯回收、容量与因果、热启动、全 K 证书及模型隔离。分析脚本校验源码哈希、每次完整 K 台账、所有有效结果的周期重放与目标值，并检查上下界一致性。',
              '复现入口：experiments/runs/compare_joint_refinement.py（--baseline-source 指向本目录 baseline_sources.zip）；experiments/runs/profile_joint_build.py；本目录 analyze.py。所有运行参数见各 manifest.json。', '',
              '## 统计误区扫描：11/11', '',
              '| 项目 | 处理 |', '| --- | --- |',
              '| Simpson 悖论 | 逐模型、逐实例报告，未跨规模平均 |',
              '| 生态谬误 | 仅对实测配置下结论 |',
              '| Berkson/选择偏差 | 明示开发选例，非随机代表样本 |',
              '| 碰撞变量偏差 | 不按成功与否筛除运行 |',
              '| 基率忽略 | 明示可行与证明最优的完整分母 |',
              '| 向均值回归 | 两版同机成对重跑 |',
              '| 幸存者偏差 | 保留无可行解及建模超时 |',
              '| 多重寻找效应 | 不作显著性宣称，保留首版负结果 |',
              '| 分析路径自由 | 冻结各版源码，保留开发诊断 |',
              '| 相关当因果 | 只评价软件配置对照，不推断现实运营收益 |',
              '| 反向因果 | 冻结源码在前，测量在后 |', '']
    (OUT/'report.md').write_text('\n'.join(lines))
    (OUT/'analysis.json').write_text(json.dumps(dict(metrics=metrics, pairs=paired,
        builds=build_summary, rows=rows, pilot=pilot, hints_trial=hints_trial), ensure_ascii=False, indent=2)+'\n')
    print(json.dumps(metrics, ensure_ascii=False))


if __name__ == '__main__':
    main()
