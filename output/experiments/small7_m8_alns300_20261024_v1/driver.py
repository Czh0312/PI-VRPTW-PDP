"""Frozen M8 ALNS experiment, 300 seconds per small case, seed 42."""
from pathlib import Path
import argparse
import csv
import json
import subprocess
import sys
import time

HERE=Path(__file__).resolve().parent
PROJECT=HERE.parents[2]
sys.path.insert(0,str(HERE/'source' if (HERE/'source').is_dir() else PROJECT))
from experiments.runs import run_thesis_completion as d
from experiments.runs import run_spacetime_quality as q
from experiments.runs.spacetime_windows_io_adapter import atomic_write
d.write=atomic_write


def summary(out,manifest):
    rows=[]
    for job in manifest['jobs']:
        attempts=d.attempt_dirs(out,job)
        completed=[p for p in attempts if d.complete_attempt(p)]
        rows.append(d.read(completed[0]/'row.json') if completed else
                    d.terminal_row(job,'RUNNING' if attempts else 'NOT_RUN'))
    state=dict(planned=9,completed=sum(r['status'] not in ('RUNNING','NOT_RUN') for r in rows),
        audited_feasible=sum(bool(r.get('valid')) for r in rows),
        proven_optimal=sum(bool(r.get('proven_optimal')) for r in rows),updated_at=d.now())
    d.write(out/'rows.json',rows);d.write(out/'progress.json',state)
    fields=['case','status','valid','objective','k','pool','minimum_pool','solve_wall_seconds',
        'first_observed_feasible_seconds','first_observed_cost','iterations','periodic_valid',
        'offline_audit_seconds','proven_optimal','error']
    with (out/'runs.csv').open('w',encoding='utf-8-sig',newline='') as handle:
        writer=csv.DictWriter(handle,fieldnames=fields,extrasaction='ignore')
        writer.writeheader();writer.writerows(rows)
    def num(x):return '—' if x is None else f'{x:.4f}'
    lines=['# 模型8小规模：每例300秒ALNS','',
        f"完成 {state['completed']}/9；独立审计可行 {state['audited_feasible']}/9；ALNS不提供最优性证明。",'',
        '正式冻结数据formal_benchmark27_spacetime_v4_1，S01—S09均7顾客，完整输入K候选域1—7。',
        '每例300秒、种子42、最大迭代数10^9，逐例串行独立冷启动，无外部初解。',
        '物理输入、费用及K域与此前exact300实验逐字节／字段核对一致，箱池系数仍为5。',
        'ALNS内部自适应分配各K的预算；未访问或未找到可行解的K不视为已证明不可行。',
        '目标值仅接受独立成本复算一致且连续两个周期重放通过的方案。',
        '调用墙钟含算法内部提取与审计；进程启动、再次独立审计另列。',
        '首次确认可行时间来自跨K候选返回后的确认记录，不能解释为算法首次构造出可行解的精确时刻。','',
        '| 算例 | 状态 | 审计可行 | 总成本 | K | 箱池 | 调用秒 | 首次确认可行秒 | 两周期重放 |',
        '|---|---|---|---:|---:|---:|---:|---:|---|']
    for r in rows:
        lines.append('| '+' | '.join([r['case'],r['status'],str(r.get('valid',False)),
            num(r.get('objective')),str(r.get('k','—')),str(r.get('pool','—')),
            num(r.get('solve_wall_seconds')),num(r.get('first_observed_feasible_seconds')),
            str(r.get('periodic_valid',False))])+' |')
    lines+=['','完整解、独立成本复算、两周期重放、跨K轨迹及日志见runs/；源码及输入哈希见manifest.json。',
        '本实验未运行全套单元测试；没有把ALNS可行解声明为精确最优解。','']
    (out/'report.md').write_text('\n'.join(lines),encoding='utf-8')
    return state


