"""Reprice audited Zhou runs and run the paired LaDe V2 CNY scenarios.

Zhou keeps synthetic cost units. LaDe uses saved km/min matrices and dated
public-reference rates. Neither calculation optimizes financial cost.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
import csv
from datetime import datetime, timezone, timedelta
import hashlib
import json
import math
from pathlib import Path
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.configuration.loader import load_routing_case, resolve_project_path
from experiments.runs.run_multibatch_cycle import (
    INPUT_KEYS, freeze_inputs, source_snapshot, run_worker)
from models.common.cycle import load_instance
from models.common.cycle.audit import audit_solution
from models.common.cycle.state import empty_result
from models.common.cycle.timing import normalize_schedule
from models.cost.multibatch import evaluate_cycle_cost
from models.cost.single_batch import compare_single_batch, lifecycle_pair
from tools.data.static_batch_dataset import load_manifest, check_pairing


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def now():
    return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec='seconds')


def write(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix+'.tmp')
    temp.write_text(json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    temp.replace(path)


def csv_write(path, rows):
    if not rows:
        return
    columns = list(dict.fromkeys(k for row in rows for k in row))
    with Path(path).open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def verify_inputs(manifest, base):
    for entry in manifest.get('input_snapshots', {}).values():
        assert digest(base/entry['snapshot']) == entry['sha256'], entry['snapshot']
    for name, expected in manifest.get('source_sha256', {}).items():
        assert digest(base/'source_snapshot'/name) == expected, name


def prepare(out, args):
    if out.exists() and any(out.iterdir()):
        raise ValueError('Refusing to overwrite a nonempty output directory')
    out.mkdir(parents=True, exist_ok=True)
    dataset, records = load_manifest('data/manifest.json', source_kinds=['lade_derived_scenario'])
    assert len(records) == 63 and check_pairing(records) == 21
    variants, jobs = [], []
    for record in records:
        config, fleet = load_routing_case(record['case_path'], use_pool_config=False)
        provenance = read(resolve_project_path(config['dataset_metadata']))
        assert provenance['distance_unit'] == 'km' and provenance['time_unit'] == 'minutes'
        paths = {resolve_project_path(record['case_path']).resolve()}
        paths.update(resolve_project_path(config[k]).resolve() for k in INPUT_KEYS if isinstance(config.get(k), str))
        hashes = {str(p): digest(p) for p in paths}
        for model in record['comparison_models']:
            copied = deepcopy(config)
            copied.update(model=model, save_plots=False, alns_iter=args.iterations,
                          container_pool_size=record['default_pool'])
            problem = load_instance(copied, fleet)
            assert problem.operating_policy.container_pool_size == problem.total
            name = f"{record['case_id']}_M{model}"
            variants.append(dict(config=copied, _source_hashes=hashes, name=name))
            for seed in args.seeds:
                jobs.append(dict(run=f'{name}_alns_seed{seed}', variant_name=name,
                    case_id=record['case_id'], family_id=record['family_id'],
                    size=record['size'], customer_count=record['customer_count'],
                    batch_count=record['batch_count'], model=model, solver='alns',
                    seed=seed, budget=args.time))
    run_dir = out/'lade_runs'
    run_dir.mkdir()
    hashes = freeze_inputs(variants, run_dir, 'data/manifest.json')
    sources = source_snapshot(run_dir)
    for variant in variants:
        write(run_dir/'generated_cases'/(variant['name']+'.json'), variant['config'])
    for job in jobs:
        job['case_path'] = str(run_dir/'generated_cases'/(job['variant_name']+'.json'))
    parameters = {}
    for key, name in [('lade_b1', 'lade_hangzhou_v2_single_batch'),
                      ('lade_m5', 'lade_hangzhou_v2_multibatch'),
                      ('zhou_b1', 'single_batch_synthetic'),
                      ('zhou_m5', 'multibatch_synthetic')]:
        category = 'lade' if name.startswith('lade_') else 'synthetic'
        source = ROOT/'config/cost'/category/f'{name}.json'
        target = out/'parameters'/source.name
        target.parent.mkdir(exist_ok=True)
        target.write_bytes(source.read_bytes())
        parameters[key] = dict(snapshot=str(target.relative_to(out)), sha256=digest(target))
    for name in ('lade_hangzhou_v2_evidence_20260930.json', '05_lade_v2_economic_parameters_20260930.md'):
        source = ROOT/'reference/cost'/name
        target = out/'parameters'/name
        target.write_bytes(source.read_bytes())
        parameters[name] = dict(snapshot=str(target.relative_to(out)), sha256=digest(target))
    write(run_dir/'manifest.json', dict(jobs=jobs, selected_dataset_cases=records,
        input_snapshots=hashes, source_sha256=sources, started_at=now(),
        dataset_version=dataset['dataset_version'], workers=args.workers,
        timeout_overhead=30, time_budget_seconds=args.time, seeds=args.seeds,
        execution='Frozen independent ALNS subprocesses; audit and two-cycle replay; no retries',
        purpose='Cost scenarios on feasible routes; not an algorithm performance study',
        pool_policy='N=Q in every reusable configuration; no restricted-pool frontier'))
    write(out/'manifest.json', dict(started_at=now(), dataset_version=dataset['dataset_version'],
        dataset_manifest_sha256=digest(ROOT/'data/manifest.json'), parameters=parameters,
        lade_family_count=21, lade_configuration_count=63, lade_route_jobs=len(jobs),
        zhou_b1_source='output/experiments/m1_m4_comparison_20261004/full',
        zhou_m5_source='output/experiments/m5_comparison_20261004/full',
        scope='LaDe CNY scenarios and Zhou synthetic-unit revaluation kept separate'))
    print(f'Prepared {len(jobs)} LaDe jobs; {args.time:g}s each; {args.workers} workers', flush=True)


def run_lade(out, args):
    base = out/'lade_runs'
    manifest = read(base/'manifest.json')
    verify_inputs(manifest, base)
    rows = [read(p) for p in base.glob('*.row.json')]
    completed = {row['run'] for row in rows}
    assert len(completed) == len(rows)
    planned = {job['run'] for job in manifest['jobs']}
    assert completed <= planned
    pending = [j for j in manifest['jobs'] if j['run'] not in completed]

    def progress(status):
        write(base/'progress.json', dict(status=status, completed=len(rows), planned=len(planned),
            valid=sum(r['valid'] for r in rows), status_counts=dict(Counter(r['status'] for r in rows)), updated_at=now()))

    def execute(job):
        row = run_worker(job, base, manifest['timeout_overhead'])
        write(base/(job['run']+'.row.json'), row)
        return row

    progress('RUNNING')
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = [executor.submit(execute, job) for job in pending]
        for future in as_completed(futures):
            row = future.result()
            rows.append(row)
            progress('RUNNING')
            print(f"{len(rows)}/{len(planned)} {row['run']}: {row['status']} valid={row['valid']}", flush=True)
    csv_write(base/'runs.csv', sorted(rows, key=lambda r: r['run']))
    progress('COMPLETED')


def load_run(job, base):
    config, fleet = load_routing_case(job['case_path'], use_pool_config=False)
    problem = load_instance(config, fleet)
    path = base/job['run']/'result.json'
    if path.exists():
        result = read(path)
    else:
        result = empty_result(problem, job['solver'])
        row_path = base/(job['run']+'.row.json')
        if row_path.exists():
            result['info'].update(status=read(row_path)['status'])
    return problem, result, path


def cost_row(dataset, job, problem, result, parameters, source, out):
    audit = audit_solution(problem, result)
    row = dict(dataset=dataset, family_id=job['family_id'], case_id=job['case_id'],
        customer_count=job['customer_count'], batch_count=job.get('batch_count', 1),
        model=job['model'], solver=job['solver'], seed=job['seed'], status=result['info']['status'],
        valid=bool(audit['valid'] and result['info'].get('objective') is not None),
        packages=problem.total, currency=parameters['currency'],
        source_result=str(source), source_result_sha256=digest(source) if source.exists() else None)
    if not row['valid']:
        row.update(error=json.dumps(audit['errors'], ensure_ascii=False))
        return row, None
    if len(problem.operating_policy.batches) == 1:
        result = normalize_schedule(problem, result)
        audit = audit_solution(problem, result)
    assert audit['valid'] and audit['periodic_replay']['valid']
    cost = evaluate_cycle_cost(problem, result, parameters)
    uses_life = parameters.get('container_lifetime_uses', parameters.get('pool_lifetime_cycles',
                                    parameters.get('pool_amortization_cycles')))
    allocation = (problem.total*parameters['container_price']/uses_life if problem.reusable else 0)
    row.update(periodic_replay=True, e1_distance=audit['distances']['e1'], e2_distance=audit['distances']['e2'],
        e1_working_minutes=audit['time_metrics']['working_minutes']['e1'],
        e2_working_minutes=audit['time_metrics']['working_minutes']['e2'],
        e1_vehicle_count=audit['e1_vehicle_count'], e2_vehicle_count=audit['e2_vehicle_count'],
        pool=cost['container_pool_size'], issued=audit['container_balance']['issued'],
        returned=audit['container_balance']['returned_to_depot'],
        period_operating_cost=cost['period_operating_cost'], initial_pool_investment=cost['initial_pool_investment'],
        use_depreciation_per_cycle=allocation, period_cost_with_use_depreciation=cost['period_operating_cost']+allocation,
        allocated_cost_per_delivery=(cost['period_operating_cost']+allocation)/problem.total,
        **{key+'_cost':value for key,value in cost['components'].items()})
    # Independent arithmetic against newly replayed physical metrics.
    expected = 0
    for layer in ('e1', 'e2'):
        expected += audit['distances'][layer]*parameters['distance_scale_to_km']*parameters[layer+'_distance_cost_per_km']
        expected += audit['time_metrics']['working_minutes'][layer]*parameters[layer+'_work_cost_per_minute']
        expected += audit[layer+'_vehicle_count']*parameters[layer+'_vehicle_cost_per_cycle']
    expected += (row['returned']*parameters['cleaning_cost_per_return']+row['issued']*parameters['tracking_cost_per_use']
                 if problem.reusable else problem.total*(parameters['single_use_package_price']+parameters['single_use_disposal_cost_per_package']))
    assert math.isclose(cost['period_operating_cost'], expected, abs_tol=1e-7, rel_tol=1e-10)
    assert not problem.reusable or row['issued'] == row['returned'] == problem.total
    assert not problem.reusable or cost['initial_pool_investment'] == row['pool']*parameters['container_price']
    write(out/'cost_details'/dataset/(job['run']+'.json'), dict(cost=cost, audit=audit,
          source_result=str(source), source_result_sha256=row['source_result_sha256'],
          schedule_convention='earliest_feasible_fixed_route_B1' if row['batch_count']==1 else 'saved_audited_multibatch_schedule'))
    return row, result


def summarize_pairs(rows):
    groups = defaultdict(list)
    for row in rows:
        groups[row['dataset'], row['customer_count'], row['pair'], row['solver']].append(row)
    summary = []
    for (dataset, count, pair, solver), records in sorted(groups.items()):
        valid = [r for r in records if r['available']]
        summary.append(dict(dataset=dataset, customer_count=count, pair=pair, solver=solver,
            planned_pairs=len(records), available_pairs=len(valid), missing_pairs=len(records)-len(valid),
            saving_pairs=sum(r['horizon_saving'] >= -1e-9 for r in valid),
            mean_saving=statistics.mean(r['horizon_saving'] for r in valid) if valid else None,
            mean_saving_fraction=(sum(r['horizon_saving'] for r in valid)/sum(r['single_use_total'] for r in valid)) if valid else None,
            min_saving=min((r['horizon_saving'] for r in valid), default=None),
            max_saving=max((r['horizon_saving'] for r in valid), default=None),
            mean_single_use_cost_per_delivery=statistics.mean(r['single_use_cost_per_delivery'] for r in valid) if valid else None,
            mean_reusable_cost_per_delivery=statistics.mean(r['reusable_cost_per_delivery'] for r in valid) if valid else None,
            first_break_even_min=min((r['first_break_even_within_horizon'] for r in valid if r['first_break_even_within_horizon'] is not None), default=None),
            first_break_even_max=max((r['first_break_even_within_horizon'] for r in valid if r['first_break_even_within_horizon'] is not None), default=None)))
    return summary


def operating_sensitivity(cost_rows, parameters, evidence):
    """Declared one-factor price scenarios on the same audited B1 routes."""
    valid = {(r['family_id'],r['seed'],r['model']):r for r in cost_rows
             if r['dataset']=='LaDe_CNY' and r['batch_count']==1 and r['valid']}
    grid = evidence['sensitivity']
    drivers = {
        'cleaning_cost_per_return': grid['cleaning_CNY_per_return'],
        'container_price': grid['container_price_CNY'],
        'diesel_CNY_per_liter': grid['diesel_CNY_per_liter'],
        'employer_cost_multiplier': grid['employer_cost_multiplier'],
    }
    rows = []
    for family,seed in sorted({(f,s) for f,s,m in valid}):
        for pure_model,reuse_model in ((1,3),(2,4)):
            if (family,seed,pure_model) not in valid or (family,seed,reuse_model) not in valid:
                continue
            pure,reuse = valid[family,seed,pure_model],valid[family,seed,reuse_model]
            for driver, values in drivers.items():
                for value in values:
                    rates = deepcopy(parameters)
                    if driver=='diesel_CNY_per_liter':
                        rates['e1_distance_cost_per_km']=rates['e2_distance_cost_per_km']=value*8.9/100+0.15
                    elif driver=='employer_cost_multiplier':
                        rates['e1_work_cost_per_minute']=103002/250/8/60*value
                        rates['e2_work_cost_per_minute']=70606/250/8/60*value
                    else:
                        rates[driver]=value
                    def operating(r, reusable):
                        amount=sum(r[l+'_distance']*rates[l+'_distance_cost_per_km']+
                                   r[l+'_working_minutes']*rates[l+'_work_cost_per_minute']+
                                   r[l+'_vehicle_count']*rates[l+'_vehicle_cost_per_cycle'] for l in ('e1','e2'))
                        if reusable:
                            amount += r['returned']*rates['cleaning_cost_per_return']+r['issued']*rates['tracking_cost_per_use']
                        else:
                            amount += r['packages']*(rates['single_use_package_price']+rates['single_use_disposal_cost_per_package'])
                        return amount
                    s=dict(model=pure_model, period_operating_cost=operating(pure,False),
                           components={l+'_transport':pure[l+'_distance']*rates[l+'_distance_cost_per_km']+
                              pure[l+'_working_minutes']*rates[l+'_work_cost_per_minute']+
                              pure[l+'_vehicle_count']*rates[l+'_vehicle_cost_per_cycle'] for l in ('e1','e2')})
                    r=dict(model=reuse_model, period_operating_cost=operating(reuse,True),
                           initial_pool_investment=reuse['pool']*rates['container_price'])
                    pair=lifecycle_pair(s,r,horizon=rates['evaluation_cycles'],lifetime=rates['pool_lifetime_cycles'],
                                        packages=pure['packages'],disposal_rate=rates['single_use_disposal_cost_per_package'])
                    pair.pop('timeline')
                    rows.append(dict(family_id=family,customer_count=pure['customer_count'],seed=seed,
                        pair=f'M{pure_model}_vs_M{reuse_model}',driver=driver,value=value,**pair))
    return rows


def price(out):
    own = read(out/'manifest.json')
    params = {}
    for key, entry in own['parameters'].items():
        assert digest(out/entry['snapshot']) == entry['sha256']
        if key in ('lade_b1','lade_m5','zhou_b1','zhou_m5'):
            params[key] = read(out/entry['snapshot'])
    sources = [('Zhou_synthetic', ROOT/own['zhou_b1_source'], params['zhou_b1'], params['zhou_m5']),
        ('Zhou_synthetic', ROOT/own['zhou_m5_source'], params['zhou_b1'], params['zhou_m5']),
        ('LaDe_CNY', out/'lade_runs', params['lade_b1'], params['lade_m5'])]
    cost_rows, pairs, sensitivity, timelines = [], [], [], []
    b1_groups = defaultdict(dict)
    hashes = {}
    for dataset, base, b1_config, m5_config in sources:
        if dataset == 'LaDe_CNY':
            progress_path = base/'progress.json'
            while read(progress_path)['status'] != 'COMPLETED':
                state = read(progress_path)
                if state['status'] not in ('RUNNING',):
                    raise ValueError('LaDe routes must complete before final costing')
                write(out/'pricing_progress.json', dict(stage='Waiting for LaDe routes',
                    already_priced=len(cost_rows), updated_at=now()))
                time.sleep(15)
        manifest = read(base/'manifest.json')
        verify_inputs(manifest, base)
        saved_verify = base/'verification.json'
        result_hashes = read(saved_verify).get('result_sha256', {}) if saved_verify.exists() else {}
        for index, job in enumerate(manifest['jobs']):
            problem, result, source = load_run(job, base)
            if source.exists() and str(source.relative_to(base)) in result_hashes:
                assert digest(source) == result_hashes[str(source.relative_to(base))]
            if dataset == 'LaDe_CNY':
                config = read(job['case_path'])
                provenance = read(resolve_project_path(config['dataset_metadata']))
                assert provenance['distance_unit'] == 'km'
            parameters = b1_config if len(problem.operating_policy.batches)==1 else m5_config
            row, canonical = cost_row(dataset, job, problem, result, parameters, source, out)
            cost_rows.append(row)
            hashes[str(source)] = row['source_result_sha256']
            if len(problem.operating_policy.batches) == 1:
                b1_groups[dataset, job['family_id'], job['solver'], job['seed']][job['model']] = (job, problem, result)
            if (index+1)%200 == 0:
                print(f'Priced {dataset}: {index+1}/{len(manifest["jobs"])}', flush=True)
                write(out/'pricing_progress.json', dict(stage='Pricing '+dataset,
                    already_priced=len(cost_rows), updated_at=now()))
    for (dataset, family, solver, seed), models in sorted(b1_groups.items()):
        assert set(models) == {1,2,3,4}
        parameters = params['lade_b1' if dataset=='LaDe_CNY' else 'zhou_b1']
        comparison = compare_single_batch({m:v[1] for m,v in models.items()}, {m:v[2] for m,v in models.items()}, parameters)
        stem = f'{family}_{solver}_seed{seed}'
        write(out/'comparisons'/dataset/(stem+'.json'), comparison)
        count = models[1][0]['customer_count']
        for pair, item in comparison['comparisons'].items():
            meta = dict(dataset=dataset, family_id=family, customer_count=count, solver=solver, seed=seed, pair=pair)
            pairs.append({**meta, **{k:v for k,v in item.items() if k!='timeline'}})
            for t in item.get('timeline', []):
                timelines.append({**meta, **t})
            if item['available']:
                life, horizon = item['pool_lifetime_cycles'], item['evaluation_cycles']
                assert life == 50 if dataset=='LaDe_CNY' else life == 10
                s = comparison['costs'][str(item['single_use_model'])] if isinstance(next(iter(comparison['costs'])), str) else comparison['costs'][item['single_use_model']]
                r = comparison['costs'][item['reusable_model']]
                expected = horizon*(s['period_operating_cost']-r['period_operating_cost'])-r['initial_pool_investment']*math.ceil(horizon/life)
                assert math.isclose(expected, item['horizon_saving'], abs_tol=1e-7, rel_tol=1e-10)
        sensitivity.extend(dict(dataset=dataset, family_id=family, customer_count=count, solver=solver, seed=seed, **s) for s in comparison['sensitivity'])
    baseline = {(r['dataset'], r['family_id'], r['solver'], r['seed']):r for r in cost_rows if r['model']==1}
    multi = []
    for row in cost_rows:
        if row['model'] != 5:
            continue
        pure = baseline.get((row['dataset'], row['family_id'], row['solver'], row['seed']))
        available = bool(row['valid'] and pure and pure['valid'])
        item = dict(dataset=row['dataset'], family_id=row['family_id'], customer_count=row['customer_count'],
            batch_count=row['batch_count'], solver=row['solver'], seed=row['seed'], available=available,
            scope='B1 fixed-assignment single-use vs multibatch reusable strategy; no same-B single-use control or M5 cash payback')
        if available:
            assert pure['packages'] == row['packages']
            item.update(packages=row['packages'], pool=row['pool'], initial_pool_investment=row['initial_pool_investment'],
                single_use_b1_period_cost=pure['period_cost_with_use_depreciation'],
                reusable_multibatch_allocated_cost=row['period_cost_with_use_depreciation'],
                allocated_period_saving=pure['period_cost_with_use_depreciation']-row['period_cost_with_use_depreciation'],
                single_use_b1_cost_per_delivery=pure['allocated_cost_per_delivery'],
                reusable_multibatch_cost_per_delivery=row['allocated_cost_per_delivery'])
        multi.append(item)
    summary = summarize_pairs(pairs)
    extra = operating_sensitivity(cost_rows, params['lade_b1'],
                                  read(out/'parameters/lade_hangzhou_v2_evidence_20260930.json'))
    for name, rows in [('costs',cost_rows),('pairs',pairs),('sensitivity',sensitivity),('timeline',timelines),('multibatch',multi),('summary',summary)]:
        csv_write(out/(name+'.csv'), rows)
    csv_write(out/'operating_sensitivity.csv', extra)
    payload = dict(costs=cost_rows, pairs=pairs, sensitivity=sensitivity, multibatch=multi, summary=summary,
        operating_sensitivity=extra, parameters=params, generated_at=now(), dataset='static_batch_suite_v2', lade_family_count=21,
        assumptions='Full same-cycle returns, no loss/discounting, identical repeated demand, equal forward package mass/volume, N=Q')
    write(out/'economic_results.json', payload)
    verification = dict(generated_at=now(), source_result_sha256=hashes,
        revalued_runs=len(cost_rows), valid_runs=sum(r['valid'] for r in cost_rows),
        all_valid_costs_replayed_two_cycles=all(r.get('periodic_replay') for r in cost_rows if r['valid']),
        independent_operating_and_lifecycle_arithmetic=True,
        cny_only_on_lade_km_matrices=True, failure_rows_retained=True,
        input_and_frozen_source_sha256_verified=True,
        status='COMPLETE', cost_is_optimized=False)
    write(out/'verification.json', verification)
    report(out, payload)
    print(json.dumps({k:v for k,v in verification.items() if k!='source_result_sha256'}, ensure_ascii=False), flush=True)


def report(out, data):
    cny = [r for r in data['pairs'] if r['dataset']=='LaDe_CNY']
    valid = [r for r in cny if r['available']]
    run_manifest = read(out/'lade_runs/manifest.json')
    lines = ['# 经济性重新核算（2026-10-05）', '',
        '人民币分析使用 static_batch_suite_v2 的 LaDe-D 杭州派生案例：10/20/30 个 AOI，各 7 个配送日（0823–0829），合计 21 组基础案例、63 个 B1/B2/B3 配置。AOI 是需求聚合区域，不是单个顾客。原始日期只暴露月日，未将其标为 2026 年实测数据。原始数据集为 [Cainiao-AI/LaDe-D](https://huggingface.co/datasets/Cainiao-AI/LaDe-D)，本轮使用本地已保存的派生数据和路网快照。', '',
        '配送数量与位置来自 LaDe，公里/分钟来自保存的 OSRM 矩阵。回收预约、短时使用、全额回收、清洗、仓址代理、车辆与一件一箱为情景假设。21 组包含嵌套 AOI 子集和重复日期，不能当作完全独立样本。', '',
        f'新跑 LaDe：每组 B1 的 M1–M4、B2/B3 的 M5，各 ALNS 种子 {run_manifest["seeds"]}，单次 {run_manifest["time_budget_seconds"]:g} 秒，{run_manifest["workers"]} 个独立进程。路线、输入及源码冻结，独立审计并连续两周期重放。费用为这些可行路线的后处理，不是距离或成本最优证书。失败不重试、不删除。所有循环方案本轮 N=Q，不代表完成紧箱池前沿研究。', '',
        '另对 2026-10-04 全量 Zhou 的 1,120 次 B1 和 560 次 M5 保存结果重新审计计价。Zhou 坐标不是公里，仅保留 synthetic_units 演示配置，其数值不进入人民币结论。', '',
        '## 计价依据', '',
        '| 成本参数 | 基准 | 来源及口径 |', '| --- | ---: | --- |',
        '| 一次性纸箱 | 4 元/次 | [顺丰 F4 公开零售价](https://www.sf-express.com/chn/sc/express/add-service/pkg-normal)，非实测批量采购价 |',
        '| 循环箱 | 36.5 元/箱 | [新华社 2022 年企业采访](https://www.news.cn/politics/2022-06/28/c_1128783518.htm)价格，非当期报价 |',
        '| 寿命 | 50 次 | 情景假设；B1 N=Q 时按 50 周期整池更新 |',
        '| 距离费 | 0.90205 元/km，两层相同 | 明确日期柴油价 8.45×油耗 8.9/100＋维保假设 0.15 |',
        '| 工时费 E1/E2 | 1.0729375 / 0.73547917 元/min | 杭州 2023 年工资中位数÷250÷8÷60×1.25，工作时间和乘数为假设 |',
        '| 车辆固定费 E1/E2 | 100 / 60 元/启用车辆周期 | 折旧保险分摊情景，按实际启用车辆计 |',
        '| 清洗/跟踪 | 0.8 / 0.1 元/箱次 | 情景假设；清洗人工不再重复计入司机工时 |',
        '| 评价期 | 300 个相同运营周期 | 情景；不是 300 天实际记录 |', '',
        '公开证据、时间与逐项假设见 [参数证据](parameters/lade_hangzhou_v2_evidence_20260930.json) 和 [参数说明](parameters/05_lade_v2_economic_parameters_20260930.md)。保留 2026-09-30 参考配置，不把固定日期价格称为现价。', '',
        '## 计算方法', '',
        '每周期运营费用 = 公里×燃油维保费＋车辆占用分钟×人工费＋启用车辆数×固定费＋包装相关费用。车辆占用工时含趟内等待，未覆盖所有付薪待命。', '',
        '单批一次性累计费用 = T×S。循环累计费用 = T×R＋N×36.5×ceil(T/L)。初始购买包含在整池购买次数中，不能再次叠加摊销。首次回本与评价期内此后不再亏损分别报告。', '',
        '多批箱体使用分摊 = Q×36.5/50，初始投入 N×36.5 单列。多批对照是 B1 M1 与 B2/B3 M5 的运营策略比较，缺少同批次数一次性对照，不能单独归因为包装价值，也不报告未经建模的 M5 现金回本。', '',
        '## 单批基准结果', '',
        f'有效人民币配对 {len(valid)}/{len(cny)}；评价期累计不更贵 {sum(r["horizon_saving"]>=-1e-9 for r in valid)}/{len(valid)}。同种子配对，不挑选经济上最有利的种子。', '',
        '| AOI 数 | 对照 | 有效/计划 | 300 周期不更贵数量 | 平均累计节省（元） | 合计费用节省率 | 首次回本范围（周期） |',
        '| ---: | --- | ---: | ---: | ---: | ---: | --- |']
    for r in data['summary']:
        if r['dataset']=='LaDe_CNY':
            mean = f"{r['mean_saving']:.2f}" if r['mean_saving'] is not None else '缺少配对'
            rate = f"{r['mean_saving_fraction']:.2%}" if r['mean_saving_fraction'] is not None else '—'
            break_even = (f"{r['first_break_even_min']}–{r['first_break_even_max']}"
                          if r['first_break_even_min'] is not None else '评价期内未回本')
            lines.append(f"| {r['customer_count']} | {r['pair']} | {r['available_pairs']}/{r['planned_pairs']} | {r['saving_pairs']} | {mean} | {rate} | {break_even} |")
    if valid:
        thresholds=[r['lifetime_average_single_use_purchase_price_threshold'] for r in valid]
        lines += ['', f'按 50 周期寿命平均口径，这些固定方案对应的一次性包装单价盈亏阈值范围为 {min(thresholds):.2f}–{max(thresholds):.2f} 元/件。该阈值来自不同案例及种子，不是供应商报价，也不代表任意中途时点已经回本。']
    example={r['model']:r for r in data['costs'] if r['dataset']=='LaDe_CNY' and r['family_id']=='lade_0823_10aoi' and r['seed']==42 and r['batch_count']==1 and r['model'] in (1,3) and r['valid']}
    if set(example)=={1,3}:
        a,b=example[1],example[3]
        lines += ['', f'成本分解例：08-23、10 AOI、种子 42。M1 一级工时费为 {a["e1_work_cost"]:.2f} 元，M3 为 {b["e1_work_cost"]:.2f} 元，增加 {b["e1_work_cost"]-a["e1_work_cost"]:.2f} 元；省下的纸箱费为 {a["single_use_purchase_cost"]:.2f} 元。M3 另有回收运输、二级工时、清洗与跟踪，故该例循环运营费用高于一次性。工时按模型实际占用时间计，包含等待回收截点。']
    groups=defaultdict(list)
    for r in data['multibatch']:
        if r['dataset']=='LaDe_CNY':
            groups[r['customer_count'],r['batch_count']].append(r)
    lines += ['', '## 多批策略费用', '',
              '| AOI 数 | 批次数 | 有效/计划 | 分摊费用不更贵数量 | 平均周期节省（元） | 合计节省率 |',
              '| ---: | ---: | ---: | ---: | ---: | ---: |']
    for (count,batch),group in sorted(groups.items()):
        available=[r for r in group if r['available']]
        saving=statistics.mean(r['allocated_period_saving'] for r in available) if available else None
        fraction=sum(r['allocated_period_saving'] for r in available)/sum(r['single_use_b1_period_cost'] for r in available) if available else None
        lines.append(f'| {count} | {batch} | {len(available)}/{len(group)} | {sum(r["allocated_period_saving"]>=-1e-9 for r in available)} | '
                     +(f'{saving:.2f} | {fraction:.2%} |' if saving is not None else '缺少配对 | — |'))
    lines += ['', '平均按案例/种子配对计算，未做显著性或独立样本推断。节省率用相同有效配对的累计节省合计除以一次性费用合计，不平均不同分母的百分比。', '',
        '## 参数敏感性与适用范围', '',
        '单批逐例扫描纸箱价 1/2/3/4/5 元 × 寿命 10/20/30/50 周期，保留全部组合。分析固定路线，改变价格与寿命不会重新优化路线。', '',
        '| 纸箱价（元/件） | 寿命（周期） | 有效配对 | 300 周期循环不更贵数量 |',
        '| ---: | ---: | ---: | ---: |']
    grid_groups=defaultdict(list)
    for r in data['sensitivity']:
        if r['dataset']=='LaDe_CNY':
            grid_groups[r['single_use_package_price'],r['pool_lifetime_cycles']].append(r)
    for (price,life),group in sorted(grid_groups.items()):
        lines.append(f'| {price:g} | {life} | {len(group)} | {sum(r["horizon_saving"]>=-1e-9 for r in group)} |')
    lines += ['',
        '另按已登记范围逐例做清洗费 0.3/0.8/1.5、箱价 25/36.5/50/60、柴油价 7/8.45/9、雇主工资成本乘数 1/1.25/1.5 的单因素敏感性，见 [运营敏感性](operating_sensitivity.csv)。给定路线不经济也保留。公开参数和情景回收规则不能替代企业完整利润、损耗、清洗产能及现金记录。', '',
        '产物：[逐次成本](costs.csv)、[单批配对](pairs.csv)、[参数敏感性](sensitivity.csv)、[逐周期现金对照](timeline.csv)、[多批费用](multibatch.csv)、[核验记录](verification.json)。', '']
    (out/'report.md').write_text('\n'.join(lines), encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    parser.add_argument('--stage', choices=['prepare','run','price','report','all'], default='all')
    parser.add_argument('--time', type=float, default=10)
    parser.add_argument('--seeds', nargs='+', type=int, default=[42,43,44])
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--iterations', type=int, default=1000000)
    args = parser.parse_args()
    if args.time <= 0 or not math.isfinite(args.time) or args.workers < 1:
        parser.error('Positive finite budgets and worker counts required')
    out = resolve_project_path(args.output).resolve()
    if args.stage in ('prepare','all'):
        prepare(out, args)
    if args.stage in ('run','all'):
        run_lade(out, args)
    if args.stage in ('price','all'):
        price(out)
    if args.stage == 'report':
        report(out, read(out/'economic_results.json'))


if __name__ == '__main__':
    main()
