"""Frozen, serial M4 comparison on the eight small cases other than S02."""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys
import time
import traceback
import zipfile

FOLDER = Path(__file__).resolve().parent
ROOT = FOLDER.parents[2]
SNAPSHOT = FOLDER/'snapshot'
CASES = ('S01', 'S03', 'S04', 'S05', 'S06', 'S07', 'S08', 'S09')
PRIOR = ROOT/'output/experiments/m4_network_transfer_S02_20261030_v1'
DATASET = 'formal_benchmark27_uniform_7_20_50_v1'


def now():
    return datetime.now(timezone(timedelta(hours=8))).isoformat()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def write(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    temporary.replace(path)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def prepare():
    if (FOLDER/'plan.json').exists() or SNAPSHOT.exists():
        raise ValueError('This study was already prepared; use its existing frozen plan')
    SNAPSHOT.mkdir()
    hashes, source_hashes, case_inputs = {}, {}, {}

    def copy(relative):
        relative = Path(relative)
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError(f'Input must be relative to the project: {relative}')
        name = relative.as_posix()
        if name in hashes:
            return
        data = (ROOT/relative).read_bytes()
        target = SNAPSHOT/relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        hashes[name] = sha(data)
        if target.suffix == '.json':
            config = json.loads(data.decode('utf-8-sig'))
            for included in config.get('$include', []) if isinstance(config, dict) else []:
                copy(included)

    paths = [p for base in ('models', 'tools/configuration') for p in (ROOT/base).rglob('*.py')]
    paths.append(ROOT/'tools/__init__.py')
    for path in sorted(paths):
        relative = path.relative_to(ROOT)
        copy(relative)
        source_hashes[relative.as_posix()] = hashes[relative.as_posix()]
    prior_sources = read(PRIOR/'network/protocol.json')['source_sha256']
    for name, digest in prior_sources.items():
        if name.startswith('models/routing/model_4/exact/'):
            assert source_hashes[name] == digest, f'M4 changed after the S02 experiment: {name}'
    for case in CASES:
        name = f'data/{DATASET}/model_4/small/{case}/case.json'
        copy(name)
        config = read(SNAPSHOT/name)
        assert config['model'] == 4 and config['base_case_id'] == case
        assert not config.get('initial_joint_result')
        inputs = [name]
        for key in ('data', 'routing_config', 'orders', 'operating_policy', 'economic_config',
                    'dataset_metadata', 'parameter_contract', 'distance_matrix', 'duration_matrix', 'algorithm_config'):
            if isinstance(config.get(key), str):
                copy(config[key])
                inputs.append(config[key])
        case_inputs[case] = dict(case_path=name, input_hashes={n: hashes[n] for n in inputs})
    jobs = []
    for index, case in enumerate(CASES):
        modes = ('baseline', 'network') if index % 2 == 0 else ('network', 'baseline')
        jobs.extend(dict(case=case, mode=mode) for mode in modes)
    with zipfile.ZipFile(FOLDER/'source_inputs.zip', 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(hashes):
            archive.write(SNAPSHOT/name, name)
        archive.write(__file__, 'study.py')
    plan = dict(created_at=now(), dataset=DATASET, cases=list(CASES), seeds=[42],
        seconds_per_call=300., threads=1, jobs=jobs, planned_runs=len(jobs), workers=1,
        snapshot_hashes=hashes, source_hashes=source_hashes, case_inputs=case_inputs,
        original_m4_matches_previous_S02_network_source=True,
        source_inputs_zip_sha256=sha((FOLDER/'source_inputs.zip').read_bytes()),
        driver_sha256=sha(Path(__file__).read_bytes()),
        external_start=False, external_bound=False, callback=False,
        baseline='network_strengthening=False, original expanded expression',
        network='network_strengthening=True, compact expression and static network strengthening',
        timing='load + build + optimize + extract + normalize + independent physical replay; interpreter/preflight excluded')
    write(FOLDER/'plan.json', plan)
    write(FOLDER/'progress.json', dict(state='PREPARED', completed=0, planned=16, updated_at=now()))
    print(json.dumps(dict(prepared=16, cases=list(CASES), source_files=len(source_hashes),
                          frozen_files=len(hashes)), ensure_ascii=False), flush=True)


def verify_snapshot(plan):
    for name, digest in plan['snapshot_hashes'].items():
        assert sha((SNAPSHOT/name).read_bytes()) == digest, f'Frozen file changed: {name}'


def worker(case, mode):
    plan = read(FOLDER/'plan.json')
    verify_snapshot(plan)
    directory = FOLDER/'runs'/case/mode
    directory.mkdir(parents=True, exist_ok=False)
    sys.path.insert(0, str(SNAPSHOT))
    from tools.configuration.loader import load_routing_case, PROJECT_ROOT
    from models.common.cycle import load_instance
    from models.common.cycle.audit import audit_solution
    from models.routing.model_4.exact import solve_exact
    assert PROJECT_ROOT == SNAPSHOT
    began_at = now()
    begun = time.perf_counter()
    try:
        config, fleet = load_routing_case(plan['case_inputs'][case]['case_path'], use_pool_config=False)
        problem = load_instance(config, fleet)
        loaded = time.perf_counter()-begun
        result = solve_exact(problem, time_limit=max(.001, 300.-loaded), seed=42, verbose=True,
                             milp_options=dict(threads=1), network_strengthening=mode == 'network')
        audited = audit_solution(problem, result) if result.get('audit', {}).get('valid') else result.get('audit', {})
        total = time.perf_counter()-begun
        info = result['info']
        row = dict(case=case, mode=mode, seed=42, budget=300., started_at=began_at, ended_at=now(),
            status=info['status'], valid=audited.get('valid', False),
            periodic_valid=audited.get('periodic_replay', {}).get('valid', False),
            proven_optimal=info.get('proven_optimal', False), objective=info.get('objective'),
            bound=info.get('best_bound'), gap=info.get('mip_gap'), customer_count=len({o.customer for o in problem.orders}),
            total_seconds=total, load_seconds=loaded, solver_info=info,
            source_snapshot_sha256=plan['source_inputs_zip_sha256'],
            input_hashes=plan['case_inputs'][case]['input_hashes'])
        write(directory/'result.json', result)
        write(directory/'audit.json', audited)
        loaded_sources = {}
        for name, module in list(sys.modules.items()):
            path = getattr(module, '__file__', None)
            if path and (name == 'models' or name.startswith('models.')
                         or name == 'tools.configuration' or name.startswith('tools.configuration.')):
                relative = Path(path).resolve().relative_to(SNAPSHOT)
                loaded_sources[name] = relative.as_posix()
        write(directory/'loaded_sources.json', loaded_sources)
        verify_snapshot(plan)
    except Exception as exc:
        row = dict(case=case, mode=mode, seed=42, budget=300., started_at=began_at, ended_at=now(),
            status='ERROR', valid=False, periodic_valid=False, proven_optimal=False,
            objective=None, bound=None, gap=None, total_seconds=time.perf_counter()-begun,
            error=f'{type(exc).__name__}: {exc}')
        (directory/'error.txt').write_text(traceback.format_exc(), encoding='utf-8')
        write(directory/'row.json', row)
        raise
    write(directory/'row.json', row)
    print(json.dumps({key: row[key] for key in ('case', 'mode', 'status', 'valid', 'periodic_valid',
                                               'proven_optimal', 'objective', 'gap', 'total_seconds')},
                     ensure_ascii=False), flush=True)


def summaries(final=False):
    plan = read(FOLDER/'plan.json')
    rows = []
    for job in plan['jobs']:
        path = FOLDER/'runs'/job['case']/job['mode']/'row.json'
        if path.exists():
            rows.append(read(path))
    write(FOLDER/'rows.json', rows)
    fields = ['case', 'mode', 'seed', 'budget', 'status', 'proven_optimal', 'valid', 'periodic_valid',
              'objective', 'bound', 'gap', 'total_seconds', 'build_seconds', 'mip_seconds', 'nodes', 'mip_work']
    with (FOLDER/'runs.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        for row in rows:
            info = row.get('solver_info', {})
            writer.writerow({**row, 'build_seconds': info.get('build_time'), 'mip_seconds': info.get('mip_runtime'),
                             'nodes': info.get('node_count'), 'mip_work': info.get('mip_work')})
    by_key = {(row['case'], row['mode']): row for row in rows}
    comparisons = []
    for case in CASES:
        if (case, 'baseline') not in by_key or (case, 'network') not in by_key:
            continue
        old, new = by_key[case, 'baseline'], by_key[case, 'network']
        both = old['proven_optimal'] and new['proven_optimal']
        match = (math.isclose(old['objective'], new['objective'], rel_tol=1e-9, abs_tol=1e-6)
                 if old['objective'] is not None and new['objective'] is not None else None)
        if both and not match:
            raise AssertionError(f'Conflicting certified optima: {case}')
        comparisons.append(dict(case=case, baseline_status=old['status'], network_status=new['status'],
            baseline_seconds=old['total_seconds'], network_seconds=new['total_seconds'],
            baseline_objective=old['objective'], network_objective=new['objective'],
            baseline_gap=old['gap'], network_gap=new['gap'], matching_objectives=match,
            both_proven_optimal=both,
            time_reduction_percent=100*(1-new['total_seconds']/old['total_seconds']) if both else None,
            speedup=old['total_seconds']/new['total_seconds'] if both else None))
    write(FOLDER/'comparison.json', comparisons)
    if comparisons:
        with (FOLDER/'comparison.csv').open('w', encoding='utf-8-sig', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=list(comparisons[0]))
            writer.writeheader()
            writer.writerows(comparisons)
    counts = {mode: dict(completed=sum(row['mode'] == mode for row in rows),
                        optimal=sum(row['mode'] == mode and row['proven_optimal'] for row in rows),
                        valid=sum(row['mode'] == mode and row['valid'] and row['periodic_valid'] for row in rows))
              for mode in ('baseline', 'network')}
    comparable = [row for row in comparisons if row['both_proven_optimal']]
    aggregate = dict(pairs=len(comparisons), proved_pairs=len(comparable),
        faster_cases=[row['case'] for row in comparable if row['network_seconds'] < row['baseline_seconds']],
        slower_cases=[row['case'] for row in comparable if row['network_seconds'] >= row['baseline_seconds']],
        counts=counts)
    if comparable:
        old_sum = sum(row['baseline_seconds'] for row in comparable)
        new_sum = sum(row['network_seconds'] for row in comparable)
        aggregate.update(baseline_sum_seconds=old_sum, network_sum_seconds=new_sum,
                         aggregate_speedup=old_sum/new_sum, aggregate_reduction_percent=100*(1-new_sum/old_sum))
    write(FOLDER/'analysis.json', aggregate)
    lines = ['# M4网络表达：其他八个小规模算例前后对照', '',
        f'状态：{"完成" if len(rows) == 16 else "运行中"}；已返回{len(rows)}/16次。', '',
        '当前统一数据formal_benchmark27_uniform_7_20_50_v1。S01、S03–S09各7顾客；每版每例300秒，种子42、单线程，16个独立进程串行执行。',
        '每例两版的先后顺序交替。全部调用使用同一份已冻结源码及输入；M4源码与此前S02优化版一致。',
        '原版关闭network_strengthening，使用原expanded表达；优化版开启并使用compact表达。不提供历史初解、外部界或回调。', '',
        '| 算例 | 原版总秒数 | 优化版总秒数 | 原版目标 | 优化版目标 | 原版状态 | 优化版状态 | 耗时减少 |',
        '| --- | ---: | ---: | ---: | ---: | --- | --- | ---: |']
    fmt = lambda value: '—' if value is None else f'{value:.6f}'
    for row in comparisons:
        rate = '—' if row['time_reduction_percent'] is None else f"{row['time_reduction_percent']:.2f}%"
        lines.append(f"| {row['case']} | {row['baseline_seconds']:.3f} | {row['network_seconds']:.3f} | "
                     f"{fmt(row['baseline_objective'])} | {fmt(row['network_objective'])} | "
                     f"{row['baseline_status']} | {row['network_status']} | {rate} |")
    lines += ['', '时间包括加载、预检查、建模、主MILP、提取、日程归一化和最终独立业务/两周期审计；解释器启动与运行环境预检另计。',
        'TIME_LIMIT表示预算耗尽，返回耗时不是证明最优所需时间。只有两版均原生证明最优的配对才计算速度比；其余比较覆盖和GAP。',
        f"原版已证最优{counts['baseline']['optimal']}/8，优化版已证最优{counts['network']['optimal']}/8。",
        f"原版审计及两周期通过{counts['baseline']['valid']}/8，优化版通过{counts['network']['valid']}/8。", '',
        f"两版均证优的{len(comparable)}组中，新版更快{len(aggregate['faster_cases'])}组、更慢{len(aggregate['slower_cases'])}组。"]
    if comparable:
        lines.append(f"这些配对原版合计{aggregate['baseline_sum_seconds']:.3f}秒、新版{aggregate['network_sum_seconds']:.3f}秒，"
                     f"合计耗时减少{aggregate['aggregate_reduction_percent']:.2f}%。")
    lines += ['', 'S02未在本轮重复，上一轮184.918秒与108.806秒的两次原始记录保持。',
        '仅种子42、每条件一次，不能据此宣称多种子稳定性。此次没有更改算法、按算例调参或追加种子。',
        '上一轮38项定向回归通过的记录保持；本轮对冻结源码、16次实际结果及独立物理审计另行核验，没有将实验次数计作pytest条目。', '',
        '[逐次数据](runs.csv) · [配对数据](comparison.csv) · [统计](analysis.json) · [实验计划](plan.json) · [核验](verification.json)', '']
    (FOLDER/'report.md').write_text('\n'.join(lines), encoding='utf-8')
    if final:
        assert len(rows) == 16
        verify_snapshot(plan)
        assert sha((FOLDER/'source_inputs.zip').read_bytes()) == plan['source_inputs_zip_sha256']
        verified = []
        for row in rows:
            directory = FOLDER/'runs'/row['case']/row['mode']
            assert row['status'] != 'ERROR'
            assert row['input_hashes'] == plan['case_inputs'][row['case']]['input_hashes']
            config = row['solver_info']['solver_config']
            assert config['threads'] == 1 and config['seed'] == 42
            assert config['formulation'] == ('expanded' if row['mode'] == 'baseline' else 'compact')
            assert config['network_strengthening']['applied'] == (row['mode'] == 'network')
            assert not config['mip_start'] and not config['custom_callback'] and not config['external_certificate']
            audit = read(directory/'audit.json')
            assert audit.get('valid') == row['valid']
            if row['valid']:
                assert audit['periodic_replay']['valid']
                assert math.isclose(audit['objective'], row['objective'], rel_tol=1e-9, abs_tol=1e-6)
            loaded = read(directory/'loaded_sources.json')
            assert 'models.routing.model_4.exact.formulation' in loaded
            verified.append(dict(case=row['case'], mode=row['mode'], status=row['status'],
                                 result_sha256=sha((directory/'result.json').read_bytes()),
                                 audit_sha256=sha((directory/'audit.json').read_bytes()),
                                 row_sha256=sha((directory/'row.json').read_bytes())))
        with zipfile.ZipFile(FOLDER/'source_inputs.zip') as archive:
            for name, digest in plan['snapshot_hashes'].items():
                assert sha(archive.read(name)) == digest, name
        verification = dict(verified_at=now(), cases=list(CASES), seeds=[42], completed=16,
            failed_calls=sum(row['status'] == 'ERROR' for row in rows),
            source_and_input_hashes_verified=True, module_paths_use_frozen_snapshot=True,
            same_inputs_in_each_pair=True, same_source_for_all_calls=True,
            native_proof_flags_respected=True, counts=counts, evidence=verified,
            experiment_scope='8 cases x 2 versions x seed42; no additional solver unit tests')
        write(FOLDER/'verification.json', verification)
    return len(rows), aggregate


def run():
    plan = read(FOLDER/'plan.json')
    verify_snapshot(plan)
    assert sha(Path(__file__).read_bytes()) == plan['driver_sha256']
    for job in plan['jobs']:
        directory = FOLDER/'runs'/job['case']/job['mode']
        if (directory/'row.json').exists():
            continue
        write(FOLDER/'active.json', dict(**job, started_at=now()))
        print(f"START {job['case']} {job['mode']}", flush=True)
        log = FOLDER/f"{job['case']}_{job['mode']}.console.log"
        with log.open('w', encoding='utf-8') as stream:
            completed = subprocess.run([sys.executable, str(Path(__file__)), 'worker',
                '--case', job['case'], '--mode', job['mode']], cwd=ROOT,
                stdout=stream, stderr=subprocess.STDOUT)
        count, aggregate = summaries()
        write(FOLDER/'progress.json', dict(state='RUNNING', completed=count, planned=16,
            last_job=job, last_exit_code=completed.returncode, updated_at=now(), summary=aggregate))
        print(f"DONE {job['case']} {job['mode']} exit={completed.returncode} completed={count}/16", flush=True)
        if completed.returncode and not (directory/'row.json').exists():
            raise RuntimeError(f'Worker failed before writing its result: {log}')
    count, aggregate = summaries(final=True)
    write(FOLDER/'progress.json', dict(state='COMPLETED', completed=count, planned=16,
        updated_at=now(), summary=aggregate))
    write(FOLDER/'active.json', dict(state='COMPLETED', ended_at=now()))
    print(json.dumps(aggregate, ensure_ascii=False), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('prepare', 'run', 'worker', 'summarize'))
    parser.add_argument('--case', choices=CASES)
    parser.add_argument('--mode', choices=('baseline', 'network'))
    args = parser.parse_args()
    if args.command == 'prepare':
        prepare()
    elif args.command == 'run':
        run()
    elif args.command == 'worker':
        worker(args.case, args.mode)
    else:
        print(summaries(final=True))


if __name__ == '__main__':
    main()
