"""Three 600-second cold exact retries and all 36 independent 60-second ALNS runs."""
from __future__ import annotations

import argparse
from collections import Counter
import csv
import math
from pathlib import Path
import shutil
import statistics
import sys
import time
import zipfile

HERE=Path(__file__).resolve().parent
ROOT=HERE/'source' if (HERE/'source').is_dir() else Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))

from experiments.runs import run_thesis_completion as d
from experiments.runs import run_spacetime_quality as q
from experiments.runs.spacetime_windows_io_adapter import atomic_write

d.write=atomic_write
PRIOR='output/experiments/small7_m1_m4_exact300_20261023_v2'
REFERENCES='output/experiments/small7_m4_combined300_20261023_v1/optimal_references.json'
FROZEN='config/experiments/benchmark27_spacetime_v4_1/frozen.json'


def plan(suite,prior):
    records=sorted((r for r in suite['cases'] if r['size']=='small' and r['model'] in (1,2,3,4)),key=lambda r:(r['model'],r['base_case_id']))
    expected={(f'S{i:02}',m) for m in range(1,5) for i in range(1,10)}
    if len(records)!=36 or {(r['base_case_id'],r['model']) for r in records}!=expected or any(r['customer_count']!=7 for r in records):
        raise ValueError('Require exactly the frozen 36 seven-customer M1-M4 inputs')
    previous={(r['case'],r['model']):r for r in prior}
    if len(prior)!=36 or set(previous)!=expected:
        raise ValueError('Require all 36 original 300-second cold rows')
    unresolved={key for key,r in previous.items() if not r.get('proven_optimal')}
    if unresolved!={('S01',3),('S01',4),('S02',4)} or any(previous[k]['status']!='TIME_LIMIT' for k in unresolved):
        raise ValueError('Original timeout selection changed; investigate before registering')
    jobs=[]
    for method,budget,chosen in (('exact',600,unresolved),('alns',60,expected)):
        for r in records:
            if (r['base_case_id'],r['model']) not in chosen:
                continue
            jobs.append(dict(id=f'{r["base_case_id"]}_m{r["model"]}_{method}_{budget}s_s42',
                case=r['base_case_id'],model=r['model'],method=method,budget=budget,seed=42,
                iterations=10**9,version='current',size='small',structure='small',split='formal',
                family=r['family_id'],source_block=r['source_block'],customer_count=7,
                case_path=f'cases/{r["base_case_id"]}_m{r["model"]}.json'))
    return records,jobs


def score(row,reference,info=None):
    """Matching a certified value is separate from a solver optimality proof."""
    target=reference['objective']
    if not math.isfinite(target) or target<=0:
        raise ValueError('Require a finite positive certified reference')
    value=row.get('objective') if row.get('valid') else None
    tolerance=max(1e-6,abs(target)*1e-8)
    conflict=value is not None and value<target-tolerance
    gap=100*max(0,value-target)/target if value is not None and not conflict else None
    history=(info or {}).get('incumbent_history',[])
    hits=[h['time'] for h in history if abs(h['objective']-target)<=tolerance]
    matches=bool(value is not None and not conflict and abs(value-target)<=tolerance)
    first=min(hits,default=None) if matches else None
    return dict(reference_objective=target,reference_proof=reference['proof'],reference_time_domain='integer_time',
        reference_conflict=conflict,optimal_gap_percent=gap,
        matches_known_optimum=matches,
        first_feasible_recorded_seconds=(info or {}).get('time_to_first_feasible'),
        first_optimum_recorded_seconds=first,first_optimum_recorded_within_budget=first is not None and first<=row['budget'],
        time_to_best_recorded_seconds=(info or {}).get('time_to_best'),
        history_scope=(info or {}).get('history_scope','solver_internal_incumbent_history'),
        history_independently_replayed=False)


