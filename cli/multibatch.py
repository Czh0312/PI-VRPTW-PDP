"""Versioned multibatch execution, artifacts and reproducibility metadata."""
from __future__ import annotations

from contextlib import redirect_stdout
from datetime import datetime
import hashlib
import io
import json
from pathlib import Path
import shutil
import sys
import time

from tools.configuration.loader import PROJECT_ROOT, config_sources, resolve_project_path, load_json
from models.routing.service import solve_routing
from models.common.cycle import load_instance
from models.common.cycle.audit import audit_solution


def _json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def plot_result(problem, result, path):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    p, audit = problem.operating_policy, result['audit']
    fig, axes = plt.subplots(3, 1, figsize=(12, 10), constrained_layout=True)
    times = [p.minutes(p.cycle_start)]+[p.minutes(e['time']) for e in audit['container_ledger']]+[p.minutes(p.cycle_end)]
    clean = [p.container_pool_size]+[e['states']['clean'] for e in audit['container_ledger']]+[audit['terminal_state']['clean']]
    if problem.reusable:
        axes[0].step(times, clean, where='post', label='Clean containers at depot')
    else:
        deliveries = sorted((e['start']+problem.order_map[e['order_id']].service_time,
                             problem.order_map[e['order_id']].count)
                            for trip in result['e2_trips'] for e in trip['events'])
        delivered, total = [0], 0
        for _, count in deliveries:
            total += count
            delivered.append(total)
        axes[0].step([p.minutes(p.cycle_start)]+[p.minutes(t) for t, _ in deliveries]+[p.minutes(p.cycle_end)],
                     delivered+[total], where='post', label='Single-use packages delivered')
    axes[0].set_ylabel('Packages')
    axes[0].legend()
    rows = []
    for layer in ('e1', 'e2'):
        for trip in result[layer+'_trips']:
            rows.append((layer, trip))
    for i, (layer, trip) in enumerate(rows):
        start = trip['departure']
        axes[1].barh(i, p.minutes(trip['return_complete']-start), left=p.minutes(start),
                     color='#3973ac' if layer == 'e1' else '#e6a23c')
    axes[1].set_yticks(range(len(rows)))
    axes[1].set_yticklabels([r['trip_id'] for _, r in rows], fontsize=8)
    for s in problem.satellites if problem.reusable else ():
        stock = [0]+[e['states'][f'sat:{s}'] for e in audit['container_ledger']]+[audit['terminal_state'][f'sat:{s}']]
        axes[2].step(times, stock, where='post', label=s)
    axes[2].set_ylabel('Satellite empty containers')
    if problem.reusable:
        axes[2].legend()
    else:
        axes[2].text(.5, .5, 'Single-use packaging: no recovery or reusable pool',
                     transform=axes[2].transAxes, ha='center')
    for ax in axes:
        for b in p.batches:
            ax.axvline(p.minutes(b.cutoff), color='#d9534f', ls='--', alpha=.5)
        ax.set_xlim(p.minutes(p.cycle_start), p.minutes(p.cycle_end))
        ax.set_xlabel('Minutes (dashed = eligibility cutoff for E1 recovery)')
    fig.savefig(path, dpi=160)
    plt.close(fig)
    # Two maps per wave: E1 assigned to that wave; E2 departures in its interval.
    # Page at most four waves so long calendars do not create oversized images.
    for page in range(0, len(p.batches), 4):
        batches = p.batches[page:page+4]
        figure, axes = plt.subplots(2, len(batches), figsize=(5*len(batches), 8), squeeze=False, constrained_layout=True)
        for column, batch in enumerate(batches):
            for row, layer in enumerate(('e1', 'e2')):
                ax = axes[row, column]
                for node in problem.network.nodes.values():
                    ax.scatter(node.x, node.y, s=50 if node.type != 'c' else 20,
                               marker='s' if node.type == 'd' else '^' if node.type == 's' else 'o', color='#888888')
                    ax.annotate(node.id, (node.x, node.y), xytext=(3, 3), textcoords='offset points', fontsize=8)
                for trip in result[layer+'_trips']:
                    belongs = trip['batch_id'] == batch.id if layer == 'e1' else batch.start <= trip['departure'] < batch.end
                    if not belongs:
                        continue
                    base = problem.depot if layer == 'e1' else trip['satellite_id']
                    ids = [base]+[e['site_id'] if layer == 'e1' else problem.order_map[e['order_id']].customer
                                  for e in trip['events']]+[base]
                    xy = [problem.network.nodes[i] for i in ids]
                    ax.plot([n.x for n in xy], [n.y for n in xy], alpha=.7, label=trip['trip_id'])
                ax.set_title(f'{layer.upper()} / {batch.id}' + (' departures' if layer == 'e2' else ''))
                ax.set_aspect('equal', adjustable='datalim')
                if ax.get_legend_handles_labels()[0]:
                    ax.legend(fontsize=7)
        figure.savefig(path.parent/f'routes_{page//4+1}.png', dpi=160)
        plt.close(figure)


