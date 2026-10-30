"""Audit minute-grid experiments and compare the recorded second-grid trials."""
from pathlib import Path
import sys,json,hashlib,re,math
OUT=Path(__file__).resolve().parent
sys.path.insert(0,str(OUT))
import driver as d

def main():
    cfg,fleet=d.d.load_routing_case(d.BASE/'cases/S01_m8_k1_4.json',use_pool_config=False)
    problem=d.d.load_instance(cfg,fleet)
    checked=[];rows=[]
    for mode in ('cold','fixed','warm'):
        summary_path=OUT/(mode+'_summary.json')
        if not summary_path.exists():continue
        row=d.read(summary_path);completion=d.read(OUT/(mode+'_completion.json'))
        for name,digest in completion['artifacts'].items():assert d.sha(OUT/name)==digest,name
        assert row['settings']['calendar_grid_seconds']==60
        if row['sol_count']:
            result=d.read(OUT/(mode+'_result.json'))
            assert all(v%60==0 for v in result['batch_boundaries'])
            assert all(v%60==0 for v in result['batch_cutoffs'].values())
            audit=d.d.audit_solution(problem,result)
            assert audit['valid'] and audit['periodic_replay']['valid']
            assert math.isclose(audit['objective'],row['objective'],abs_tol=1e-6)
            assert row['max_constraint_violation']<=1e-5 and row['max_integer_violation']<=1e-5
        log=(OUT/(mode+'_gurobi.log')).read_text(encoding='utf-8')
        if mode=='warm':assert 'Loaded user MIP start with objective 35.5091' in log
        if mode=='cold':assert 'Loaded user MIP start' not in log
        presolve=re.search(r'Presolve time: ([0-9.]+)s',log)
        root=re.search(r'Root relaxation:.*?([0-9.]+) seconds',log)
        row.update(calendar_grid_seconds=60,presolve_seconds=float(presolve[1]) if presolve else None,
                   root_relaxation_seconds=float(root[1]) if root else None)
        checked.append(row)
        if mode!='fixed':rows.append(row)
    old=d.ROOT/'output/experiments/m8_s01_feasibility_diagnosis_20261024'
    for mode in ('cold','warm'):
        baseline=d.read(old/(mode+'-compact_summary.json'))
        baseline.update(mode=mode,calendar_grid_seconds=1)
        rows.append(baseline)
    d.save('comparison.json',rows)
    d.save('verification.json',dict(completed_minute_runs=len(checked),
        feasible_audit_passes=sum(bool(r.get('audit_valid')) for r in checked),
        minute_grid_checks_passed=True,artifact_hashes_verified=True,full_unit_suite_run=False,
        result_hashes={p.name:d.sha(p) for p in OUT.glob('*_result.json')}))
    lines=['# S01 模型8：整分钟批次求解试验','',
        'K=1～4，批次边界和配送/回收截点限制为整分钟；车辆行驶、服务及其它时长仍使用原秒级时间。',
        '紧凑MILP、单线程、种子42；物理输入、费用、箱池1～7、每阶段最短1分钟与此前试验一致。生产模型和正式数据未修改。',
        '每组优化预算300秒，建模另计。冷启动MIPFocus=1、DegenMoves=0、NoRelHeurTime=60、SolutionLimit=1（首个可行解即停止）；热启动使用默认搜索参数。',
        '冷/热两组的参数分别与相应秒级对照组一致。热启动前固定已审计整分钟方案补齐辅助变量；完整热启动模型不固定路线、K、箱池或日历。',
        '整分钟模型的最优性和下界只覆盖整分钟允许域，不能直接作为原秒级模型的下界或最优性证明。','',
        '| 批次刻度 | 启动方式 | 状态 | 方案费用 | 下界 | GAP | 建模秒 | 优化秒 | 节点 |',
        '|---|---|---|---:|---:|---:|---:|---:|---:|']
    def f(v):return '—' if v is None else f'{v:.4f}'
    for r in sorted(rows,key=lambda x:(x['calendar_grid_seconds'],x['mode'])):
        gap=r.get('mip_gap')
        lines.append(f"| {r['calendar_grid_seconds']}秒 | {r['mode']} | {r['status']} | {f(r.get('objective'))} | {f(r.get('best_bound'))} | {f(None if gap is None else gap*100)}% | {f(r['build_time'])} | {f(r['mip_runtime'])} | {r['node_count']} |")
    lines+=['','所有返回的分钟方案均重新核验整分钟限制、独立业务可行性、费用和两周期重放；未运行单元测试套件。',
        '完整原生日志、输入/源码校验、初解、结果、费用及对照数据保存在本目录。','']
    (OUT/'report.md').write_text('\n'.join(lines),encoding='utf-8')
    print(json.dumps(dict(verification=d.read(OUT/'verification.json'),comparison=rows),ensure_ascii=False,indent=2))

if __name__=='__main__':main()
