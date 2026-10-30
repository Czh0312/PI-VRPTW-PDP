"""User-requested K=1..4 experiment; run only S01, at most 1800 seconds."""
from pathlib import Path
import argparse
import json
import math
import shutil
import subprocess
import sys

HERE=Path(__file__).resolve().parent
PROJECT=HERE.parents[2]
sys.path.insert(0,str(HERE/'source' if (HERE/'source').is_dir() else PROJECT))
from experiments.runs import run_thesis_completion as d
from experiments.runs import run_spacetime_quality as q
from experiments.runs.spacetime_windows_io_adapter import atomic_write
d.write=atomic_write
COUNTS=[1,2,3,4]


def summarize(out,manifest):
    job=manifest['jobs'][0]
    attempts=d.attempt_dirs(out,job)
    completed=[p for p in attempts if d.complete_attempt(p)]
    row=d.read(completed[0]/'row.json') if completed else d.terminal_row(job,'RUNNING' if attempts else 'NOT_RUN')
    d.write(out/'rows.json',[row])
    state=dict(case='S01',status=row['status'],allowed_K=COUNTS,budget_seconds=1800,
        valid=row.get('valid',False),proven_optimal=row.get('proven_optimal',False),updated_at=d.now())
    d.write(out/'progress.json',state)
    def n(value):return '—' if value is None else f'{value:.6f}'
    fields=[('状态',row['status']),('独立审计可行',str(row.get('valid',False))),
        ('已证最优',str(row.get('proven_optimal',False))),('目标值',n(row.get('objective'))),
        ('Gurobi下界',n(row.get('best_bound'))),
        ('GAP(%)',n(None if row.get('mip_gap') is None else row['mip_gap']*100)),
        ('批次数K',str(row.get('k','—'))),('箱池',str(row.get('pool','—'))),
        ('建模秒',n(row.get('build_seconds'))),('优化秒',n(row.get('mip_seconds'))),
        ('求解调用墙钟秒',n(row.get('solve_wall_seconds'))),
        ('再次独立审计秒',n(row.get('offline_audit_seconds'))),
        ('变量数',str(row.get('variables','—'))),('约束数',str(row.get('constraints','—')))]
    lines=['# M8 / S01 / K=1—4 / 精确求解1800秒上限','',
        '按用户要求将本轮小规模实验的K域设为1、2、3、4；已准备S01—S09配置，本轮仅求解S01。',
        '正式冻结数据保持；物理输入、费用、最短阶段1分钟和箱池1—7保持。',
        '默认expanded统一MILP、单线程、种子42、冷启动，无外部初解、提示、自定义回调或强化。',
        '1800秒包含模型入口、建模及优化；提前最优则结束。提取、内部审计、进程启动和再次审计另按实际记录。',
        '最优性及下界只覆盖本轮允许的K=1—4，不覆盖原K=1—7。',
        '此前S01/ALNS已审计成本35.50906153797168、K=2、箱池4，属于本轮允许域；它仅供事后比较，没有输入本轮求解器。','',
        '| 指标 | 结果 |','|---|---|']
    lines += [f'| {key} | {value} |' for key,value in fields]
    lines += ['','完整Gurobi日志、结果、独立成本核算及两周期重放见runs/；本轮不运行其余8例。',
        'TIME_LIMIT不表示不可行；不能从一次超时可靠外推证明最优还需多久。','']
    (out/'report.md').write_text('\n'.join(lines),encoding='utf-8')
    return state


