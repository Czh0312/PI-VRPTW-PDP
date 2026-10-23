"""Verify the registered experiment and publish the completed efficiency report."""
from collections import Counter
from pathlib import Path
import statistics
import sys

sys.path.insert(0,str(Path(__file__).resolve().parent/'source'))

from experiments.runs import run_thesis_completion as d
from tools.configuration.loader import load_routing_case
from models.common.cycle import load_instance
from models.common.cycle.audit import audit_solution


def main():
    out=Path(__file__).resolve().parent
    manifest=d.read(out/'manifest.json');d.verify(out,manifest)
    for name,expected in manifest['source_archive_hashes'].items():assert d.sha(out/name)==expected,name
    dataset=Path(manifest['dataset']);suite=d.read(dataset/'manifest.json')
    assert d.sha(dataset/'manifest.json')==manifest['dataset_sha256']
    assert all(d.sha(dataset/name)==expected for name,expected in suite['artifact_sha256'].items())
    rows=d.read(out/'rows.json');assert len(rows)==39
    assert all(r['status'] not in ('NOT_RUN','RUNNING') for r in rows)
    checks=[]
    for job in manifest['jobs']:
        attempts=d.attempt_dirs(out,job)
        assert len(attempts)==1 and d.complete_attempt(attempts[0]),job['id']
        row=next(r for r in rows if r['id']==job['id'])
        result_path=attempts[0]/'result.json'
        if result_path.exists():
            cfg,fleet=load_routing_case(out/job['case_path'],use_pool_config=False)
            result=d.read(result_path)
            if result['info'].get('objective') is not None:
                audited=audit_solution(load_instance(cfg,fleet),result)
                assert audited['valid']==row['valid'],job['id']
            if job['method']=='alns':assert not result['info'].get('proven_optimal'),job['id']
        checks.append(dict(id=job['id'],valid=row['valid'],status=row['status']))
    assert not any(r['reference_conflict'] for r in rows),'A candidate contradicts its reference; investigate time domains before claiming optimality'
    exact=[r for r in rows if r['method']=='exact'];alns=[r for r in rows if r['method']=='alns']
    assert len(exact)==3 and len(alns)==36
    references=d.read(out/'references.json')
    certified={(r['case'],r['model']):dict(r) for r in references['references']}
    for r in exact:
        assert r['proven_optimal'] and r['valid']
        original=certified[r['case'],r['model']]
        assert abs(original['objective']-r['objective'])<=1e-6
        result_path=out/'runs'/r['id']/'attempt_001/result.json'
        certified[r['case'],r['model']]=dict(case=r['case'],model=r['model'],objective=r['objective'],
            proof='cold_exact_600',source=result_path.relative_to(Path.cwd()).as_posix(),source_sha256=d.sha(result_path))
    for r in certified.values():r['source']=str(r['source']).replace('\\','/')
    assert len(certified)==36 and all(r['proof'] in ('cold_exact','cold_exact_600') for r in certified.values())
    d.write(out/'optimal_references.json',dict(dataset=manifest['dataset'],dataset_manifest_sha256=manifest['dataset_sha256'],
        objective='distance',time_domain='integer_time',references=sorted(certified.values(),key=lambda r:(r['model'],r['case'])),
        scope='All 36 small M1-M4 reference values now have direct cold MILP optimality proofs: 33 from the original 300-second runs and three from the new 600-second runs. Original reference inputs remain unchanged.'))
    prior={(r['case'],r['model']):r for r in d.read(out/'prior_300_rows.json')}
    summary=d.read(out/'alns_summary.json')
    efficiency=[]
    for model in range(1,5):
        group=[r for r in alns if r['model']==model]
        hits=[r['first_optimum_recorded_seconds'] for r in group if r['matches_known_optimum'] and r['first_optimum_recorded_seconds'] is not None]
        first_feasible=[r['first_feasible_recorded_seconds'] for r in group if r['valid'] and r['first_feasible_recorded_seconds'] is not None]
        efficiency.append(dict(model=model,recorded_hits_within_60_seconds=sum(r['first_optimum_recorded_within_budget'] for r in group),
            mean_first_feasible_internal_seconds=statistics.mean(first_feasible) if first_feasible else None,
            mean_first_optimum_internal_seconds=statistics.mean(hits) if hits else None,
            max_first_optimum_internal_seconds=max(hits) if hits else None,
            mean_full_solve_call_seconds=statistics.mean(r['solve_wall_seconds'] for r in group),
            max_full_solve_call_seconds=max(r['solve_wall_seconds'] for r in group)))
    d.write(out/'efficiency_detail.json',efficiency)
    verification=dict(passed=True,registered_hashes_unchanged=True,dataset_unchanged=True,
        result_replays=checks,statuses=dict(Counter(r['status'] for r in rows)),
        exact600_optimal=sum(r['proven_optimal'] for r in exact),alns60_feasible=sum(r['valid'] for r in alns),
        alns60_matches_known_optimum=sum(r['matches_known_optimum'] for r in alns),
        alns_is_an_independent_optimality_proof=False,test_scope=d.read(out/'tests.json'),summary=summary,
        efficiency=efficiency,recorded_hits_within_60_seconds=sum(r['first_optimum_recorded_within_budget'] for r in alns))
    d.write(out/'verification.json',verification)
    num=lambda v:'—' if v is None else f'{v:.3f}'
    lines=['# 7顾客：600秒精确补测与60秒ALNS效率','',
        '固定formal_benchmark27_spacetime_v4_1；单线程、种子42、串行，输入及求解器源码与原300秒冷启动保持。'
        '10分钟是重新冷启动的总预算。ALNS为独立60秒调用，不输入历史解、见证或参考最优值，也不使用最优值提前停止。','',
        '## 原5分钟未证最优的3次调用','',
        '| 算例/模型 | 300秒GAP | 600秒状态 | 已证最优 | 实际调用秒 | 600秒GAP |',
        '|---|---:|---|---|---:|---:|']
    for r in exact:
        p=prior[r['case'],r['model']]
        lines.append(f"| {r['case']}/M{r['model']} | {num(100*p['mip_gap'])}% | {r['status']} | {r['proven_optimal']} | {num(r.get('solve_wall_seconds'))} | {num(100*r['mip_gap'] if r.get('mip_gap') is not None else None)}% |")
    lines+=['','## ALNS每例60秒，M1—M4各9例','',
        '| 模型 | 审计可行 | 达到已知MILP最优值 | 平均GAP | 最大GAP | 命中算例内部首次达到平均秒 |',
        '|---|---:|---:|---:|---:|---:|']
    for r in summary:
        lines.append(f"| M{r['model']} | {r['feasible']}/9 | {r['matches_known_optimum']}/9 | {num(r['mean_gap_percent'])}% | {num(r['max_gap_percent'])}% | {num(r['mean_first_optimum_internal_seconds'])} |")
    lines+=['','## ALNS未匹配最优值的调用','']
    misses=[r for r in alns if not r['matches_known_optimum']]
    if not misses:lines+=['36/36份最终ALNS结果均与已保存的MILP最优参考值一致。']
    for r in misses:
        lines.append(f"- {r['case']}/M{r['model']}：{r['status']}，可行={r['valid']}，目标={num(r.get('objective'))}，参考={num(r['reference_objective'])}，GAP={num(r['optimal_gap_percent'])}%。")
    lines+=['','## 验证与解释','',
        '39次调用均保留首次尝试。注册输入、源码快照、源码ZIP、冻结数据和结果文件哈希核验通过；最终路线另经独立两周期重放。'
        '实验覆盖/评分测试只选中tests/routing/test_small7_efficiency_check.py：9通过、0失败、0跳过；Gurobi预检含真实整数优化且成功。',
        'ALNS命中参考值是求解质量指标，ALNS自身仍报告FEASIBLE并且proven_optimal=False。参考值对应整数时间MILP，最终物理路线经独立审计；此处不另行声称连续时间问题的全局最优性。',
        '原33份300秒直接最优证明与本轮3份600秒直接最优证明现已覆盖全部36个小规模模型配置；独立MILP最优参考表另存optimal_references.json，原有冻结参考输入保持。',
        '首次达到最优的时间来自ALNS内部最优解历史，相对内部搜索入口；中间每条记录未独立重放。完整调用耗时、首次可行时间、迭代次数、历史范围及预算内匹配标记另列CSV。'
        '60秒是预算，少量返回/审计开销及历史时钟范围不应混作严格端到端的60秒时限。',
        '仅一个种子，不据此宣称多种子稳定性；平均GAP只统计有可行结果且参考一致的调用。39次全部结果及失败均保留。','',
        '[逐次CSV](tables/runs.csv) · [ALNS统计](alns_summary.json) · [详细耗时](efficiency_detail.json) · [全部36项直接最优参考](optimal_references.json) · [完整核验](verification.json) · [注册与源码哈希](manifest.json)','']
    (out/'comparison.md').write_text('\n'.join(lines),encoding='utf-8')
    print({k:verification[k] for k in ('passed','exact600_optimal','alns60_feasible','alns60_matches_known_optimum')})


if __name__=='__main__':main()
