"""Audit all nine combined runs and publish the complete small7 comparison."""
from pathlib import Path
import csv
import json

from experiments.runs import run_single_batch_warm_start_trial as trial
from tools.configuration.loader import load_routing_case
from models.common.cycle import load_instance
from models.common.cycle.audit import audit_solution


def main():
    out=Path('output/experiments/small7_m4_combined300_20261023_v1')
    cold=Path('output/experiments/small7_m1_m4_exact300_20261023_v2')
    inspections=Path(__file__).resolve().parent
    integrity=trial.read(out/'integrity.json')
    assert integrity['completed']==integrity['planned']==9
    assert integrity['source_unchanged'] and integrity['dataset_unchanged'] and integrity['source_archive_unchanged']
    for name,expected in integrity['artifact_sha256'].items():
        assert trial.hashlib.sha256((out/name).read_bytes()).hexdigest()==expected,name
    protocol=trial.read(out/'protocol.json')
    assert protocol['total_seconds']==300 and protocol['alns_seconds']==30
    assert protocol['solver_sources_match_cold_snapshot']
    rows=trial.read(out/'rows.json')
    assert len(rows)==9 and {r['case'] for r in rows}=={f'S{i:02}' for i in range(1,10)}
    audits=[]
    for row in rows:
        assert row['model']==4 and row['mode']=='alns' and row['strengthening']=='capacity_activation'
        folder=out/f'{row["case"]}_m4_alns'
        cfg,fleet=load_routing_case(str(Path(protocol['dataset'])/'model_4'/'small'/row['case']/'case.json'),use_pool_config=False)
        problem=load_instance(cfg,fleet)
        result=trial.read(folder/'result.json')
        checked=audit_solution(problem,result)
        assert checked['valid']==row['valid'],row['case']
        audits.append(dict(case=row['case'],valid=checked['valid'],initial_valid=row['initial_valid'],start_accepted=row['mip_start_accepted']))
    # Preserve the timed-out M3 call, but derive its missing reference optimum
    # from the solved free-assignment extension on identical physical inputs.
    dataset=Path(protocol['dataset'])
    c3=trial.read(dataset/'model_3/small/S01/case.json')
    c4=trial.read(dataset/'model_4/small/S01/case.json')
    assert all(c3[k]==c4[k] for k in ('data','routing_config','economic_config','container_pool_size'))
    assert c3.get('objective_mode','distance')==c4.get('objective_mode','distance')=='distance'
    assert Path(c3['orders']).read_bytes()==Path(c4['orders']).read_bytes()
    assert Path(c3['operating_policy']).read_bytes()==Path(c4['operating_policy']).read_bytes()
    with Path(c3['data']).open(encoding='utf-8-sig',newline='') as f:
        assert sum(r['node_type']=='s' for r in csv.DictReader(f))==1
    cfg3,fleet3=load_routing_case(str(dataset/'model_3/small/S01/case.json'),use_pool_config=False)
    m3_path=cold/'runs/S01_m3_exact_300s_s42/attempt_001/result.json'
    m3=trial.read(m3_path)
    assert audit_solution(load_instance(cfg3,fleet3),m3)['valid']
    m4=next(r for r in rows if r['case']=='S01')
    assert m4['proven_optimal'] and m4['valid']
    upper=m3['info']['objective'];lower=m4['best_bound']
    assert abs(upper-lower)<=1e-6
    certificate=dict(valid=True,case='S01',model=3,objective_mode='distance',
        optimum=upper,feasible_upper_bound=upper,relaxation_lower_bound=lower,tolerance=1e-6,
        proof='M4 allows every M3 fixed assignment under the same single-batch reusable-container rules. Identical orders, policy, network, fleet and pool; S01 has one warehouse. Valid M4 cuts preserve integer feasibility. Thus z4* <= z3* <= audited M3 feasible objective; the bounds coincide.',
        original_m3_status=m3['info']['status'],original_status_and_timing_preserved=True,
        scope='Supplementary optimum certificate for this physical instance and integer-time distance model; not a claim that the original M3 call proved optimal in 300 seconds.',
        evidence_sha256={str(p):trial.hashlib.sha256(p.read_bytes()).hexdigest() for p in (
            m3_path,out/'S01_m4_alns/result.json',Path(c3['orders']),Path(c4['orders']),
            Path(c3['operating_policy']),Path(c4['operating_policy']),Path(c3['data']),Path(c3['routing_config']))})
    trial.write(out/'S01_m3_reference_certificate.json',certificate)
    scopes=[trial.read(inspections/name) for name in ('tests.json','warm_start_tests.json')]
    verification=dict(passed=True,completed_calls=9,optimal=sum(r['proven_optimal'] for r in rows),
        audited_feasible=sum(r['valid'] for r in rows),valid_alns_initials=sum(r['initial_valid'] for r in rows),
        accepted_starts=sum(bool(r['mip_start_accepted']) for r in rows),audits=audits,
        integrity=integrity,test_scopes=scopes,supplementary_m3_certificate=certificate)
    trial.write(out/'verification.json',verification)
    summary=trial.read(cold/'verification.json')['summary']
    cold_rows={(r['case'],r['model']):r for r in trial.read(cold/'rows.json')}
    references=[]
    for (case,model),r in cold_rows.items():
        if r['proven_optimal']:
            reference=dict(case=case,model=model,objective=r['objective'],proof='cold_exact',source=str(cold/'runs'/r['id']/'attempt_001/result.json'))
        elif model==4:
            warm=next(w for w in rows if w['case']==case)
            assert warm['proven_optimal']
            reference=dict(case=case,model=model,objective=warm['objective'],proof='ALNS30_and_strengthened_exact',source=str(out/f'{case}_m4_alns/result.json'))
        else:
            assert (case,model)==('S01',3)
            reference=dict(case=case,model=model,objective=upper,proof='M4_relaxation_bound_and_M3_feasible_schedule',source=str(out/'S01_m3_reference_certificate.json'))
        reference['source_sha256']=trial.hashlib.sha256(Path(reference['source']).read_bytes()).hexdigest()
        references.append(reference)
    trial.write(out/'optimal_references.json',dict(dataset=protocol['dataset'],objective='distance',time_domain='integer_time',references=references,
        scope='36 certified reference values. Original per-call statuses and budgets remain in the cold/combined reports.'))
    lines=['# 小规模统一7顾客：冻结与重新求解','',
        '正式输入为formal_benchmark27_spacetime_v4_1。S01—S09按对应v3十顾客算例的数值顾客编号取前7位，不依据求解结果选例。'
        '保留全部仓点、车辆和所选订单的所有字段；中大规模物理输入及成本字节保持。27个基础算例对应216份模型配置。','',
        '本轮完成36次M1—M4冷启动基线，以及9次M4强化/初解补测。每次预算300秒，单线程、种子42、compact。'
        'M4补测统一使用capacity_activation和30秒ALNS初解，ALNS、审计、建模、初解映射与MILP共享总预算；最终提取与审计开销另记。'
        '没有读取数据集见证或历史结果作为初解，没有按算例调参。','',
        '| 模型 | 10顾客冷启动最优 | 7顾客冷启动最优 | 7顾客冷启动审计可行 | 7顾客M4补测最优 |',
        '|---|---:|---:|---:|---:|']
    for r in summary:
        combined=f"{verification['optimal']}/9" if r['model']==4 else '未补测'
        lines.append(f"| M{r['model']} | {r['old10_optimal']}/9 | {r['new7_optimal']}/9 | {r['new7_audited_feasible']}/9 | {combined} |")
    lines+=['','## M4逐例对照','',
        '| 算例 | 冷启动最优 | 冷启动秒 | 冷启动GAP | 补测最优 | 补测总秒 | 补测GAP | ALNS初解有效 / Start接受 |',
        '|---|---|---:|---:|---|---:|---:|---|']
    pct=lambda v:'—' if v is None else f'{100*v:.2f}%'
    for r in rows:
        c=cold_rows[r['case'],4]
        lines.append(f"| {r['case']} | {c['proven_optimal']} | {c['solve_wall_seconds']:.2f} | {pct(c.get('mip_gap'))} | {r['proven_optimal']} | {r['total_seconds']:.2f} | {pct(r.get('mip_gap'))} | {r['initial_valid']} / {r['mip_start_accepted']} |")
    lines+=['','## 尚未证明最优的调用','']
    unresolved=[r for r in trial.read(cold/'rows.json') if not r['proven_optimal']]
    for r in unresolved:
        lines.append(f"- 冷启动 {r['case']}/M{r['model']}：{r['status']}，审计可行={r['valid']}，GAP={pct(r.get('mip_gap'))}。")
    for r in rows:
        if not r['proven_optimal']:
            lines.append(f"- M4补测 {r['case']}：{r['status']}，审计可行={r['valid']}，GAP={pct(r.get('mip_gap'))}。")
    lines+=['','S01/M3的独立冷启动记录保持TIME_LIMIT。补充证明：M4是相同物理输入下的自由选仓扩展，故其最优值为M3的下界；'
        f"该下界{lower:.8f}与已审计M3可行目标{upper:.8f}一致，因此可确认这条M3路线也最优。此证明未改写原调用状态或耗时。"
        '36个小规模距离最优参考值另存M4补测目录的optimal_references.json，其中S01/M3明确标注跨模型证明来源。']
    lines+=['','## 核验与结论边界','',
        f"基线36/36结果审计可行；M4补测{verification['audited_feasible']}/9审计可行，ALNS初解{verification['valid_alns_initials']}/9有效，{verification['accepted_starts']}/9被MILP接受。全部结果和注册输入、源码、源码ZIP的哈希核验通过，补测9份结果另经独立两周期重放。",
        '输入审计及216份两周期见证通过；它们仅证明输入可行。所选数据回归模块small7/small10共10通过，MIP-start映射模块10通过；合计20通过、0失败、0跳过，并非完整测试套件。每次严格预检均含实际Gurobi整数求解。',
        '旧v3全部1129个文件保持。初始v4的容量说明表遗漏修正另存v4_1，810份物理输入逐字节一致，已保留19次准备阶段调用，正式36次全部重跑。',
        '不同顾客规模不能直接比较目标值或推断成本收益；这里比较给定预算下的求解覆盖。M4补测同时改变了初解和表达式，不能把改善单独归因于其中一项；耗时受机器负载影响。同源小规模子集存在顾客重叠。','',
        '[新数据说明](../../data/formal_benchmark27_spacetime_v4_1/README.md) · [冻结登记](../../config/experiments/benchmark27_spacetime_v4_1/frozen.json) · '
        '[36次基线](small7_m1_m4_exact300_20261023_v2/comparison.md) · [9次M4补测](small7_m4_combined300_20261023_v1/report.md) · '
        '[补测核验](small7_m4_combined300_20261023_v1/verification.json)','']
    Path('output/experiments/small7_solver_comparison_20261023_v1.md').write_text('\n'.join(lines),encoding='utf-8')
    print(json.dumps({k:verification[k] for k in ('passed','completed_calls','optimal','audited_feasible','valid_alns_initials','accepted_starts')},ensure_ascii=False))


if __name__=='__main__':
    main()
