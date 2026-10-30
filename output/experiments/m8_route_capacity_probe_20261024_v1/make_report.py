"""Summarize completed experiments without optimization or changing raw records."""
import csv
import json
from pathlib import Path

from driver import HERE,BASE,read,write,validate,sha

validate()
first=read(BASE/'comparison.json');capacity=read(HERE/'comparison.json')
phase=read(HERE/'phased/S01/summary.json')
assert sha(HERE/'phased/S01/summary.json')==read(HERE/'phased/S01/complete.json')['summary_sha256']
rows=[]
for s in first:
    if s['case']=='S02':continue
    rows.append(dict(s,label={'baseline':'原增强模型','domains':'时间域收紧','domains_symmetry':'时间域＋车辆排序'}[s['profile']]))
for s in capacity:
    rows.append(dict(s,label='时间域＋路线容量/连通',profile='domains_route_capacity'))
rows.append(dict(phase,label='先找解，后加路线约束',profile='native_phased',mip_runtime=phase['total_optimization_seconds']))
write(HERE/'all_comparisons.json',rows)
fields=['case','label','status','valid','objective','best_bound','mip_gap','first_solver_incumbent_seconds',
        'build_time','mip_runtime','variables','constraints']
with (HERE/'all_comparisons.csv').open('w',encoding='utf-8-sig',newline='') as h:
    writer=csv.DictWriter(h,fieldnames=fields);writer.writeheader()
    writer.writerows({k:r.get(k) for k in fields} for r in rows)
fmt=lambda v:'—' if v is None else f'{v:.4f}'
lines=['# 模型八精确求解改进实测（2026-10-24）','',
    '范围：S01、S03的7顾客实例，完整K=1～7、原物理输入及整数时钟、相同成本。每组300秒优化预算，建模、加约束和独立审计另计。单线程、种子42。',
    '前四种配置均各自冷启动，没有ALNS或其他运行的初解。S01另测两阶段流程：首阶段冷启动，随后同一Gurobi模型保留自身解，再加静态有效约束；总优化预算仍为300秒。',
    '原9例“精确1800秒后ALNS300秒”批次仍暂停。本轮没有运行新的ALNS，不能当作已完成全部9例GAP对照。','']
for case in ('S01','S03'):
    lines += [f'## {case}','',
        '| 方法 | 原生最终状态 | 已审计成本 | 全K下界 | 求解器/流程GAP% | 首次候选秒 | 建模秒 | 优化秒 |',
        '|---|---|---:|---:|---:|---:|---:|---:|']
    for r in rows:
        if r['case']!=case:continue
        lines.append('| '+' | '.join([r['label'],r['status'],fmt(r.get('objective')),fmt(r.get('best_bound')),
            fmt(100*r['mip_gap'] if r.get('mip_gap') is not None else None),
            fmt(r.get('first_solver_incumbent_seconds')),fmt(r.get('build_time')),fmt(r.get('mip_runtime'))])+' |')
    lines.append('')
lines += ['## 如何理解结果','',
    '1. 时间域筛选确实缩小模型：S01变量63,940→35,525（减少44.4%），S03为15,514→10,069（减少35.1%）。但两例的300秒最终成本都未优于原配置。车辆排序在本次试验也未改善最终成本，所以两个选项默认保持关闭。',
    '2. 静态路线约束直接增强下界：S01初始根LP从30.2821提升到33.0657；300秒最终下界33.1001，但未找到可行候选。S03则保持47.7610成本，首次候选约198.20→97.55秒，最终下界39.7998→39.8594，改善较小。',
    '3. 两阶段流程用于检验能否同时保留原模型较容易找到的解和路线约束的更强下界。它调用两次optimize，首阶段最多210秒，在首次整数候选后至少5秒、下一个合适MIP回调处结束；剩余预算用于继续求解。没有外部MIP start，第二阶段可复用Gurobi自身此前解。']