def run_case(config, fleet, *, case_path=None, output=None):
    instance = load_instance(config, fleet)
    out = resolve_project_path(output) if output else PROJECT_ROOT / 'output/single_runs/multi_batch_cutoff_v1' / (
        datetime.now().strftime('%Y%m%dT%H%M%S_%f')+'_'+config['solver'])
    if out.exists() and any(out.iterdir()):
        raise ValueError(f'Refusing to overwrite nonempty output directory: {out}')
    out.mkdir(parents=True, exist_ok=True)
    input_paths = [resolve_project_path(config[key]) for key in (
        'data', 'orders', 'routing_config', 'operating_policy', 'algorithm_config', 'distance_matrix', 'duration_matrix', 'dataset_metadata', 'pool_config', 'economic_config', 'initial_joint_result')
                   if isinstance(config.get(key), str)]
    if case_path:
        input_paths.append(resolve_project_path(case_path))
    if getattr(instance, 'joint_batch', False) and instance.cost_profile.config.get('evidence_file'):
        input_paths.append(resolve_project_path(instance.cost_profile.config['evidence_file']))
    input_paths = sorted({dependency for path in input_paths for dependency in config_sources(path)})
    sources = [path for name in ('models/routing', 'models/common', 'models/cost', 'tools/configuration', 'cli')
               for path in (PROJECT_ROOT/name).rglob('*.py')]
    sources += [PROJECT_ROOT/'main.py', PROJECT_ROOT/'models/__init__.py', PROJECT_ROOT/'tools/__init__.py']
    for source in sources:
        target = out/'source_snapshot'/source.relative_to(PROJECT_ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    inputs_dir = out/'inputs'
    inputs_dir.mkdir()
    for index, source in enumerate(sorted(set(input_paths))):
        shutil.copy2(source, inputs_dir/f'{index:02d}_{source.name}')
    manifest = dict(config=config, fleet=fleet, python=sys.version,
                    policy=instance.operating_policy.as_dict(),
                    input_sha256={str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in input_paths},
                    source_sha256={str(path.relative_to(PROJECT_ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                                   for path in sources},
                    converted_orders=[dict(id=o.id, batch=o.batch, delivery_window=o.delivery_window,
                                           pickup_window=o.pickup_window, service_time=o.service_time,
                                           min_use_time=o.min_use_time) for o in instance.orders],
                    converted_travel_ticks={a: {b: instance.travel(a, b) for b in instance.network.nodes}
                                            for a in instance.network.nodes})
    if getattr(instance, 'joint_batch', False):
        manifest.update(economic_profile=instance.cost_profile.config,
                        economic_profile_hash=instance.cost_profile.hash,
                        evidence_hash=instance.cost_profile.evidence_hash,
                        joint_batch_options=instance.options,
                        cutoff_bounds_ticks=instance.cutoff_bounds,
                        legal_order_batches=instance.legal_batches,
                        release_times={o.id:o.release_time for o in instance.orders})
        if getattr(instance,'uniform_cycle',False):
            manifest.update(uniform_cycle_config=instance.uniform_contract,
                            cutoff_bounds_ticks=None,legal_order_batches=None,
                            source_calendar_note='Reference only; solver rebuilds the entire cycle for each K')
        if getattr(instance, 'calendar_cycle', False):
            manifest.update(calendar_config=instance.calendar_contract,
                            cutoff_bounds_ticks=None, legal_order_batches=None,
                            source_calendar_note='Reference only; M7/M8 optimize a new calendar over the entire cycle')
    _json(out/'manifest.json', manifest)
    print(f'M{instance.model_number} {"single_batch" if len(instance.operating_policy.batches) == 1 else "multi_batch"} {config["solver"]}: {out}')
    log, begun = io.StringIO(), time.perf_counter()
    try:
        with redirect_stdout(log):
            result = solve_routing(model=config['model'], method=config['solver'], problem=instance,
                                   alns_time=config.get('alns_time', 10), alns_iter=config.get('alns_iter', 5000),
                                   exact_time=config.get('exact_time', 60),
                                   exact_backend=config.get('exact_backend'), seed=config.get('seed', 42),
                                   exact_strategy=config.get('exact_strategy'), milp_options=config.get('milp_options'),
                                   initial_result=load_json(config['initial_joint_result']) if config.get('initial_joint_result') else None)
        if result['info'].get('objective') is not None:
            result['audit'] = audit_solution(instance, result)
            if not result['audit']['valid']:
                result['info'].update(status='AUDIT_FAILED', proven_optimal=False)
        result['wall_time'] = time.perf_counter()-begun
        result['output_dir'] = str(out)
        _json(out/'result.json', result)
        (out/'solver.log').write_text(log.getvalue(), encoding='utf-8')
        audit = result.get('audit', {})
        joint = getattr(instance, 'joint_batch', False)
        pool = result.get('container_pool_size', instance.operating_policy.container_pool_size)
        objective_label = '总成本' if joint else '距离'
        lines = [f'M{instance.model_number} {"联合分批成本" if joint else "固定批次"}运行结果', '',
                 f"包装：{'循环' if instance.reusable else '一次性'}；批次数：{result.get('cycle_batch_count',len(instance.operating_policy.batches))}。",
                 f"状态：{result['info']['status']}。",
                 f"独立审计：{audit.get('valid', False)}；已证明最优：{result['info']['proven_optimal']}。",
                 f"{objective_label}：{result['info']['objective']}；循环箱池：{pool if instance.reusable else '不适用'}。",
                 f"已证明不可行：{result['info'].get('proven_infeasible', False)}；"
                 f"证明范围：{result['info'].get('proof_scope') or '无'}。",
                 f"必要容器池下界：{result.get('precheck', {}).get('pool_lower_bound', '未计算')}；"
                 '必要条件通过不代表已找到可行解。', '',
                 '| 批次 | 装货前清洁箱 | 本批需求 | 装货后剩余 |', '| --- | ---: | ---: | ---: |']
        for row in audit.get('clean_stock', []):
            lines.append(f"| {row['batch_id']} | {row['available']} | {row['demand']} | {row['after']} |")
        lines += ['', f"连续两个大周期重放：{audit.get('periodic_replay', {}).get('valid', False)}。",
                  '', '时间表 JSON 使用整数 ticks；分钟 = ticks × time_tick_seconds / (60 × time_tick_subdivision)，细分倍率缺省为1。']
        if not instance.reusable:
            lines += ['一次性包装的重放检查车辆与配送服务；每个周期重新购入包装，不声明容器闭环。']
        if instance.free_assignment:
            lines += ['自由分仓按顾客选择，每个顾客的全部订单保持同仓。精确枚举的全局证明状态以 info 为准。']
        if joint:
            lines += ['', f"启用配送批次数：{result.get('delivery_batch_count')}；总距离：{result.get('total_distance')}。",
                      f"成本来源类型：{instance.cost_profile.config['source_type']}；单位：{instance.cost_profile.config['currency']}。",
                      '关闭配送批次仍保留回收义务；纯回收趟次显式计费。初始箱体投资单列。',
                      '截点仅决定本轮回运资格；二级车辆在第二阶段仍可配送和回收。', '']
            if result.get('batch_cutoffs'):
                lines += [f"截点模式：{result.get('cutoff_mode', 'fixed')}。", '',
                          '| 批次 | 开始/分钟 | 回运截点/分钟 | 第一窗/分钟 | 第二窗/分钟 | 结束/分钟 |',
                          '| --- | ---: | ---: | ---: | ---: | ---: |']
                for b in result['batches']:
                    minutes=instance.operating_policy.minutes
                    lines.append(f"| {b['id']} | {minutes(b['start']):g} | {minutes(b['cutoff']):g} | "
                                 f"{minutes(b['cutoff']-b['start']):g} | {minutes(b['end']-b['cutoff']):g} | {minutes(b['end']):g} |")
                lines.append('')
            lines.extend(f"- {key}: {value}" for key, value in result.get('cost_components', {}).items())
            if getattr(instance,'uniform_cycle',False):
                lines += ['',f"均匀分批搜索范围：{list(instance.batch_count_candidates)}；选定周期批次数：{result.get('cycle_batch_count')}。",
                          f"每批时长/分钟：{instance.operating_policy.minutes(result['batch_duration_ticks']) if result.get('batch_duration_ticks') is not None else None}；"
                          f"统一分界比例：{result.get('shared_cutoff_ratio')}；实际空箱回运轮数：{result.get('actual_recovery_round_count')}。",
                          '所有配置批次数均解决，才能声明整个均匀分批模型的成本最优；单个K最优不足以证明全局最优。',
                          '', '| K | 状态 | 有效成本 | 箱数 | 该K成本最优已证明 |', '| --- | --- | ---: | ---: | --- |']
                lines.extend(f"| {r['batch_count']} | {r['status']} | {r['objective']} | {r.get('container_pool_size')} | {r['proven_optimal']} |" for r in result['count_search'])
            if getattr(instance, 'calendar_cycle', False):
                lines += ['', f"日历模式：{instance.schedule_mode}；K 搜索范围：{list(instance.batch_count_candidates)}。",
                          f"逐批长度/ticks：{result.get('batch_durations_ticks')}；逐批分界比例：{result.get('cutoff_ratios')}。",
                          f"实际空箱回运轮数：{result.get('actual_recovery_round_count')}。",
                          '所有配置K均证明最优或不可行，才能声明整个模型成本最优。',
                          '', '| K | 状态 | 有效成本 | 箱数 | 该K成本最优已证明 |', '| --- | --- | ---: | ---: | --- |']
                lines.extend(f"| {r['batch_count']} | {r['status']} | {r['objective']} | {r.get('container_pool_size')} | {r['proven_optimal']} |" for r in result['count_search'])
        if audit.get('errors'):
            lines += ['', '审计错误：', '```json', json.dumps(audit['errors'], ensure_ascii=False, indent=2), '```']
        if result.get('precheck', {}).get('errors'):
            lines += ['', '必要条件不可行证据：', '```json',
                      json.dumps(result['precheck']['errors'], ensure_ascii=False, indent=2), '```']
        (out/'report.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')
        if audit.get('valid') and config.get('save_plots', True):
            selected = instance.for_count(result['cycle_batch_count']) if getattr(instance,'uniform_cycle',False) else instance
            if getattr(instance, 'calendar_cycle', False):
                selected = instance.selected_problem(result)
            plotted = selected.project(result['order_batch_assignment'], pool,result.get('batch_cutoffs')) if joint else instance
            plot_result(plotted, result, out/'timeline.png')
        print(f"{result['info']['status']} | valid={audit.get('valid', False)} | {objective_label}={result['info']['objective']}")
        return result
    except Exception as exc:
        _json(out/'error.json', dict(error=f'{type(exc).__name__}: {exc}', wall_time=time.perf_counter()-begun))
        (out/'solver.log').write_text(log.getvalue(), encoding='utf-8')
        raise
