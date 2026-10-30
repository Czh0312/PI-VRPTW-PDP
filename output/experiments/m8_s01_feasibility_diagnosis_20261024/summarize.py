"""Verify saved diagnostic evidence and write an explicitly scoped report."""
from pathlib import Path
import json, hashlib, math, sys, re
OUT=Path(__file__).resolve().parent
sys.path.insert(0,str(OUT))
import diagnose as d

def main():
    problem=d.load_instance(*d.load_routing_case(d.BASE/'cases/S01_m8_k1_4.json',use_pool_config=False))
    rows=[]
    for path in sorted(OUT.glob('*_summary.json')):
        row=json.loads(path.read_text(encoding='utf-8'));mode=row['mode']
        log=(OUT/(mode+'.log')).read_text(encoding='utf-8')
        presolve=re.search(r'Presolve time: ([0-9.]+)s',log)
        root=re.search(r'Root relaxation:.*?([0-9.]+) seconds',log)
        row['presolve_seconds']=float(presolve[1]) if presolve else None
        row['root_relaxation_seconds']=float(root[1]) if root else None
        # The first two processes used read_milp_info's baseline default false;
        # normalize this reporting field from the explicit experimental mode.
        row['mip_start_supplied']=mode.startswith('warm')
        result_path=OUT/(mode+'_result.json')
        if row['sol_count']:
            result=json.loads(result_path.read_text(encoding='utf-8'))
            audit=d.audit_solution(problem,result)
            assert audit['valid'],audit
            assert math.isclose(audit['objective'],row['objective'],abs_tol=1e-6)
            assert 1<=row['K']<=4
            assert row['max_constraint_violation']<=1e-5
            assert row['max_integer_violation']<=1e-5
            if mode.startswith('warm'):
                assert row['objective']<=35.50906153797168+1e-5
                assert 'Loaded user MIP start with objective 35.5091' in (OUT/(mode+'.log')).read_text(encoding='utf-8')
            result['info']['mip_start_supplied']=row['mip_start_supplied']
            d.save(result_path.name,result)
        if mode.startswith('cold'):assert 'Loaded user MIP start' not in log
        d.save(path.name,row);rows.append(row)
    d.save('verification.json',dict(checked_runs=len(rows),feasible_audit_passes=sum(bool(r.get('audit_valid')) for r in rows),
        full_unit_suite_run=False,metadata_normalization='mip_start_supplied inferred from explicit warm mode and native acceptance log',
        result_hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in OUT.glob('*_result.json')}))
    lines=['# 模型八 S01 可行性诊断','',
        '使用上一轮冻结的模型源码与 S01 输入；逐项验证源码及输入哈希。K 域为 1～4，种子 42，单线程。',
        'ALNS 原方案的允许 K 域元数据从 1～7 改为 1～4；路线、批次、时间、数量、费用均未改动，并重新通过独立审计。',
        'fixed 为固定 ALNS 完整业务方案后补齐辅助变量；其 OPTIMAL 仅属于固定方案子问题，不能称为原问题全局最优。',
        'warm 为完整 K=1～4 模型，仅设置 Start，不固定任何业务决策。cold 不提供初解。',
        '每次优化上限 300 秒，建模另计；与此前包含建模的 1800 秒预算口径不同。',
        'cold-compact 同时使用紧凑表达及 MIPFocus=1、DegenMoves=0、NoRelHeurTime=60、SolutionLimit=1（找到首个可行解即停止）；不能据此单独归因于某一参数。','',
        '| 试验 | 状态 | 可行成本 | 下界 | 建模秒 | 优化秒 | K | 变量 | 约束 |',
        '|---|---|---:|---:|---:|---:|---:|---:|---:|']
    def fmt(x):return '—' if x is None else f'{x:.4f}'
    for r in rows:
        lines.append(f"| {r['mode']} | {r['status']} | {fmt(r.get('objective'))} | {fmt(r.get('best_bound'))} | {fmt(r['build_time'])} | {fmt(r['mip_runtime'])} | {r.get('K','—')} | {r['variables']} | {r['constraints']} |")
    lines+=['','所有有解结果均重新进行了独立业务审计和成本核对；未运行完整单元测试套件。',
        '', '诊断结论：ALNS 的这份 S01 方案可由 expanded 和 compact 两种 MILP 表达，成本一致；这不构成两者全部可行域等价性的完整证明。',
        '热启动试验在完整 K 域放开所有决策后被 Gurobi 原生接纳。返回 TIME_LIMIT 表示未证明全局最优；固定方案子问题的 OPTIMAL 不可转用于完整问题。',
        '诊断脚本、完整原生日志、变量补全值、结果及核验记录均保存在本目录；生产求解器及正式数据未修改。','']
    (OUT/'report.md').write_text('\n'.join(lines),encoding='utf-8')
    print(json.dumps(rows,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
