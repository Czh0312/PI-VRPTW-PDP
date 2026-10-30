"""Combine certified case reductions, exact relaxation LB, audited original UB."""
from pathlib import Path
import importlib.util,math,copy,json
OUT=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('final_certificate',OUT/'certify_reductions.py')
c=importlib.util.module_from_spec(spec);spec.loader.exec_module(c)
t=c.trial

def main():
    cfg,fleet=t.d.load_routing_case(t.BASE/'cases/S01_m8_k1_4.json',use_pool_config=False)
    parent=t.d.load_instance(cfg,fleet);seed=t.read(t.SEED)
    audit=t.d.audit_solution(parent,seed)
    assert audit['valid'] and audit['periodic_replay']['valid']
    minutes=c.certificate(parent,audit['objective'],60)
    seconds=c.certificate(parent,audit['objective'],1)
    assert minutes==t.read(OUT/'reduction_certificate.json')
    assert seconds==t.read(OUT/'second_grid_reduction_certificate.json')
    bound=t.read(OUT/'route_lower_bound.json');independent=t.read(OUT/'independent_partition_verification.json')
    assert independent['status']=='OPTIMAL' and independent['gap']==0
    assert math.isclose(independent['objective'],bound['E2_distance_lower_bound'],abs_tol=1e-7)
    lower_N4=20+minutes['E1_distance_lower_bound']*.1+bound['E2_distance_lower_bound']*.05+minutes['constant_cost']
    lower=min(lower_N4,minutes['N_ge_5_total_cost_lower_bound'])
    upper=audit['objective'];assert abs(upper-lower)<1e-7
    assert math.isclose(seed['cost_components']['e2_distance']/.05,bound['E2_distance_lower_bound'],abs_tol=1e-7)
    certificate=dict(status='CERTIFIED_OPTIMAL',model=8,case='S01',fixed_K=2,
        calendar_grids_verified_seconds=[1,60],objective=upper,global_lower_bound=lower,gap=0,
        proof_method='Optimality-preserving case reductions + complete feasible-trip relaxation enumeration + exact set partition + audited original feasible solution',
        original_full_MILP_native_status='Not closed by direct MILP; incomplete searches preserved separately',
        pool=4,N_ge_5_lower_bound=minutes['N_ge_5_total_cost_lower_bound'],N4_lower_bound=lower_N4,
        E1_distance_lower_bound=minutes['E1_distance_lower_bound'],E2_distance_lower_bound=bound['E2_distance_lower_bound'],
        all_task_masks_replayed=True,enumerated_prefixes=bound['enumerated_prefixes'],feasible_task_masks=bound['feasible_task_masks'],
        set_partition_states=bound['set_partition_states'],exact_enumeration_seconds=bound['elapsed_seconds'],
        independent_Gurobi_partition=independent,
        selected_tests=dict(passed=7,failed=0,skipped=0,pytest_seconds=1.16,full_suite=False,
            scope=['test_valid_bounds.py','test_certificate.py','test_route_lower_bound.py']),
        source_solution_sha256=t.sha(t.SEED),
        evidence_hashes={name:t.sha(OUT/name) for name in ('reduction_certificate.json','second_grid_reduction_certificate.json',
            'route_lower_bound.json','route_bound_patterns.json','independent_partition_verification.json','route_lower_bound.py',
            'certify_reductions.py','test_valid_bounds.py','test_certificate.py','test_route_lower_bound.py')})
    t.save('optimality_certificate.json',certificate)
    result=copy.deepcopy(seed)
    result['info']['original_direct_MILP_info']=copy.deepcopy(seed['info'])
    result['info'].update(status='CERTIFIED_OPTIMAL',proven_optimal=True,best_bound=lower,mip_gap=0,
        proof_scope='S01_M8_fixed_K2_second_or_minute_calendar',proof_method=certificate['proof_method'],
        original_native_status=seed['info']['status'],optimality_certificate='optimality_certificate.json')
    result['optimality_certificate']=certificate
    assert t.d.audit_solution(parent,result)['valid']
    t.save('certified_optimal_result.json',result)
    t.save('final_verification.json',dict(status='PASS',original_solution_audit_valid=True,
        periodic_replay_valid=True,lower_equals_upper=True,seconds_and_minutes_both_certified=True,
        optimality_certificate_sha256=t.sha(OUT/'optimality_certificate.json'),
        certified_optimal_result_sha256=t.sha(OUT/'certified_optimal_result.json'),
        selected_tests=certificate['selected_tests']))
    report='''# S01 模型8：仅固定批次数K=2，最优性已证明

精确最优成本为 **34.905055668684454**，箱池4个。结论同时适用于原1秒批次决策域和整分钟批次决策域；批次边界和截点没有预先固定。范围仅为S01、K=2，不是K=1～4的总体最优性结论。

## 模型规模及此前困难

| 模型8表达 | 变量 | 约束 |
|---|---:|---:|
| 单批次K=1，整分钟 | 8,668 | 8,475 |
| 双批次K=2，整分钟 | 8,802 | 9,061 |

变量仅增加约1.55%，约束约增加6.91%。但双批次增加订单分组、三个自由批次时间点和箱子跨批回用时序。此前MILP的线性松弛过弱：总费用下界甚至低于本例必需的20单位箱池成本；显式必要约束已将初始下界提高到约30.39。

模型3的单批次固定日历和固定箱池，与模型8单批次也并非完全相同的决策域。

## 最优性证明

1. 每批一次领用该批全部箱子，故每批需求≤箱池N。7单分两批，整数N≥4。
2. 两个实际批次各须完成卫星点配送及回总仓，一级运输距离至少57.271284253105414。二级所有闭合路线拼接并按三角不等式跳过重复访问，长度不少于遍历7客户的最短巡回。穷举5,040个顺序得该巡回94.32237807494273。因此N≥5时，即使忽略其它约束，总成本也至少35.58324732905768，高于已有方案34.905055668684454。最优箱池必为4。
3. N=4而总领箱7个，第二批装货前至少须回用第一批的3个已清洗箱。按每单最乐观的回到卫星点时刻、一级回运和清洗时长，秒级第二批最早09:57:02；整分钟最早09:58。四个早订单的配送截止均不晚于09:56，故只能属于第一批；它们已占满4个箱子，其余3单只能属于第二批。
4. 对该必要分组，穷举所有单趟送取任务顺序，保留原时窗、单趟重量/容积、同趟先送后取及使用时长、同趟不得混合两个配送批次等约束，放松趟间车辆衔接、跨趟送取先后、一级供货与回收截点。每趟末端最多3箱，故单趟最多3项配送及3项回收；枚举覆盖原问题的每一种可行单趟。共检查12,668个前缀，得到1,247种可行任务集合。
5. 用精确集合划分覆盖全部14项任务，动态规划得最低二级距离180.75854486747826；独立Gurobi集合划分（1,247个二进制变量、14条覆盖约束）也证明相同最优值，GAP=0。这是放松问题的精确最优下界，不要求其最低路线集合本身能满足全部原问题衔接条件。
6. 总下界为20＋5.727128425310542＋9.037927243373913＋0.14＝34.905055668684454。已有原问题方案经过独立业务审计、整分钟核验和两周期重放，费用恰好等于该下界。因此原问题K=2的最优性闭合。

## 一个最优方案

| 项目 | 结果 |
|---|---|
| 箱池 | 4 |
| 第一批 | 08:00—11:55；截点11:28 |
| 第二批 | 11:55—21:00；截点20:33 |
| 第一批订单 | C2、C4、C5、C7 |
| 第二批订单 | C3、C6、C8 |
| 精确最优费用 | 34.905055668684454 |
| 证明下界 | 34.905055668684454 |
| 证明GAP | 0 |

这些时刻是一个最优方案，不意味着最优日历唯一。

## 验证及耗时口径

本轮选定验证范围为三个实验测试文件：7通过、0失败、0跳过，未运行完整仓库套件。验证包括微型MILP的有效约束对照、独立Held–Karp巡回校验、全部1,247种单趟模式的独立重放和Gurobi集合划分对照。

最终精确单趟枚举及集合划分动态规划约1.159秒，独立Gurobi复核约0.022秒。这仅是最终证明计算步骤；本轮另有结构推导、开发验证和此前MILP试验，不能当作端到端求解耗时。原始大MILP没有直接运行到OPTIMAL；三个未完成搜索的原生日志和中断记录单独保留，不作为完整时间预算的性能对照。

核心证据见optimality_certificate.json、reduction_certificate.json、second_grid_reduction_certificate.json、route_lower_bound.json和independent_partition_verification.json。原始可行方案由上一轮K2_result.json保留；certified_optimal_result.json另附完整最优性证书并保留原直接MILP状态TIME_LIMIT。正式数据与生产求解器未修改。
'''
    (OUT/'report.md').write_text(report,encoding='utf-8')
    print(json.dumps(certificate,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
