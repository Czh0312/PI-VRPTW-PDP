"""Local reproducible M8 cold-start experiment; use tools/run.ps1."""
from pathlib import Path
import argparse
import csv
import json
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[2]
sys.path.insert(0, str(HERE/'source' if (HERE/'source').is_dir() else PROJECT))
from experiments.runs import run_thesis_completion as d
from experiments.runs import run_spacetime_quality as q


def summarize(out, manifest):
    rows = []
    for job in manifest['jobs']:
        attempts = d.attempt_dirs(out, job)
        completed = [p for p in attempts if d.complete_attempt(p)]
        rows.append(d.read(completed[0]/'row.json') if completed else
                    d.terminal_row(job, 'RUNNING' if attempts else 'NOT_RUN'))
    terminal = [r for r in rows if r['status'] not in ('RUNNING', 'NOT_RUN')]
    state = dict(planned=len(rows), completed=len(terminal),
        audited_feasible=sum(bool(r.get('valid')) for r in rows),
        proven_optimal=sum(bool(r.get('valid') and r.get('proven_optimal')) for r in rows),
        running=[r['case'] for r in rows if r['status']=='RUNNING'], updated_at=d.now())
    d.write(out/'rows.json', rows)
    d.write(out/'progress.json', state)
    fields = ['case','status','valid','proven_optimal','objective','best_bound','mip_gap',
              'k','pool','solve_wall_seconds','build_seconds','mip_seconds',
              'offline_audit_seconds','variables','constraints','complete_count_domain','proof_scope','error']
    with (out/'runs.csv').open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)
    def num(value, digits=4):
        return '—' if value is None else f'{value:.{digits}f}'
    lines = ['# 当前模型8小规模：每例300秒精确求解', '',
        f"完成 {state['completed']}/9；独立审计可行 {state['audited_feasible']}/9；已证最优 {state['proven_optimal']}/9。", '',
        '冻结正式数据 formal_benchmark27_spacetime_v4_1，S01—S09每例7顾客。',
        '完整K候选域、默认expanded统一MILP、直接Gurobi、单线程、种子42、逐例串行冷启动，零相对与绝对最优间隙，不使用外部初解。',
        '沿用各例原有economic.json（箱池系数5），没有另行改价。',
        '每次300秒覆盖模型入口、建模和优化；求解调用墙钟另含解提取和内部审计，进程启动及再次独立审计另计。', '',
        '| 算例 | 状态 | 审计可行 | 已证最优 | 目标值 | 下界 | GAP | 调用秒 | 建模秒 | K | 箱池 |',
        '|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|']
    for r in rows:
        gap = None if r.get('mip_gap') is None else 100*r['mip_gap']
        lines.append('| '+' | '.join([r['case'],r['status'],str(r.get('valid',False)),
            str(r.get('proven_optimal',False)),num(r.get('objective')),num(r.get('best_bound')),
            num(gap,2)+'%',num(r.get('solve_wall_seconds'),2),num(r.get('build_seconds'),2),
            str(r.get('k','—')),str(r.get('pool','—'))])+' |')
    lines += ['', '限时找到可行解不等于证明最优；限时无解不等于不可行。',
              '完整求解日志、结果、独立成本核算和两周期重放审计见 runs/；配置及源码哈希见 manifest.json。', '']
    (out/'report.md').write_text('\n'.join(lines), encoding='utf-8')
    return state


