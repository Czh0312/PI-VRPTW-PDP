"""Verify the finished registered study and compare solve coverage with small10."""
from collections import Counter
import csv
import json
from pathlib import Path
import statistics

from experiments.runs import run_thesis_completion as d
from tools.data.build_formal_benchmark27 import digest


def main():
    out=Path(__file__).resolve().parent
    manifest=d.read(out/'manifest.json')
    d.verify(out,manifest)
    for name,expected in manifest['source_archive_hashes'].items():
        assert digest(out/name)==expected,name
    frozen=d.read(out/'frozen_dataset.json')
    dataset=Path(frozen['dataset'])
    assert digest(dataset/'manifest.json')==frozen['manifest_sha256']
    dataset_manifest=d.read(dataset/'manifest.json')
    assert all(digest(dataset/name)==expected for name,expected in dataset_manifest['artifact_sha256'].items())
    source=d.read(dataset/'source_integrity.json')
    assert all(digest(Path(source['source_dataset'])/name)==expected for name,expected in source['sha256'].items())
    rows=d.read(out/'rows.json')
    assert len(rows)==36 and all(r['customer_count']==7 for r in rows)
    assert {(r['case'],r['model']) for r in rows}=={(f'S{i:02}',m) for i in range(1,10) for m in range(1,5)}
    assert all(r['status'] not in ('NOT_RUN','RUNNING') for r in rows)
    for job in manifest['jobs']:
        attempts=d.attempt_dirs(out,job)
        assert len(attempts)==1 and d.complete_attempt(attempts[0]),job['id']
    assert manifest['external_initial_solution'] is False and manifest['m4_strengthening']=='none'
    pairs=[]
    by_key={(r['case'],r['model']):r for r in rows}
    for case in (f'S{i:02}' for i in range(1,10)):
        for fixed,free in ((1,2),(3,4)):
            a,b=by_key[case,fixed],by_key[case,free]
            if not a.get('valid'):
                continue
            tolerance=1e-4*max(1,abs(a['objective']))
            if b.get('best_bound') is not None:
                assert b['best_bound']<=a['objective']+tolerance,(case,fixed,free,'bound contradicts fixed feasible solution')
            if a.get('proven_optimal') and b.get('proven_optimal'):
                assert b['objective']<=a['objective']+tolerance,(case,fixed,free)
            pairs.append(dict(case=case,fixed_model=fixed,free_model=free,
                checked_free_bound=b.get('best_bound') is not None,
                checked_optimal_objective=a.get('proven_optimal',False) and b.get('proven_optimal',False)))
    old_out=Path('output/experiments/small10_m1_m4_exact300_20261023_v1')
    old_rows=d.read(old_out/'rows.json')
    table=[]
    for model in range(1,5):
        old=[r for r in old_rows if r['model']==model]
        new=[r for r in rows if r['model']==model]
        optimal=[r['solve_wall_seconds'] for r in new if r.get('proven_optimal') and r.get('valid')]
        gaps=[r['mip_gap'] for r in new if r.get('valid') and r.get('mip_gap') is not None]
        table.append(dict(model=model,planned=9,old10_optimal=sum(r.get('proven_optimal',False) for r in old),
            new7_optimal=sum(r.get('proven_optimal',False) for r in new),new7_audited_feasible=sum(r.get('valid',False) for r in new),
            new7_no_feasible=sum(not r.get('valid',False) for r in new),
            optimal_mean_seconds=statistics.mean(optimal) if optimal else None,
            optimal_max_seconds=max(optimal) if optimal else None,
            worst_feasible_gap=max(gaps) if gaps else None))
    with (out/'tables/model_summary.csv').open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(table[0]));writer.writeheader();writer.writerows(table)
    verification=dict(passed=True,dataset=frozen['dataset'],dataset_manifest_sha256=frozen['manifest_sha256'],
        registered_input_and_source_hashes_unchanged=True,source_archives_unchanged=True,
        dataset_artifacts_unchanged=len(dataset_manifest['artifact_sha256']),
        old_v3_source_files_unchanged=source['files_checked'],completed_calls=36,
        attempt_count=36,optimal=sum(r.get('proven_optimal',False) for r in rows),
        audited_feasible=sum(r.get('valid',False) for r in rows),statuses=dict(Counter(r['status'] for r in rows)),
        test_scope=d.read('output/inspections/small7_revision_20261023_v2/tests.json'),
        pair_consistency_checks=pairs,summary=table)
    d.write(out/'verification.json',verification)
    lines=['# 7顾客冻结版：300秒精确求解覆盖','',
        f"36次均已完成；已证最优{verification['optimal']}/36，独立审计可行{verification['audited_feasible']}/36。",'',
        '新版为formal_benchmark27_spacetime_v4_1。9个小规模均由同编号v3算例按数值顾客编号取前7位；仓点、车辆、所选订单字段和费用保持。中大规模未参加本轮求解。',
        'M1—M4各9次，每次300秒覆盖预检、建模和优化；compact、单线程、种子42、串行冷启动，M4强化关闭。最终提取、独立审计和进程开销另记。','',
        '| 模型 | 原10顾客已证最优 | 新7顾客已证最优 | 新7顾客审计可行 | 最优平均秒 | 最优最长秒 |',
        '|---|---:|---:|---:|---:|---:|']
    for r in table:
        avg=f"{r['optimal_mean_seconds']:.2f}" if r['optimal_mean_seconds'] is not None else '—'
        maximum=f"{r['optimal_max_seconds']:.2f}" if r['optimal_max_seconds'] is not None else '—'
        lines.append(f"| M{r['model']} | {r['old10_optimal']}/9 | {r['new7_optimal']}/9 | {r['new7_audited_feasible']}/9 | {avg} | {maximum} |")
    pending=[r for r in rows if not r.get('proven_optimal')]
    lines+=['','## 未证最优的运行','']
    if pending:
        lines+=['| 算例/模型 | 状态 | 审计可行 | 目标值 | 下界 | GAP |','|---|---|---|---:|---:|---:|']
        for r in pending:
            fmt=lambda v:'—' if v is None else f'{v:.6f}'
            gap='—' if r.get('mip_gap') is None else f"{100*r['mip_gap']:.2f}%"
            lines.append(f"| {r['case']}/M{r['model']} | {r['status']} | {r.get('valid',False)} | {fmt(r.get('objective'))} | {fmt(r.get('best_bound'))} | {gap} |")
    else:
        lines+=['全部36次均在预算内证明最优。']
    lines+=['','## 核验与范围','',
        '冻结数据、运行输入、源码快照、源码ZIP及36份完整结果的哈希核验均通过；旧v3全部1129个文件保持。',
        '数据回归仅选中small7与small10两个测试模块：10通过、0失败、0跳过；Gurobi预检含实际整数优化且成功。216份输入配置和两周期见证也已独立核验；见证未用于正式求解初解。',
        '保留了被替代的v4元数据版本和19次准备阶段调用，正式v4_1全部36次另起快照重跑。两版810份物理输入逐字节一致；详见../../inspections/small7_revision_20261023_v2/metadata_correction.json。',
        '10与7顾客是不同问题，不能据目标值差异推断成本优化效果；此表只比较限定预算下的求解覆盖，耗时受机器负载影响。同源小规模子集重叠，不能视为9个独立原始样本。','',
        '[逐例结果](report.md) · [逐次数据](tables/runs.csv) · [模型统计](tables/model_summary.csv) · [完整核验](verification.json)','']
    (out/'comparison.md').write_text('\n'.join(lines),encoding='utf-8')
    print(json.dumps({k:verification[k] for k in ('passed','completed_calls','optimal','audited_feasible','statuses')},ensure_ascii=False))


if __name__=='__main__':
    main()