def prepare(out):
    from experiments.runs.run_model6_comparison import freeze_case
    import gurobipy as gp
    import psutil
    if out.exists() and any(out.iterdir()):
        raise ValueError('Refusing to overwrite a study')
    frozen=d.read(ROOT/FROZEN);dataset=ROOT/frozen['dataset'];suite=d.read(dataset/'manifest.json')
    if d.sha(dataset/'manifest.json')!=frozen['manifest_sha256']:
        raise ValueError('Frozen dataset manifest changed')
    for name,expected in suite['artifact_sha256'].items():
        if d.sha(dataset/name)!=expected:
            raise ValueError(f'Frozen dataset input changed: {name}')
    prior=d.read(ROOT/PRIOR/'rows.json');prior_manifest=d.read(ROOT/PRIOR/'manifest.json')
    d.verify(ROOT/PRIOR,prior_manifest)
    if prior_manifest['dataset_sha256']!=frozen['manifest_sha256']:
        raise ValueError('Prior study used different inputs')
    if prior_manifest['external_initial_solution'] or prior_manifest['m4_strengthening']!='none' or prior_manifest['exact_threads']!=1:
        raise ValueError('Prior study was not the registered single-thread cold baseline')
    records,jobs=plan(suite,prior)
    references=d.read(ROOT/REFERENCES)
    if references['dataset']!=frozen['dataset'] or references['objective']!='distance' or references.get('time_domain')!='integer_time':
        raise ValueError('Reference scope differs')
    refs={(r['case'],r['model']):r for r in references['references']}
    if len(references['references'])!=36 or set(refs)!={(r['base_case_id'],r['model']) for r in records}:
        raise ValueError('Require all 36 certified references')
    out.mkdir(parents=True,exist_ok=True)
    for name in ('source','inputs','cases','runs','tables','reference_evidence'):
        (out/name).mkdir()
    sources={f'source/{name}':value for name,value in d.snapshot(ROOT,out/'source').items()}
    for name,value in sources.items():
        if name.startswith(('source/models/','source/tools/configuration/')) and prior_manifest['source_hashes'][name]!=value:
            raise ValueError(f'Solver changed since 300-second baseline: {name}')
    shutil.copyfile(__file__,out/'driver.py');sources['driver.py']=d.sha(out/'driver.py')
    evidence={}
    for record in records:
        prefix=f'{record["base_case_id"]}_m{record["model"]}'
        cfg,original=freeze_case(record['case_path'],out,prefix)
        if cfg.get('initial_joint_result'):
            raise ValueError('External starts are not allowed')
        cfg.update(exact_strategy='milp',milp_options=dict(threads=1,formulation='compact'))
        d.write(out/'cases'/f'{prefix}.json',cfg);evidence.update(original)
    proofs={}
    def copy_proof(path,expected):
        origin=(ROOT/path).resolve()
        if d.sha(origin)!=expected:
            raise ValueError(f'Reference evidence changed: {path}')
        target=out/'reference_evidence'/f'{expected}_{origin.name}'
        if not target.exists():shutil.copyfile(origin,target)
        proofs[str(path)]=dict(snapshot=target.relative_to(out).as_posix(),sha256=expected)
    for ref in refs.values():
        copy_proof(ref['source'],ref['source_sha256'])
        if ref['proof']=='M4_relaxation_bound_and_M3_feasible_schedule':
            for name,expected in d.read(ROOT/ref['source'])['evidence_sha256'].items():copy_proof(name,expected)
    environment=dict(captured_at=d.now(),python=sys.version,interpreter=sys.executable,
        gurobi_version=list(gp.gurobi.version()),workers=1,solver_threads=1,
        available_memory_mib=psutil.virtual_memory().available//1024**2,
        ordering='three exact retries, then ALNS by model/case')
    for name,value in (('frozen_dataset.json',frozen),('dataset_manifest.json',suite),
        ('prior_300_rows.json',prior),('references.json',references),('reference_evidence.json',proofs),
        ('source_input_evidence.json',evidence),('environment.json',environment)):
        d.write(out/name,value)
    inputs={p.relative_to(out).as_posix():d.sha(p) for folder in ('inputs','cases','reference_evidence') for p in (out/folder).rglob('*') if p.is_file()}
    for name in ('frozen_dataset.json','dataset_manifest.json','prior_300_rows.json','references.json',
                 'reference_evidence.json','source_input_evidence.json','environment.json'):
        inputs[name]=d.sha(out/name)
    with zipfile.ZipFile(out/'source.zip','x',compression=zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(sources):archive.write(out/name,name)
    manifest=dict(schema_version=1,registered_at=d.now(),phase='small7_exact600_alns60',
        dataset=frozen['dataset'],dataset_sha256=frozen['manifest_sha256'],jobs=jobs,plan_count=39,
        source_hashes=sources,input_hashes=inputs,source_archive_hashes={'source.zip':d.sha(out/'source.zip')},
        total_call_budget=3960,workers=1,exact_threads=1,formulation='compact',m4_strengthening='none',
        external_initial_solution=False,reference_values_used_only_after_solving=True,
        worker_memory_mib=1536,minimum_available_mib=256,memory_pressure_grace_seconds=20,timeout_overhead=180,
        policy='600 seconds total from a new cold start, not continuation; same formulation/seed as prior300. ALNS 60 seconds each, independent seed42, no supplied solution or reference-based stopping.',
        timing='Full solve call and offline audit/process overhead separately recorded; internal incumbent timestamps are labelled separately.',
        solver_sources_match_prior=True,interpreter=sys.executable)
    d.write(out/'manifest.json',manifest);d.write(out/'manifest.sha256.json',dict(sha256=d.sha(out/'manifest.json')))
    (out/'.gitignore').write_text('/source/\n/driver.lock\n',encoding='utf-8')
    return summarize(out,manifest)


def summarize(out,manifest):
    refs={(r['case'],r['model']):r for r in d.read(out/'references.json')['references']}
    rows=[]
    for job in manifest['jobs']:
        attempts=d.attempt_dirs(out,job);complete=[p for p in attempts if d.complete_attempt(p)]
        row=d.read(complete[0]/'row.json') if complete else d.terminal_row(job,'RUNNING' if attempts else 'NOT_RUN')
        info=d.read(complete[0]/'result.json').get('info',{}) if complete and (complete[0]/'result.json').exists() else {}
        row.update(score(row,refs[row['case'],row['model']],info));rows.append(row)
    terminal=[r for r in rows if r['status'] not in ('NOT_RUN','RUNNING')]
    state=dict(planned=39,terminal=len(terminal),state='COMPLETED' if len(terminal)==39 else 'IN_PROGRESS',
        statuses=dict(Counter(r['status'] for r in rows)),audited_feasible=sum(r['valid'] for r in rows),
        reference_conflicts=sum(r['reference_conflict'] for r in rows),updated_at=d.now())
    d.write(out/'rows.json',rows);d.write(out/'progress.json',state)
    fields=['case','model','method','budget','status','valid','proven_optimal','objective','best_bound','mip_gap',
        'solve_wall_seconds','iterations','reference_objective','optimal_gap_percent','matches_known_optimum',
        'first_feasible_recorded_seconds','first_optimum_recorded_seconds','time_to_best_recorded_seconds',
        'reference_conflict','history_scope','error']
    with (out/'tables/runs.csv').open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');writer.writeheader();writer.writerows(rows)
    summary=[]
    for model in range(1,5):
        group=[r for r in rows if r['method']=='alns' and r['model']==model]
        valid=[r for r in group if r['valid']];gaps=[r['optimal_gap_percent'] for r in valid if r['optimal_gap_percent'] is not None]
        times=[r['first_optimum_recorded_seconds'] for r in valid if r['matches_known_optimum'] and r['first_optimum_recorded_seconds'] is not None]
        summary.append(dict(model=model,planned=9,finished=sum(r['status'] not in ('NOT_RUN','RUNNING') for r in group),
            feasible=len(valid),matches_known_optimum=sum(r['matches_known_optimum'] for r in group),
            mean_gap_percent=statistics.mean(gaps) if gaps else None,max_gap_percent=max(gaps) if gaps else None,
            mean_first_optimum_internal_seconds=statistics.mean(times) if times else None,
            max_first_optimum_internal_seconds=max(times) if times else None))
    d.write(out/'alns_summary.json',summary)
    lines=['# 7顾客：10分钟精确补测与1分钟ALNS','',f"状态：{state['state']}；完成{state['terminal']}/39。",'',
        '固定v4_1；单线程、种子42、串行。3次精确调用均重新冷启动、总预算600秒，原表达none；36次ALNS均独立60秒，不提供历史解、见证或最优值，不按最优参考提前停止。','',
        '## 10分钟精确补测','', '| 算例/模型 | 状态 | 审计可行 | 已证最优 | 秒 | GAP |','|---|---|---|---|---:|---:|']
    number=lambda v:'—' if v is None else f'{v:.3f}'
    for r in rows:
        if r['method']=='exact':lines.append(f"| {r['case']}/M{r['model']} | {r['status']} | {r['valid']} | {r['proven_optimal']} | {number(r.get('solve_wall_seconds'))} | {number(100*r['mip_gap'] if r.get('mip_gap') is not None else None)}% |")
    lines+=['','## 60秒ALNS','', '| 模型 | 完成 | 审计可行 | 达已知MILP最优值 | 平均GAP | 最大GAP | 内部首达最优平均秒 |','|---|---:|---:|---:|---:|---:|---:|']
    for r in summary:
        lines.append(f"| M{r['model']} | {r['finished']}/9 | {r['feasible']}/9 | {r['matches_known_optimum']}/9 | {number(r['mean_gap_percent'])}% | {number(r['max_gap_percent'])}% | {number(r['mean_first_optimum_internal_seconds'])} |")
    lines+=['','ALNS达到已知最优目标值不代表算法自身给出了最优证明。参考值对应整数时间MILP；最终路线独立审计，内部历史时间相对搜索入口，未对每个历史中间方案逐一独立重放。完整调用耗时及首次可行、首次匹配、迭代次数见tables/runs.csv。',
        '60秒是一次独立搜索的预算；搜索日程依赖预算，不能当作既有30秒运行的直接延长。单种子结果只描述本轮表现，不构成多种子稳定性结论。',
        '最优值以下超出数值容差的结果标为reference_conflict，不能按零GAP处理；无可行解不计入平均GAP。',
        '', '[逐次结果](tables/runs.csv) · [运行进度](progress.json) · [注册配置与哈希](manifest.json)','']
    (out/'report.md').write_text('\n'.join(lines),encoding='utf-8')
    return state


def run(out):
    manifest=d.read(out/'manifest.json');d.verify(out,manifest)
    with d.study_lock(out):
        begun=time.perf_counter()
        for job in manifest['jobs']:
            if (out/'STOP').exists():break
            attempts=d.attempt_dirs(out,job)
            if any(d.complete_attempt(p) for p in attempts):continue
            if attempts:raise RuntimeError(f'Incomplete prior attempt: {job["id"]}')
            print(f'START {job["id"]}',flush=True)
            row=q.execute(out,manifest,job);state=summarize(out,manifest)
            print(f"{state['terminal']}/39 {job['id']} {row['status']} valid={row['valid']} wall={row.get('solve_wall_seconds')}",flush=True)
        state=summarize(out,manifest)
        d.verify(out,manifest)
        d.write(out/'execution.json',dict(finished_at=d.now(),wall_seconds=time.perf_counter()-begun,progress=state))
        print(state,flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('prepare','run','worker','status'))
    parser.add_argument('--output',required=True);parser.add_argument('--job');parser.add_argument('--attempt',type=int,default=1)
    args=parser.parse_args();out=Path(args.output).resolve()
    if args.action=='prepare':print(prepare(out),flush=True)
    elif args.action=='run':run(out)
    elif args.action=='worker':q.worker(args)
    else:print(summarize(out,d.read(out/'manifest.json')),flush=True)