def prepare(out):
    if (out/'manifest.json').exists():
        raise ValueError('Refusing to replace an existing manifest')
    from experiments.runs.run_model6_comparison import freeze_case
    import gurobipy as gp
    import psutil
    frozen = d.read(PROJECT/'config/experiments/benchmark27_spacetime_v4_1/frozen.json')
    dataset = PROJECT/frozen['dataset']
    for filename,key in [('manifest.json','manifest_sha256'),('selection.json','selection_sha256'),
                         ('case_index.csv','case_index_sha256')]:
        if d.sha(dataset/filename)!=frozen[key]:
            raise ValueError(f'Frozen dataset hash differs: {filename}')
    records = sorted((r for r in d.read(dataset/'manifest.json')['cases']
                      if r['model']==8 and r['size']=='small'),key=lambda r:r['base_case_id'])
    assert len(records)==9 and all(r['customer_count']==7 for r in records)
    for name in ('inputs','cases','runs'):
        (out/name).mkdir(exist_ok=True)
    source_hashes = {f'source/{k}':v for k,v in d.snapshot(PROJECT,out/'source').items()}
    source_hashes['driver.py'] = d.sha(out/'driver.py')
    jobs,evidence = [],{}
    for r in records:
        case = r['base_case_id']
        config,hashes = freeze_case(r['case_path'],out,f'{case}_m8')
        config.update(solver='exact',exact_time=300,seed=42,exact_strategy='milp',
                      milp_options=dict(threads=1,formulation='expanded'))
        case_path=f'cases/{case}_m8.json'
        d.write(out/case_path,config)
        evidence.update(hashes)
        jobs.append(dict(id=f'{case}_m8_exact_300s_s42',case=case,model=8,method='exact',
            budget=300,seed=42,version='current',iterations=10**9,size='small',structure='small',
            split='formal',family=r['family_id'],source_block=r['source_block'],
            customer_count=7,case_path=case_path))
    d.write(out/'frozen_dataset.json',frozen)
    d.write(out/'source_input_evidence.json',evidence)
    d.write(out/'environment.json',dict(captured_at=d.now(),python=sys.version,
        interpreter=sys.executable,gurobi_version=list(gp.gurobi.version()),
        physical_cpus=psutil.cpu_count(logical=False),logical_cpus=psutil.cpu_count(),
        total_memory_mib=psutil.virtual_memory().total//1024**2,
        available_memory_mib=psutil.virtual_memory().available//1024**2))
    inputs={p.relative_to(out).as_posix():d.sha(p) for folder in ('inputs','cases')
            for p in (out/folder).rglob('*') if p.is_file()}
    for name in ('frozen_dataset.json','source_input_evidence.json','environment.json'):
        inputs[name]=d.sha(out/name)
    manifest=dict(schema_version=1,phase='small7_m8_exact300',registered_at=d.now(),jobs=jobs,
        workers=1,exact_threads=1,exact_formulation='expanded',external_starts=False,
        dataset=frozen['dataset'],source_hashes=source_hashes,input_hashes=inputs,
        timeout_overhead=180,worker_memory_mib=3072,minimum_available_mib=256,
        memory_pressure_grace_seconds=20,interpreter=sys.executable,
        commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=PROJECT,text=True).strip(),
        economics='Original frozen economic profiles; no price overrides',
        budget_scope='300 seconds for model entry/build/optimize; extraction/audit/process overhead reported separately')
    d.write(out/'manifest.json',manifest)
    d.write(out/'manifest.sha256.json',dict(sha256=d.sha(out/'manifest.json')))
    (out/'.gitignore').write_text('/source/\n/driver.lock\n',encoding='utf-8')
    print(summarize(out,manifest),flush=True)


def worker(args):
    # Preserve the existing logging/independent audit worker while selecting
    # the current default expression instead of its compact study expression.
    from models.routing import service
    original = service.solve_routing
    def solve(**kwargs):
        kwargs.update(exact_strategy='milp',milp_options=dict(threads=1,formulation='expanded'))
        return original(**kwargs)
    service.solve_routing=solve
    q.worker(args)


def run(out):
    manifest=d.read(out/'manifest.json')
    d.verify(out,manifest)
    with d.study_lock(out):
        for job in manifest['jobs']:
            if (out/'STOP').exists():
                break
            attempts=d.attempt_dirs(out,job)
            if any(d.complete_attempt(p) for p in attempts):
                continue
            if attempts:
                raise RuntimeError(f'Incomplete attempt: {job["id"]}')
            print(f'START {job["id"]}',flush=True)
            row=q.execute(out,manifest,job)
            state=summarize(out,manifest)
            print(json.dumps(dict(progress=state,case=job['case'],status=row['status'],
                objective=row.get('objective'),bound=row.get('best_bound'),gap=row.get('mip_gap'),
                wall=row.get('solve_wall_seconds')),ensure_ascii=False),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['prepare','run','worker','status'])
    parser.add_argument('--output',required=True)
    parser.add_argument('--job')
    parser.add_argument('--attempt',type=int,default=1)
    args=parser.parse_args()
    out=Path(args.output).resolve()
    if args.action=='prepare':prepare(out)
    elif args.action=='run':run(out)
    elif args.action=='worker':worker(args)
    else:print(summarize(out,d.read(out/'manifest.json')),flush=True)
