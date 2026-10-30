"""Windows IO adapter; frozen solver sources, inputs and manifest are unchanged."""
from pathlib import Path
import argparse
import csv
import importlib.util
import json
import sys

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE/'source'))
spec=importlib.util.spec_from_file_location('frozen_study',HERE/'driver.py')
study=importlib.util.module_from_spec(spec)
spec.loader.exec_module(study)
d,q=study.d,study.q
from experiments.runs.spacetime_windows_io_adapter import atomic_write,WorkerSubprocessProxy
d.write=atomic_write
q.subprocess=WorkerSubprocessProxy(HERE/'driver.py',Path(__file__))


def summary(out,manifest):
    rows,attempts_all=[],[]
    for job in manifest['jobs']:
        complete=[d.read(p/'row.json') for p in d.attempt_dirs(out,job) if d.complete_attempt(p)]
        attempts_all.extend(complete)
        eligible=[r for r in complete if r['status']!='INTERRUPTED_IO']
        rows.append(eligible[0] if eligible else d.terminal_row(job,'NOT_RUN'))
    state=dict(planned=9,completed=sum(r['status']!='NOT_RUN' for r in rows),
        feasible=sum(bool(r.get('valid')) for r in rows),
        optimal=sum(bool(r.get('valid') and r.get('proven_optimal')) for r in rows),
        interrupted_attempts=sum(r['status']=='INTERRUPTED_IO' for r in attempts_all),updated_at=d.now())
    d.write(out/'recovered_rows.json',rows)
    d.write(out/'all_attempts.json',attempts_all)
    d.write(out/'recovered_progress.json',state)
    fields=['case','status','valid','proven_optimal','objective','best_bound','mip_gap','k','pool',
            'solve_wall_seconds','build_seconds','mip_seconds','variables','constraints','attempt','error']
    with (out/'recovered_runs.csv').open('w',encoding='utf-8-sig',newline='') as handle:
        writer=csv.DictWriter(handle,fieldnames=fields,extrasaction='ignore')
        writer.writeheader();writer.writerows(rows)
    def n(x):return '—' if x is None else f'{x:.4f}'
    lines=['# 当前模型8小规模：每例300秒精确求解','',
        f"完成 {state['completed']}/9；可行 {state['feasible']}/9；已证最优 {state['optimal']}/9。",'',
        '冻结正式数据formal_benchmark27_spacetime_v4_1；S01—S09均为7顾客；完整K=1—7。',
        '默认expanded统一MILP、直接Gurobi、单线程、种子42、串行冷启动、MIPGap=0、MIPGapAbs=0。',
        '原始economic.json保持（箱池系数5）；无外部初解、回调或自定义强化。',
        '300秒包含模型入口、建模和优化；调用墙钟另含提取及内部审计，进程和再次独立审计开销另列。',
        'S02首次调用被Windows状态文件共享冲突中断，保留attempt_001和INTERRUPTED_IO记录，另以attempt_002独立冷启动复测。该中断不算作超时或完整300秒调用。',
        'recovery_driver.py仅使用仓库现有atomic_write重试处理状态输出；原driver.py、manifest和全部求解源码／输入哈希保持。','',
        '| 算例 | 状态 | 可行 | 最优证明 | 目标 | 下界 | GAP(%) | 调用秒 | 建模秒 | K | 箱池 |',
        '|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|']
    for r in rows:
        lines.append('| '+' | '.join([r['case'],r['status'],str(r.get('valid',False)),
            str(r.get('proven_optimal',False)),n(r.get('objective')),n(r.get('best_bound')),
            n(None if r.get('mip_gap') is None else 100*r['mip_gap']),n(r.get('solve_wall_seconds')),
            n(r.get('build_seconds')),str(r.get('k','—')),str(r.get('pool','—'))])+' |')
    lines+=['','限时无可行解不等于不可行。完整结果、Gurobi日志、独立成本和两周期重放审计见runs/。',
            '本报告只统计上述9例M8求解，没有运行全套单元测试。','']
    (out/'recovered_report.md').write_text('\n'.join(lines),encoding='utf-8')
    return state


def run(out):
    manifest=d.read(out/'manifest.json');d.verify(out,manifest)
    with d.study_lock(out):
        job=next(j for j in manifest['jobs'] if j['case']=='S02')
        abandoned=out/'runs'/job['id']/'attempt_001'
        if abandoned.exists() and not d.complete_attempt(abandoned):
            row=d.terminal_row(job,'INTERRUPTED_IO','Controller WinError 5 replacing live.json; worker terminated by controller; not a complete budget run')
            row['attempt']=1
            d.finalize_attempt(abandoned,row,('gurobi.log','launch.json'))
        for job in manifest['jobs']:
            completed=[d.read(p/'row.json') for p in d.attempt_dirs(out,job) if d.complete_attempt(p)]
            if any(r['status']!='INTERRUPTED_IO' for r in completed):continue
            if (out/'STOP').exists():break
            print('START '+job['id'],flush=True)
            row=q.execute(out,manifest,job)
            print(json.dumps(dict(progress=summary(out,manifest),case=job['case'],status=row['status'],
                objective=row.get('objective'),bound=row.get('best_bound'),gap=row.get('mip_gap'),
                wall=row.get('solve_wall_seconds')),ensure_ascii=False),flush=True)
        print(summary(out,manifest),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['run','worker','status'])
    parser.add_argument('--output',required=True)
    parser.add_argument('--job')
    parser.add_argument('--attempt',type=int,default=1)
    args=parser.parse_args();out=Path(args.output).resolve()
    if args.action=='worker':study.worker(args)
    elif args.action=='run':run(out)
    else:print(summary(out,d.read(out/'manifest.json')),flush=True)