def prepare(out):
    if (out/'manifest.json').exists():raise ValueError('Existing manifest')
    from experiments.runs.run_model6_comparison import freeze_case
    import psutil
    frozen=d.read(PROJECT/'config/experiments/benchmark27_spacetime_v4_1/frozen.json')
    dataset=PROJECT/frozen['dataset']
    for filename,key in [('manifest.json','manifest_sha256'),('selection.json','selection_sha256'),
                         ('case_index.csv','case_index_sha256')]:
        assert d.sha(dataset/filename)==frozen[key],filename
    records=sorted((r for r in d.read(dataset/'manifest.json')['cases']
                    if r['model']==8 and r['size']=='small'),key=lambda r:r['base_case_id'])
    assert len(records)==9 and all(r['customer_count']==7 for r in records)
    for folder in ('inputs','cases','runs'):(out/folder).mkdir(exist_ok=True)
    sources={f'source/{k}':v for k,v in d.snapshot(PROJECT,out/'source').items()}
    sources['driver.py']=d.sha(out/'driver.py')
    prior=PROJECT/'output/experiments/small7_m8_exact300_20261024_v1'
    jobs,evidence,matched=[],{},[]
    for r in records:
        case=r['base_case_id']
        config,hashes=freeze_case(r['case_path'],out,f'{case}_m8')
        old=d.read(prior/f'cases/{case}_m8.json')
        for key in ('data','orders','routing_config','economic_config','operating_policy'):
            assert d.sha(Path(config[key]))==d.sha(Path(old[key])),(case,key)
        assert config['joint_batch_options']==old['joint_batch_options']
        assert not config.get('initial_joint_result')
        config.update(solver='alns',alns_time=300,alns_iter=10**9,seed=42)
        for key in ('milp_options','exact_strategy'):config.pop(key,None)
        case_path=f'cases/{case}_m8.json'
        d.write(out/case_path,config);evidence.update(hashes);matched.append(case)
        jobs.append(dict(id=f'{case}_m8_alns_300s_s42',case=case,model=8,method='alns',
            budget=300,seed=42,version='current',iterations=10**9,size='small',structure='small',
            split='formal',family=r['family_id'],source_block=r['source_block'],
            customer_count=7,case_path=case_path))
    old_sources=d.read(prior/'manifest.json')['source_hashes']
    source_differences=[k for k in sources if k!='driver.py' and old_sources.get(k)!=sources[k]]
    for name,value in [('frozen_dataset.json',frozen),('source_input_evidence.json',evidence),
        ('comparison_evidence.json',dict(prior=str(prior),matched_cases=matched,
            physical_economic_policy_and_K_inputs_match=True,source_differences=source_differences)),
        ('environment.json',dict(captured_at=d.now(),interpreter=sys.executable,python=sys.version,
            physical_cpus=psutil.cpu_count(logical=False),logical_cpus=psutil.cpu_count(),
            total_memory_mib=psutil.virtual_memory().total//1024**2,
            available_memory_mib=psutil.virtual_memory().available//1024**2))]:d.write(out/name,value)
    inputs={p.relative_to(out).as_posix():d.sha(p) for folder in ('inputs','cases')
            for p in (out/folder).rglob('*') if p.is_file()}
    for name in ('frozen_dataset.json','source_input_evidence.json','comparison_evidence.json','environment.json'):
        inputs[name]=d.sha(out/name)
    manifest=dict(schema_version=1,phase='small7_m8_alns300',registered_at=d.now(),jobs=jobs,
        workers=1,external_starts=False,dataset=frozen['dataset'],source_hashes=sources,input_hashes=inputs,
        timeout_overhead=180,worker_memory_mib=1536,minimum_available_mib=192,
        memory_pressure_grace_seconds=20,interpreter=sys.executable,
        commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=PROJECT,text=True).strip(),
        economics='Original frozen profiles; stock coefficient 5; no price overrides',
        budget_scope='300 seconds total ALNS including internal K allocation; process/offline audit separately')
    d.write(out/'manifest.json',manifest)
    d.write(out/'manifest.sha256.json',dict(sha256=d.sha(out/'manifest.json')))
    (out/'.gitignore').write_text('/source/\n/driver.lock\n',encoding='utf-8')
    print(summary(out,manifest),flush=True)


def worker(args):
    out=Path(args.output).resolve()
    manifest=d.read(out/'manifest.json')
    job=next(j for j in manifest['jobs'] if j['id']==args.job)
    path=out/'runs'/job['id']/f'attempt_{args.attempt:03d}'
    observe=d.observe_counts
    def logged_observer(aggregate,trace,history,begun):
        observed=observe(aggregate,trace,history,begun)
        def run(problem,*,solve_candidate,**kwargs):
            def candidate(child,**child_kwargs):
                result=solve_candidate(child,**child_kwargs)
                d.write(path/'search_live.json',dict(elapsed=time.perf_counter()-begun,
                    batch_count=len(child.operating_policy.batches),
                    child_audit_valid=bool(result.get('audit',{}).get('valid')),
                    child_objective=result['info'].get('objective'),
                    scope='returned_child_candidate_before_parent_final_audit',updated_at=d.now()))
                return result
            return observed(problem,solve_candidate=candidate,**kwargs)
        return run
    d.observe_counts=logged_observer
    q.worker(args)


def run(out):
    manifest=d.read(out/'manifest.json');d.verify(out,manifest)
    with d.study_lock(out):
        for job in manifest['jobs']:
            if (out/'STOP').exists():break
            attempts=d.attempt_dirs(out,job)
            if any(d.complete_attempt(p) for p in attempts):continue
            if attempts:raise RuntimeError(f'Incomplete attempt: {job["id"]}')
            print('START '+job['id'],flush=True)
            row=q.execute(out,manifest,job)
            print(json.dumps(dict(progress=summary(out,manifest),case=job['case'],
                status=row['status'],valid=row['valid'],objective=row.get('objective'),
                k=row.get('k'),pool=row.get('pool'),wall=row.get('solve_wall_seconds')),ensure_ascii=False),flush=True)
        print(summary(out,manifest),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['prepare','run','worker','status'])
    parser.add_argument('--output',required=True)
    parser.add_argument('--job')
    parser.add_argument('--attempt',type=int,default=1)
    args=parser.parse_args();out=Path(args.output).resolve()
    if args.action=='prepare':prepare(out)
    elif args.action=='run':run(out)
    elif args.action=='worker':worker(args)
    else:print(summary(out,d.read(out/'manifest.json')),flush=True)
