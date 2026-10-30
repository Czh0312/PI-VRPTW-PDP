"""Verify and explain a fixed-K2 solve with valid physical inequalities."""
from pathlib import Path
import json,math,importlib.util
OUT=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('bounds_trial_report',OUT/'solve_k2.py')
trial=importlib.util.module_from_spec(spec);spec.loader.exec_module(trial)

def main():
    cfg,fleet=trial.d.load_routing_case(trial.BASE/'cases/S01_m8_k1_4.json',use_pool_config=False)
    parent=trial.d.load_instance(cfg,fleet)
    row=trial.read(OUT/'solve_summary.json');result=trial.read(OUT/'solve_result.json')
    completion=trial.read(OUT/'solve_completion.json')
    for name,digest in completion['artifacts'].items():assert trial.sha(OUT/name)==digest,name
    audit=trial.d.audit_solution(parent,result)
    assert audit['valid'] and audit['periodic_replay']['valid']
    assert len(result['batches'])==2 and all(v%60==0 for v in result['batch_boundaries'])
    assert all(v%60==0 for v in result['batch_cutoffs'].values())
    assert result['info']['proof_scope']=='M8_K2_all_free_minute_calendar_with_valid_bounds'
    assert math.isclose(audit['objective'],row['objective'],abs_tol=1e-6)
    assert row['max_constraint_violation']<=1e-5 and row['max_integer_violation']<=1e-5
    tests=trial.read(OUT/'test_results.json')
    assert tests['passed']==2 and tests['failed']==tests['skipped']==0
    trial.save('verification.json',dict(hash_checks_passed=True,independent_audit_valid=True,
        periodic_replay_valid=True,only_K_fixed=True,calendar_grid_seconds=60,
        free_boundary_and_cutoffs_verified_at_build=True,test_results=tests,
        result_sha256=trial.sha(OUT/'solve_result.json')))
    old=trial.read(trial.ROOT/'output/experiments/m8_s01_fixed_k_minute_exact_20261024/K2_summary.json')
    def f(x):return '—' if x is None else f'{x:.6f}'
    lines=['# 模型8 S01：仅固定批次数K=2的最优性求解','',
        '仅固定K=2；订单分批、路线、车辆、服务时刻、箱池、内部批次边界与两个回收截点全部继续优化。批次决策仍限定整分钟，车辆运行时间仍为秒。',
        '紧凑MILP、单线程、种子42、默认Gurobi搜索参数；以此前已审计成本34.905055668684454方案热启动。优化上限1800秒，建模另计。',
        '当前允许域为K=2、整分钟批次；任何最优性证明都只覆盖这一允许域，不覆盖K=1～4或原秒级批次决策域。','',
        '## 模型大小与组合选择','',
        '| 模型 | 变量 | 约束 |','|---|---:|---:|',
        '| 模型8 K=1，整分钟批次 | 8668 | 8475 |',
        '| 模型8 K=2，原表达 | 8802 | 9061 |',
        '| 模型8 K=2，补有效约束 | 8802 | 9067 |','',
        'K=1到K=2的变量数量仅增加约1.55%，约束约增加6.91%。但订单需选择两批之一，7订单最多126种非空分组；自由批次时间点从1个增为3个，并新增跨批次净箱回用时序。组合复杂度不由变量数量比例直接决定。',
        '模型3单批次的原始日历和箱池固定，与模型8单批次也并非完全相同的决策域。','',
        '## 补充约束的有效性','',
        '1. 每批装货时一次领用该批全部箱子，物理箱池总量为N，故每批箱量D_b≤N。由D_1+D_2=7，得到整数N≥ceil(7/2)=4。',
        '2. 本例只有一个卫星点；每批均要求正配送和正回库箱量，故必须有配送访问和回收访问。原模型已限制每类访问最多一次，因此各类访问和恰好等于1。',
        '这些是原整数业务约束的必要条件，没有固定任何批次时间、订单分组或路线。新增6条线性约束和箱池下界，未新增变量。',
        '两个微型双批次算例对比均保持精确最优值，并通过独立审计：2通过、0失败、0跳过；未运行完整测试套件。','',
        '## 求解结果','',
        '| 表达 | 状态 | 费用 | 下界 | GAP | 优化秒 | 节点 |','|---|---|---:|---:|---:|---:|---:|']
    for label,r in [('原表达，300秒',old),('补有效约束，本次',row)]:
        lines.append(f"| {label} | {r['status']} | {f(r.get('objective'))} | {f(r.get('best_bound'))} | {f(None if r.get('mip_gap') is None else 100*r['mip_gap'])}% | {f(r['mip_runtime'])} | {r['node_count']} |")
    lines+=['',f"本次建模{row['build_time']:.4f}秒，总求解调用{row['solve_wall_seconds']:.4f}秒，箱池{row['pool']}。",
        '返回方案的整分钟限制、独立费用和两周期重放再次核验通过。输入与模型主体使用同一冻结源码，正式数据及生产求解器未修改。','']
    (OUT/'report.md').write_text('\n'.join(lines),encoding='utf-8')
    print(json.dumps(dict(summary=row,verification=trial.read(OUT/'verification.json')),ensure_ascii=False,indent=2))

if __name__=='__main__':main()
