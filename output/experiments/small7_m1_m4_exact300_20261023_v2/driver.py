"""Frozen 36-call M1-M4 exact check of the accepted seven-customer benchmark.

Uses the existing isolated worker, compact MILP, memory monitor and independent
two-cycle auditor. No solver or frozen dataset is modified.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
from pathlib import Path
import shutil
import statistics
import subprocess
import sys
import time
import zipfile

HERE = Path(__file__).resolve().parent
ROOT = HERE/'source' if (HERE/'source').is_dir() else Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from experiments.runs import run_thesis_completion as d
from experiments.runs import run_spacetime_quality as q
from experiments.runs.spacetime_windows_io_adapter import atomic_write

d.write = atomic_write


def selected_jobs(suite):
    records = sorted((r for r in suite['cases'] if r['size']=='small' and r['model'] in (1,2,3,4)),
                     key=lambda r:(r['model'],r['base_case_id']))
    expected = {(f'S{i:02}',m) for i in range(1,10) for m in range(1,5)}
    if len(records)!=36 or {(r['base_case_id'],r['model']) for r in records}!=expected:
        raise ValueError('Require all nine small cases for each of M1-M4 exactly once')
    if any(r['customer_count']!=7 for r in records):
        raise ValueError('Only the frozen seven-customer small cases are allowed')
    return records, [dict(id=f'{r["base_case_id"]}_m{r["model"]}_exact_300s_s42',
        case=r['base_case_id'],model=r['model'],method='exact',budget=300,seed=42,version='current',
        iterations=10**9,size='small',structure='small',split='formal',family=r['family_id'],
        source_block=r['source_block'],customer_count=7,
        case_path=f'cases/{r["base_case_id"]}_m{r["model"]}.json') for r in records]


def prepare(out):
    if out.exists() and any(out.iterdir()):
        raise ValueError(f'Refusing to overwrite study: {out}')
    from tools.audit.audit_benchmark27_spacetime import audit_design
    from tools.audit.certify_formal_benchmark27 import verify_witnesses
    from experiments.runs.run_model6_comparison import freeze_case
    import psutil
    import gurobipy as gp

    frozen_path=ROOT/'config/experiments/benchmark27_spacetime_v4_1/frozen.json'
    frozen=d.read(frozen_path)
    dataset=ROOT/frozen['dataset']
    for filename,key in (('manifest.json','manifest_sha256'),('selection.json','selection_sha256'),
                         ('case_index.csv','case_index_sha256')):
        if d.sha(dataset/filename)!=frozen[key]:
            raise ValueError(f'Frozen dataset hash differs: {filename}')
    suite=d.read(dataset/'manifest.json')
    records,jobs=selected_jobs(suite)
    audit=audit_design(dataset)
    witnesses=verify_witnesses(dataset)
    out.mkdir(parents=True,exist_ok=True)
    for folder in ('source','inputs','cases','runs','tables'):
        (out/folder).mkdir()
    source_hashes={f'source/{name}':value for name,value in d.snapshot(ROOT,out/'source').items()}
    shutil.copyfile(__file__,out/'driver.py')
    source_hashes['driver.py']=d.sha(out/'driver.py')
    evidence={}
    for record,job in zip(records,jobs):
        cfg,original=freeze_case(record['case_path'],out,f'{record["base_case_id"]}_m{record["model"]}')
        cfg.update(exact_strategy='milp',milp_options=dict(threads=1,formulation='compact'))
        d.write(out/job['case_path'],cfg)
        evidence.update(original)
    memory=psutil.virtual_memory()
    environment=dict(captured_at=d.now(),interpreter=sys.executable,python=sys.version,
        gurobi_version=list(gp.gurobi.version()),physical_cpus=psutil.cpu_count(logical=False),
        logical_cpus=psutil.cpu_count(),total_memory_mib=memory.total//1024**2,
        available_memory_mib=memory.available//1024**2,workers=1,exact_threads=1,
        ordering='model_then_case',numerical_library_threads=1)
    for name,value in (('frozen_dataset.json',frozen),('dataset_manifest.json',suite),
            ('input_audit.json',audit),('witness_verification.json',witnesses),
            ('source_input_evidence.json',evidence),('environment.json',environment)):
        d.write(out/name,value)
    inputs={p.relative_to(out).as_posix():d.sha(p) for folder in ('inputs','cases')
            for p in (out/folder).rglob('*') if p.is_file()}
    for name in ('frozen_dataset.json','dataset_manifest.json','input_audit.json',
                 'witness_verification.json','source_input_evidence.json','environment.json'):
        inputs[name]=d.sha(out/name)
    archive=out/'source.zip'
    with zipfile.ZipFile(archive,'x',compression=zipfile.ZIP_DEFLATED) as z:
        for name in sorted(source_hashes):
            z.write(out/name,name)
    manifest=dict(schema_version=1,phase='small7_m1_m4_exact_300s',registered_at=d.now(),
        jobs=jobs,plan_count=36,workers=1,total_call_budget=10800,timeout_overhead=180,
        worker_memory_mib=1536,minimum_available_mib=256,memory_pressure_grace_seconds=20,
        resource_basis='Sequential single-thread workers; memory availability recorded in environment.json and monitored throughout execution.',
        source_hashes=source_hashes,input_hashes=inputs,source_archive_hashes={'source.zip':d.sha(archive)},
        commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        interpreter=sys.executable,dataset=frozen['dataset'],dataset_sha256=frozen['manifest_sha256'],
        exact_threads=1,formulation='compact',m4_strengthening='none',external_initial_solution=False,
        budget_scope='300 seconds including precheck/build/optimize; extraction/audit and process startup reported separately',
        policy='Every model/case is an independent cold start; retain all timeouts and failures; no long-budget extensions.')
    d.write(out/'manifest.json',manifest)
    d.write(out/'manifest.sha256.json',dict(sha256=d.sha(out/'manifest.json')))
    (out/'.gitignore').write_text('/source/\n/driver.lock\n',encoding='utf-8')
    return summarize(out,manifest)


def summarize(out,manifest):
    rows=[]
    for job in manifest['jobs']:
        attempts=d.attempt_dirs(out,job)
        complete=[p for p in attempts if d.complete_attempt(p)]
        rows.append(d.read(complete[0]/'row.json') if complete else
                    d.terminal_row(job,'RUNNING' if attempts else 'NOT_RUN'))
    terminal=[r for r in rows if r['status'] not in ('RUNNING','NOT_RUN')]
    state=dict(planned=36,terminal=len(terminal),optimal=sum(r.get('proven_optimal',False) for r in rows),
        audited_feasible=sum(r.get('valid',False) for r in rows),statuses=dict(Counter(r['status'] for r in rows)),
        updated_at=d.now(),state='COMPLETED' if len(terminal)==36 else 'IN_PROGRESS',running=[r['id'] for r in rows if r['status']=='RUNNING'])
    d.write(out/'rows.json',rows)
    d.write(out/'progress.json',state)
    fields=['case','model','customer_count','status','valid','proven_optimal','objective','best_bound',
        'mip_gap','solve_wall_seconds','solver_runtime','build_seconds','mip_seconds','offline_audit_seconds',
        'variables','constraints','proof_scope','error']
    with (out/'tables/runs.csv').open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore')
        writer.writeheader();writer.writerows(rows)
    lines=['# 当前小规模M1—M4：每次300秒精确求解','',
        f'状态：{state["state"]}；完成{len(terminal)}/36，已证最优{state["optimal"]}/36，独立审计可行{state["audited_feasible"]}/36。','',
        '冻结spacetime_v4，S01—S09均7位顾客；M1—M4各9例。直接Gurobi MILP，compact等价表达，单线程、种子42、串行冷启动；不使用外部初解。',
        '每次300秒覆盖预检、建模与优化；最终提取、审计及进程开销另记。最优证明与找到可行解分别统计；内存保护中断不能解释为超时或不可行。','',
        '| 模型 | 已完成 | 已证最优 | 审计可行 | 成功最优平均秒 | 成功最优最长秒 |',
        '|---|---:|---:|---:|---:|---:|']
    for model in range(1,5):
        group=[r for r in rows if r['model']==model]
        optimal=[r for r in group if r.get('proven_optimal') and r.get('valid')]
        times=[r['solve_wall_seconds'] for r in optimal]
        avg=f'{statistics.mean(times):.2f}' if times else '—'
        maximum=f'{max(times):.2f}' if times else '—'
        finished=sum(r['status'] not in ('RUNNING','NOT_RUN') for r in group)
        lines.append(f'| M{model} | {finished}/9 | {len(optimal)}/9 | {sum(r.get("valid",False) for r in group)}/9 | {avg} | {maximum} |')
    lines+=['','| 算例 | M1 | M2 | M3 | M4 |','|---|---|---|---|---|']
    for case in (f'S{i:02}' for i in range(1,10)):
        cells=[]
        for model in range(1,5):
            row=next(r for r in rows if r['case']==case and r['model']==model)
            elapsed=row.get('solve_wall_seconds')
            label=f'最优 {elapsed:.2f}s' if row.get('proven_optimal') else row['status']
            if row.get('valid') and not row.get('proven_optimal'):
                gap=row.get('mip_gap')
                label=f'限时可行 GAP={gap*100:.2f}%' if gap is not None else '可行，未证最优'
            cells.append(label)
        lines.append('| '+case+' | '+' | '.join(cells)+' |')
    lines+=['','[逐次结果](tables/runs.csv)；每次完整解、Gurobi日志与独立审计见runs/。',
        '耗时受当前机器负载影响，仅代表本次冻结配置与300秒预算。顾客子集存在同源重叠，不能视作九个独立原始来源。','']
    (out/'report.md').write_text('\n'.join(lines),encoding='utf-8')
    return state


def run(out):
    manifest=d.read(out/'manifest.json')
    d.verify(out,manifest)
    with d.study_lock(out):
        started=time.perf_counter()
        for job in manifest['jobs']:
            if (out/'STOP').exists():
                break
            attempts=d.attempt_dirs(out,job)
            if any(d.complete_attempt(p) for p in attempts):
                continue
            if attempts:
                raise RuntimeError(f'Incomplete prior attempt requires explicit investigation: {job["id"]}')
            print(f'START {job["id"]}',flush=True)
            row=q.execute(out,manifest,job)
            state=summarize(out,manifest)
            print(f'{state["terminal"]}/36 {job["id"]} {row["status"]} valid={row["valid"]} wall={row.get("solve_wall_seconds")} gap={row.get("mip_gap")}',flush=True)
        state=summarize(out,manifest)
        d.write(out/'execution.json',dict(started_to_return_seconds=time.perf_counter()-started,finished_at=d.now(),progress=state))
        print(state,flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('prepare','run','worker','status'))
    parser.add_argument('--output',required=True)
    parser.add_argument('--job')
    parser.add_argument('--attempt',type=int,default=1)
    args=parser.parse_args();out=Path(args.output).resolve()
    if args.action=='prepare': print(prepare(out),flush=True)
    elif args.action=='run': run(out)
    elif args.action=='worker': q.worker(args)
    else: print(summarize(out,d.read(out/'manifest.json')),flush=True)
