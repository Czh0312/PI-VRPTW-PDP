"""Assemble reproducible diagnostic evidence without changing the instance/model."""
import json
from pathlib import Path

from diagnose import OUT, ROOT, PREVIOUS, read, save, sha


if __name__ == '__main__':
    data = read(OUT/'analysis.json')
    cuts = {row['case']: row for row in read(OUT/'capacity_cut_probe.json')}
    selected = {row['case']: row for row in data if row['case'] in ('M03', 'M05', 'M07')}
    target = selected['M03']
    native = target['root_log']
    observed = [row for row in native['points'] if abs(row['incumbent']-target['objective']) < 1e-4]
    first_best = min((row['seconds'] for row in observed), default=None)
    snapshots = {}
    frozen = read(PREVIOUS/'plan.json')
    for relative, expected in frozen['hashes'].items():
        assert sha(ROOT/relative) == expected
    for row in read(PREVIOUS/'rows.json'):
        directory = PREVIOUS/'runs'/row['id']
        for filename, expected in read(directory/'completion.json')['files'].items():
            assert sha(directory/filename) == expected
        phase = next(phase for phase in row['native_phases'] if phase['phase'] == 'full_model')
        for filename in ('row.json', 'result.json', 'independent_audit.json', phase['logfile']):
            path = directory/filename
            snapshots[path.relative_to(ROOT).as_posix()] = sha(path)
    save(OUT/'historical_evidence_hashes.json', snapshots)
    probe = cuts['M03']
    stronger_cost_bound = (target['constant_cost']+target['cost_components']['e1_distance']
                          +.05*probe['final_distance_LP_bound'])
    assert stronger_cost_bound < target['bound']
    lines = ['# 模型3 M03 为什么难以证明最优', '',
        '证据来自M01–M09统一1800秒运行的冻结输入、完整原生日志和已审计结果，'
        '另做18个小型辅助LP复算及M03/M05/M07的短时切割试验。未改生产模型、费用、时间窗或数据，未重跑一小时完整MILP。', '',
        '## 先区分可行结果与最优证明', '',
        '最近可核实的M03单次预算是1800秒，含提取审计总耗时1801.144秒；'
        '本次证据不包含单次3600秒运行。九例队列本身累计约37.63分钟求解及审计，不能当作M03单次耗时。', '',
        f'完整原业务调度在约{target["initial_full_schedule_ready_seconds"]:.2f}秒已经生成，初始成本{target["initial_cost"]:.6f}。'
        f'30分钟内最好成本为{target["objective"]:.6f}、全局下界{target["bound"]:.6f}，总成本GAP为1.5650%。'
        '因此缺少的是全局最优证明，已有可行解且通过成本与两周期审计。', '',
        f'日志在主求解约{first_best:.0f}秒首次记录到本轮最终最好成本；之后主要缓慢抬升下界。'
        '建模1.793秒、全部准备6.656秒，完整主模型求解1793.52秒；不是文件加载或建模占用了半小时。', '',
        '## 已验证：初始松弛与根节点切割后的下界仍偏弱', '',
        '固定箱池费100、清洗费0.2、磨损费0.2合计100.4，在该实例中都是恒定项。'
        '真正可优化的是运输成本31.448713。对比当前最好可行值时：', '',
        '| 阶段 | 总成本下界 | 距最好可行总成本的比例 | 距最好可行运输成本的比例 |',
        '| --- | ---: | ---: | ---: |']
    labels = dict(root_lp='原生初始根LP', root_after_cuts='根节点切割后', final_global_bound='30分钟全局下界')
    for item in target['comparisons_with_best_known_feasible']:
        lines.append(f'| {labels[item["label"]]} | {item["bound"]:.6f} | '
            f'{100*item["total_cost_relative_gap"]:.3f}% | {100*item["transport_cost_relative_gap"]:.3f}% |')
    lines += ['', '这里以“当前最好可行解”为参照，M03尚未知真正最优值，故这些比例不能称为已知真最优值下的精确积分间隙。'
        '常数100.4把总成本相对缺口压小了；扣常数只用于解释，不改变原目标、上下界或官方GAP。', '',
        '## 已验证：辅助下界允许不回仓小环', '',
        '生产辅助LP仅保留每个任务一进一出、合法弧和必要出发数，主动放松了连通、载荷传播和调度。'
        '复算M03时，40个任务里30个处在6个与仓库不连接的有向小环中；LP值192.073317是二级距离下界。'
        '当前已审计可行解的二级距离为363.342946，差距很大。', '',
        '**这是辅助LP的松弛解，不是完整主模型的根LP原解，也不是原模型允许不回仓。**'
        '完整整数模型通过因果序位、时间等联系排除这些环；已审计可行路线全部真实出发并回仓。', '',
        '对辅助LP增加通用子集连通/容量切割后，M03用了24条切割，下界从192.073317升至267.840962。'
        '子集S的出边数至少为配送/回收重量及体积所需趟数向上取整的最大值；每条原物理路线都满足它。'
        '试验只分离当前正流支持图中的连通分量，不是完整的所有子集切割分离。', '',
        f'折算后的组合总成本下界约{stronger_cost_bound:.6f}，仍低于原主模型已有全局下界129.785251。'
        '所以该短试验验证了辅助松弛存在补强空间，却不能证明仅补这些割即可解决M03，或保证完整MILP提速。', '',
        '## 算例结构：有难度因素，尚无设计错误证据', '',
        '- M03来自Zhou 100-5-1b的20顾客子集，20个不同顾客/不同坐标集中于S1；'
        '外部预约分两段、订单13+7，配送窗75分钟，回收窗95/105/115分钟，单次服务10分钟，最小使用20/30/40分钟。',
        '- 物理设定为6辆同质二级车，每车体积容量6、重量容量120；原趟位上限12，'
        '已有对称性规约后仍保留60个候选车辆趟位。当前最好解实际用4辆/4趟，但不能据此在证明前把原可行域限定为4趟。',
        '- 当前完整模型9034个变量、8443个二元变量、约4万约束；预处理后仍有37302行。'
        '30分钟处理1817个节点、约139.88万次单纯形迭代，既有较大证明空间，也有较昂贵的节点LP。',
        '- 工时、车辆启用及趟次费均为0，等待不计价；已知解有跨预约段长时间在途等待，'
        f'最长趟约{target["longest_reported_trip_minutes"]:.1f}分钟，最大相邻事件空等约{target["max_reported_inter_event_wait_minutes"]:.1f}分钟。'
        '一秒整数时钟及免费等待带来许多等成本时间表和车辆安排，是候选退化因素。',
        '- 日志未见数值故障警告；存在大时间尺度系数，但尚无证据把超时归因为数值错误。', '',
        '这些现象不能单独证明哪一项造成多少耗时。其他快实例也存在免费等待和一秒时钟。'
        'M05/M07与M03的空间、车队和时间窗不同，下表是实例对照，未做单因素因果消融。', '',
        '| 实例 | 二元变量 | 根LP下界 | 根节点切割后下界 | 最好成本 | 辅助LP不连接仓库任务数 | 总耗时 |',
        '| --- | ---: | ---: | ---: | ---: | ---: | ---: |']
    for name in ('M03', 'M05', 'M07'):
        item = selected[name]
        lines.append(f'| {name} | {item["binary"]} | {item["root_log"]["root_lp_value"]:.6f} | '
            f'{item["root_log"]["root_after_cuts_bound"]:.6f} | {item["objective"]:.6f} | '
            f'{item["sites"][0]["auxiliary_LP"]["unanchored_tasks"]}/40 | {item["historical_run_seconds"]:.2f}秒 |')
    lines += ['', 'M05变量更多却38.86秒最优，表明“单仓20单”或“变量数量”都不能单独解释M03。'
        '其根节点切割能闭合缺口；M03切割后仍离最好运输成本约10.51%，需要大量后续证明。', '',
        '当前证据支持优先增强完整模型的连通/容量松弛及时间、载荷与路线归属联系，并做同输入、同种子42的对照。'
        '已有同质车辆对称性破除，不能把它说成完全未处理。算例已证明可行，目前未发现必须改时间窗、费用或删顾客才能解决的输入错误。', '',
        '## 证据文件', '',
        '- `analysis.json`：九例输入结构、原生根LP/切割下界、固定成本分解与辅助LP连通性复算。',
        '- `capacity_cut_probe.json`：M03/M05/M07的辅助切割历史，单线程、种子42、每例15秒上限；不构成原问题最优证明。',
        '- `*_aux_LP.log`、`*_connected_capacity_LP.log`：本次所有辅助LP的原生日志。',
        '- `historical_evidence_hashes.json`：用到的原生主模型日志及已审计结果哈希；历史数据与结果未改写。',
        '- `diagnose.py`、`probe_cuts.py`、`report.py`：本次诊断程序；生产模型与算例均未修改。', '']
    (OUT/'report.md').write_text('\n'.join(lines), encoding='utf-8')

    # A standard static plot of the actual historical convergence trace.
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    points = native['points']
    fig, ax = plt.subplots(figsize=(8.5, 4.5))
    ax.step([point['seconds']/60 for point in points], [point['incumbent']-100.4 for point in points],
            where='post', label='Best feasible transport cost', color='#155e75', linewidth=2)
    ax.step([point['seconds']/60 for point in points], [point['bound']-100.4 for point in points],
            where='post', label='Global lower bound, constant removed', color='#c2410c', linewidth=2)
    ax.set(xlabel='Main MILP elapsed time (minutes)', ylabel='Cost after subtracting fixed 100.4',
           title='M03: feasible solutions exist; optimality proof remains open', xlim=(0, 30))
    ax.grid(alpha=.2)
    ax.legend(loc='upper right', fontsize=9)
    fig.tight_layout()
    fig.savefig(OUT/'m03_convergence.png', dpi=160)
    plt.close(fig)
    validation = read(OUT/'validation.json')
    validation.update(all_historical_results_hash_verified=True,
        auxiliary_cut_probe_cases=['M03', 'M05', 'M07'], production_code_modified=False,
        stored_convergence_plot_generated=True)
    save(OUT/'validation.json', validation)
    save(OUT/'evidence_manifest.json', dict(files={p.relative_to(OUT).as_posix(): sha(p) for p in OUT.rglob('*')
        if p.is_file() and '__pycache__' not in p.parts and p.name != 'evidence_manifest.json'}))
    print(json.dumps(dict(report=str(OUT/'report.md'), original_model_unchanged=True,
        m03_root_transport_gap_percent=100*target['comparisons_with_best_known_feasible'][0]['transport_cost_relative_gap'],
        m03_final_transport_gap_percent=100*target['comparisons_with_best_known_feasible'][-1]['transport_cost_relative_gap'],
        stronger_auxiliary_total_bound=stronger_cost_bound), ensure_ascii=False))