def prepare(out):
    if (out/'manifest.json').exists():raise ValueError('Existing manifest')
    from experiments.runs.run_model6_comparison import freeze_case
    import psutil
    import gurobipy as gp
    frozen=d.read(PROJECT/'config/experiments/benchmark27_spacetime_v4_1/frozen.json')
    dataset=PROJECT/frozen['dataset']
    for name,key in [('manifest.json','manifest_sha256'),('selection.json','selection_sha256'),('case_index.csv','case_index_sha256')]:
        assert d.sha(dataset/name)==frozen[key],name
    records=sorted((r for r in d.read(dataset/'manifest.json')['cases'] if r['model']==8 and r['size']=='small'),key=lambda r:r['base_case_id'])
    assert len(records)==9 and all(r['customer_count']==7 for r in records)
    for folder in ('inputs','cases','runs'):(out/folder).mkdir(exist_ok=True)
    sources={f'source/{k}':v for k,v in d.snapshot(PROJECT,out/'source').items()}
    sources['driver.py']=d.sha(out/'driver.py')
    evidence={}
    for r in records:
        case=r['base_case_id']
        config,hashes=freeze_case(r['case_path'],out,f'{case}_m8')
        evidence.update(hashes)
        original_contract=PROJECT/config['parameter_contract']
        contract=d.read(original_contract)
        contract['decision_bounds']['batch_count_candidates']=COUNTS
        contract['experiment_override']=dict(reason='User request 2026-10-24: restrict K to 1..4',
            original_K=list(range(1,8)),allowed_K=COUNTS,source_sha256=d.sha(original_contract))
        target=out/'inputs'/f'{case}_m8_parameters_k1_4.json'
        d.write(target,contract);config['parameter_contract']=str(target)
        certificate=PROJECT/config['batch_count_certificate']
        target=out/'inputs'/f'{case}_m8_original_natural_K_certificate.json'
        shutil.copyfile(certificate,target);config['batch_count_certificate']=str(target)
        config['joint_batch_options']['batch_count_candidates']=COUNTS
        config.update(solver='exact',exact_time=1800,seed=42,exact_strategy='milp',
            milp_options=dict(threads=1,formulation='expanded'))
        assert not config.get('initial_joint_result')
        d.write(out/f'cases/{case}_m8_k1_4.json',config)
    record=next(r for r in records if r['base_case_id']=='S01')
    job=dict(id='S01_m8_k1_4_exact_1800s_s42',case='S01',model=8,method='exact',budget=1800,
        seed=42,version='current',iterations=10**9,size='small',structure='small',split='formal',
        family=record['family_id'],source_block=record['source_block'],customer_count=7,
        case_path='cases/S01_m8_k1_4.json')
    d.write(out/'frozen_dataset.json',frozen);d.write(out/'source_input_evidence.json',evidence)
    d.write(out/'environment.json',dict(captured_at=d.now(),interpreter=sys.executable,python=sys.version,
        gurobi_version=list(gp.gurobi.version()),physical_cpus=psutil.cpu_count(logical=False),
        logical_cpus=psutil.cpu_count(),total_memory_mib=psutil.virtual_memory().total//1024**2,
        available_memory_mib=psutil.virtual_memory().available//1024**2))
    inputs={p.relative_to(out).as_posix():d.sha(p) for folder in ('inputs','cases') for p in (out/folder).rglob('*') if p.is_file()}
    for name in ('frozen_dataset.json','source_input_evidence.json','environment.json'):inputs[name]=d.sha(out/name)
    manifest=dict(schema_version=1,phase='small7_m8_k1_4_S01_exact1800',registered_at=d.now(),jobs=[job],
        prepared_cases=[r['base_case_id'] for r in records],allowed_batch_counts=COUNTS,
        original_batch_counts=list(range(1,8)),restriction_basis='Explicit user request; K5-K7 not proved infeasible',
        workers=1,exact_threads=1,exact_formulation='expanded',external_starts=False,
        source_hashes=sources,input_hashes=inputs,dataset=frozen['dataset'],
        timeout_overhead=180,worker_memory_mib=3072,minimum_available_mib=192,memory_pressure_grace_seconds=20,
        interpreter=sys.executable,commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=PROJECT,text=True).strip())
    d.write(out/'manifest.json',manifest);d.write(out/'manifest.sha256.json',dict(sha256=d.sha(out/'manifest.json')))
    (out/'.gitignore').write_text('/source/\n/driver.lock\n',encoding='utf-8')
    print(summarize(out,manifest),flush=True)


def worker(args):
    from models.routing import service
    original=service.solve_routing
    def solve(**kwargs):
        kwargs.update(exact_strategy='milp',milp_options=dict(threads=1,formulation='expanded'))
        return original(**kwargs)
    service.solve_routing=solve
    q.worker(args)


def verify(out,manifest):
    d.verify(out,manifest)
    job=manifest['jobs'][0];attempts=d.attempt_dirs(out,job)
    assert len(attempts)==1 and d.complete_attempt(attempts[0])
    path=attempts[0];row=d.read(path/'row.json');result=d.read(path/'result.json');info=result['info']
    assert info['batch_count_candidates']==COUNTS
    assert info['solver_config']['formulation']=='expanded' and info['solver_config']['threads']==1
    assert info['solver_config']['time_limit']==1800 and not info['solver_config']['mip_start']
    for case in manifest['prepared_cases']:
        config=d.read(out/f'cases/{case}_m8_k1_4.json')
        assert config['joint_batch_options']['batch_count_candidates']==COUNTS
        assert d.read(config['parameter_contract'])['decision_bounds']['batch_count_candidates']==COUNTS
    if row.get('best_bound') is not None:assert row['best_bound']<=35.50906153797168+1e-5
    if row['valid']:
        audit=d.read(path/'audit.json')['audit']
        assert audit['valid'] and audit['periodic_replay']['valid'] and row['periodic_valid']
        assert math.isclose(row['objective'],audit['objective'],rel_tol=1e-7,abs_tol=1e-6)
    if row['proven_optimal']:assert row['valid'] and row['objective']<=35.50906153797168+1e-5
    d.write(out/'verification.json',dict(verified_at=d.now(),case='S01',allowed_K=COUNTS,
        completed_calls=1,status=row['status'],valid=row['valid'],proven_optimal=row['proven_optimal'],
        frozen_and_completion_hashes_verified=True,remaining_eight_cases_run=False,unit_test_suite_run=False))


def run(out):
    manifest=d.read(out/'manifest.json');d.verify(out,manifest)
    job=manifest['jobs'][0]
    with d.study_lock(out):
        attempts=d.attempt_dirs(out,job)
        if not attempts:
            print('START '+job['id'],flush=True)
            q.execute(out,manifest,job)
        verify(out,manifest)
        print(summarize(out,manifest),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['prepare','run','worker','status','verify'])
    parser.add_argument('--output',required=True);parser.add_argument('--job');parser.add_argument('--attempt',type=int,default=1)
    args=parser.parse_args();out=Path(args.output).resolve()
    if args.action=='prepare':prepare(out)
    elif args.action=='worker':worker(args)
    elif args.action=='run':run(out)
    elif args.action=='verify':verify(out,d.read(out/'manifest.json'))
    else:print(summarize(out,d.read(out/'manifest.json')),flush=True)