base=next(r for r in rows if r['case']=='S01' and r['profile']=='baseline')
if phase['valid'] and phase['objective']<=base['objective']+1e-6 and phase['best_bound']>base['best_bound']+1e-5:
    lines.append(f"本次S01两阶段流程的成本{fmt(phase['objective'])}、下界{fmt(phase['best_bound'])}，在不增加总优化预算的情况下，保留了不差于基准的上界，并提高了下界。尚不能据此宣称其他实例也会稳定加速。")
else:
    lines.append('本次两阶段结果尚未同时满足“不差于原配置的成本”和“更强下界”；不能把这一流程作为已经解决S01瓶颈的方案。')
lines += ['',
    '表中GAP由各组已审计上界与相应最终原生下界计算。TIME_LIMIT并未证明最优；无解也不表示不可行。首次候选是求解器事件，未单独提取审计，不能称为首次业务审计通过时间。',
    '两阶段表格的优化秒是两次Runtime之和；建模秒不含后续加行时间，后者在summary.json的augmentation_seconds中单列。原生阶段状态和解来源也分别保留。',
    '单种子、单预算结果不是多次运行平均，不根据这些数据推断一般加速比例。','',
    '## S02最优值控制','',
    '新配置（时间域＋车辆排序）在23.0970秒原生证明最优52.5159754765，K=2、箱池5；变量数6062。它与前次最优值一致。前次原配置为183.05秒、16844变量；本轮没有重跑S02原配置，因此不把时间差全部归因于模型。','',
    '## 正确性与复核','',
    '- 生产模型相关选定范围：tests/routing/test_model8_temporal_preprocessing.py、test_model8_physical_bounds.py、test_unified_batch_milp.py、test_count_screening.py，共62项通过，0失败、0跳过。',
    '- 路线约束选定范围：本目录test_route_bounds.py，8项通过，0失败、0跳过；XML结果见validation.xml。包含独立六任务容量路径覆盖枚举，以及expanded/compact表达下的最优值保持和两周期审计。',
    '- 九个已有ALNS方案分别通过时间域、前序回库覆盖、车辆重编号和路线子集约束的离线核对。它们只用于检查必要条件，未作为求解初解。',
    '- 所有最终可行方案由独立进程复核业务可行性、成本和两周期运行。S01单阶段路线约束组无最终方案，故不计为已审计可行。两阶段还分别保留阶段一、阶段二方案及最终选用来源。',
    '- 所有筛选保留原K=1～7及不可行证书；未用启发式排名删K、固定日历或订单分组。静态路线约束不通过回调加割。',
    '- 冻结代码和输入哈希均已复核，未修改原批次的冻结代码。以上是选定检查，不是完整仓库测试。','',
    '## 文件','',
    f'- [三组对照原始汇总]({(BASE/"comparison.json").as_posix()})',
    f'- [全部实测表格CSV]({(HERE/"all_comparisons.csv").as_posix()})',
    f'- [时间域筛选与证书]({(BASE/"preprocessing_diagnostics.json").as_posix()})',
    f'- [路线约束原型]({(HERE/"route_bounds.py").as_posix()})',
    f'- [S01两阶段记录]({(HERE/"phased/S01/summary.json").as_posix()})','']
(HERE/'report.md').write_text('\n'.join(lines),encoding='utf-8')
write(HERE/'final_verification.json',dict(frozen_dependency_hashes_verified=True,
    selected_test_runs=[dict(passed=62,failed=0,skipped=0),dict(passed=8,failed=0,skipped=0)],
    first_stage_completed=len(first),route_probe_completed=len(capacity),phased_completed=True,
    original_nine_case_batch_resumed=False,alns_run_in_this_round=False,
    report_sha256=sha(HERE/'report.md')))
print(json.dumps(dict(report=str(HERE/'report.md'),phased_result={k:phase.get(k) for k in
    ('status','objective','best_bound','mip_gap','total_optimization_seconds','augmentation_seconds')}),ensure_ascii=False))
