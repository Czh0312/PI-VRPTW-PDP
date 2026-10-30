"""Verify fixed-K evidence and compare the earlier unified-K experiment."""
from pathlib import Path
import sys,json,math,importlib.util
OUT=Path(__file__).resolve().parent
sys.path.insert(0,str(OUT))
spec=importlib.util.spec_from_file_location('fixed_k_trial',OUT/'driver.py')
trial=importlib.util.module_from_spec(spec);spec.loader.exec_module(trial)

def main():
    cfg,fleet=trial.d.load_routing_case(trial.BASE/'cases/S01_m8_k1_4.json',use_pool_config=False)
    problem=trial.d.load_instance(cfg,fleet)
    rows=[];verified=[]
    for path in sorted(OUT.glob('K*_summary.json')):
        prefix=path.name.removesuffix('_summary.json');row=trial.read(path)
        completion=trial.read(OUT/(prefix+'_completion.json'))
        for name,digest in completion['artifacts'].items():assert trial.sha(OUT/name)==digest,name
        if row['sol_count']:
            result=trial.read(OUT/(prefix+'_result.json'))
            audit=trial.d.audit_solution(problem,result)
            assert audit['valid'] and audit['periodic_replay']['valid']
            assert len(result['batches'])==row['fixed_K']
            assert all(v%60==0 for v in result['batch_boundaries'])
            assert all(v%60==0 for v in result['batch_cutoffs'].values())
            assert math.isclose(audit['objective'],row['objective'],abs_tol=1e-6)
        rows.append(row);verified.append(prefix)
    baseline=trial.read(trial.PREVIOUS/'warm_summary.json')
    trial.save('verification.json',dict(verified_runs=verified,all_artifact_hashes_verified=True,
        independent_audit_passes=sum(bool(r.get('audit_valid')) for r in rows),full_unit_suite_run=False,
        proof_scope='Each fixed K only; does not certify original K1..4 domain',
        result_hashes={p.name:trial.sha(p) for p in OUT.glob('K*_result.json')}))
    def f(x):return '—' if x is None else f'{x:.4f}'
    lines=['# S01 模型8：固定K、整分钟批次精确求解','',
        '仅固定批次数；订单分批、路线、车辆、服务时间、箱池、批次长度和回收截点仍参与优化。批次边界和截点限定整分钟，车辆运行保留原秒级时间。',
        '紧凑MILP、单线程、种子42、默认Gurobi搜索参数、无自定义回调；K=2使用已审计整分钟方案的完整辅助变量初解。',
        '每次优化预算300秒，建模另计；使用与此前相同的冻结源码和物理/费用输入。',
        '固定K下的最优性及下界仅覆盖该K，不能用于宣称K=1～4整体最优。','',
        '| 模型 | 状态 | 费用 | 下界 | GAP | 变量 | 约束 | 建模秒 | 优化秒 | 节点 |',
        '|---|---|---:|---:|---:|---:|---:|---:|---:|---:|']
    for name,r in [('统一K=1～4（此前对照）',baseline)]+[(f"固定K={r['fixed_K']}",r) for r in rows]:
        gap=r.get('mip_gap');gap='—' if gap is None else f'{gap*100:.2f}%'
        lines.append(f"| {name} | {r['status']} | {f(r.get('objective'))} | {f(r.get('best_bound'))} | {gap} | {r['variables']} | {r['constraints']} | {f(r['build_time'])} | {f(r['mip_runtime'])} | {r['node_count']} |")
    lines+=['','所有返回方案均再次核验固定K、整分钟约束、业务可行性、费用及两周期重放；未运行单元测试套件。',
        '原生日志、求解结果、费用、模型MPS、可行解SOL、输入/源码哈希和校验记录均在本目录。生产模型及正式数据未修改。','']
    (OUT/'report.md').write_text('\n'.join(lines),encoding='utf-8')
    print(json.dumps(rows,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
