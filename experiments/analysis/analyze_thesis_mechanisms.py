"""E2–E5 mechanism tables, price replay and physical timelines from audited runs."""
from __future__ import annotations

import argparse
from collections import defaultdict
import math
from pathlib import Path
import statistics

from experiments.analysis.analyze_thesis_stages import primary_rows, table
from experiments.runs.run_thesis_completion import read, write


def plotting():
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    return plt


def e2(study, rows):
    plt = plotting()
    cases = sorted({r['case'] for r in rows})
    for index, case in enumerate(cases):
        grouped = {g: [r for r in rows if r['case'] == case and r.get('group') == g] for g in 'ABCD'}
        keys = sorted({key for members in grouped.values() for r in members if r['valid'] for key in r['cost_components']})
        fig, (ax, pool_ax) = plt.subplots(1, 2, figsize=(11, 5.2), constrained_layout=True)
        fig.suptitle(case, fontsize=11)
        bottoms = [0.]*4
        valid = [[r for r in grouped[g] if r['valid']] for g in 'ABCD']
        for color_index, key in enumerate(keys):
            heights = [statistics.mean(r['cost_components'][key] for r in rs) if rs else 0 for rs in valid]
            ax.bar(list('ABCD'), heights, bottom=bottoms, label=key,
                   color=plt.get_cmap('tab20')(color_index))
            bottoms = [a+b for a, b in zip(bottoms, heights)]
        for i, rs in enumerate(valid):
            ax.text(i, bottoms[i], f'{len(rs)}/{len(grouped["ABCD"[i]])}', ha='center', va='bottom', fontsize=8)
        ax.set(title='Audited cost (valid/planned)', ylabel='Synthetic cost units')
        ax.margins(y=.12)
        if keys:
            ax.legend(fontsize=7, ncol=3, loc='upper center', bbox_to_anchor=(.5, -.1))
        for i, rs in enumerate(valid):
            if rs:
                pool_ax.bar(i, statistics.mean(r['pool'] for r in rs))
        pool_ax.set(xticks=range(4), xticklabels=list('ABCD'), ylabel='Physical pool N', title='Mean among audited feasible runs')
        fig.savefig(study/'figures'/f'e2_components_{index:02d}.png', dpi=170)
        fig.savefig(study/'figures'/f'e2_components_{index:02d}.pdf')
        plt.close(fig)


def e3(study, rows):
    records, domains = [], defaultdict(list)
    for row in rows:
        if row.get('group') not in ('standard_m7', 'delayed_m7', 'standard_m8'):
            continue
        record = dict(case=row['mechanism'], strategy=row['group'], k=row['counts'][0],
            status=row['status'], valid=row['valid'], upper=row['objective'], lower=row.get('best_bound'),
            proven_optimal=row['proven_optimal'], proven_infeasible=row['proven_infeasible'],
            pool=row.get('pool'), delivery_batches=row.get('delivery_batches'))
        if row['valid']:
            result = read(row['_path']/'result.json')
            record.update(boundaries=str(result['batch_boundaries']), cutoffs=str(result['batch_cutoffs']),
                loading_starts=str(result.get('batch_loading_starts', {b['id']: b['start'] for b in result['batches']})))
        records.append(record)
        domains[row['mechanism'], row['group']].append(row)
    table(study/'tables/calendar_fixed_k.csv', records)
    selected = []
    for (case, strategy), members in sorted(domains.items()):
        feasible = [r for r in members if r['valid']]
        upper = min((r['objective'] for r in feasible), default=None)
        remaining = [r for r in members if not r['proven_infeasible']]
        lower = min((r['best_bound'] for r in remaining), default=None) if all(r.get('best_bound') is not None for r in remaining) else None
        selected.append(dict(case=case, strategy=strategy, allowed_k=str(sorted(r['counts'][0] for r in members)),
            upper=upper, lower=lower,
            proven_optimal=upper is not None and lower is not None and lower >= upper-1e-6-1e-7*abs(upper),
            proven_infeasible=all(r['proven_infeasible'] for r in members),
            selected_k=min(feasible, key=lambda r: r['objective'])['counts'][0] if feasible else None,
            proof_scope='registered_finite_K_union_only'))
    table(study/'tables/calendar_best_over_registered_k.csv', selected)
    # The original Zhou K=2 case is selected for illustration before outcomes.
    illustrations = [r for r in rows if r.get('mechanism') == 'zhou_7_original' and r['counts'] == [2] and r['valid']]
    plt = plotting()
    for row in illustrations:
        result = read(row['_path']/'result.json')
        factor = result['time_tick_seconds']/(60*result.get('time_tick_subdivision', 1))
        loading = result.get('batch_loading_starts', {b['id']: b['start'] for b in result['batches']})
        trips = [('e1', t) for t in result['e1_trips']]+[('e2', t) for t in result['e2_trips']]
        fig, ax = plt.subplots(figsize=(10, max(3, len(trips)*.32)), constrained_layout=True)
        for i, (layer, trip) in enumerate(trips):
            has_delivery = any(e['kind'] == 'd' for e in trip['events'])
            start = loading[trip['batch_id']] if layer == 'e1' and has_delivery else trip['departure']
            if layer == 'e2' and has_delivery:
                start -= result['operating_policy']['e2_dispatch_prep_time']
            ax.barh(i, (trip['return_complete']-start)*factor, left=start*factor, height=.5,
                    color='#4078a8' if layer == 'e1' else '#3c9977', alpha=.7)
            for event in trip['events']:
                ax.scatter(event['start']*factor, i, marker='o' if event['kind'] == 'd' else 'x', color='black', s=18)
        for t in result['batch_boundaries']:
            ax.axvline(t*factor, color='gray', lw=.7, ls='--')
        for t in result['batch_cutoffs'].values():
            ax.axvline(t*factor, color='#b47429', lw=.7, ls=':')
        ax.set(yticks=range(len(trips)), yticklabels=[t['trip_id'] for _, t in trips], xlabel='Absolute time (minutes)',
               title=f'{row["group"]}, K=2, {row["status"]}; dots delivery / crosses pickup')
        fig.savefig(study/'figures'/f'e3_timeline_{row["group"]}.png', dpi=160)
        fig.savefig(study/'figures'/f'e3_timeline_{row["group"]}.pdf')
        plt.close(fig)


def e4(study, rows):
    table(study/'tables/pool_reoptimization.csv', [dict(case=r.get('reference_case'), seed=r['seed'],
        fixed_pool=r.get('fixed_pool'), necessary_lower_bound=r.get('necessary_lower_bound'),
        status=r['status'], valid=r['valid'], proven_infeasible=r['proven_infeasible'],
        total_cost=r['objective'], **r.get('cost_components', {})) for r in rows])
    summaries = []
    for case in sorted({r['reference_case'] for r in rows}):
        members = [r for r in rows if r['reference_case'] == case]
        feasible = sorted({r['fixed_pool'] for r in members if r['valid']})
        infeasible = {r['fixed_pool'] for r in members if r['proven_infeasible']}
        lower = min(r['necessary_lower_bound'] for r in members)
        threshold = feasible[0] if feasible else None
        summaries.append(dict(case=case, necessary_lower_bound=lower, smallest_audited_feasible_pool=threshold,
            proven_feasibility_threshold=threshold if threshold is not None and all(n in infeasible for n in range(lower, threshold)) else None,
            pending_integer_candidates=str([n for n in range(lower, threshold) if n not in infeasible]) if threshold is not None else 'no feasible upper witness'))
    table(study/'tables/pool_thresholds.csv', summaries)
    # Do not draw a curve through failed calls or silently replace them by 0.
    plt = plotting()
    for index, case in enumerate(sorted({r['reference_case'] for r in rows})):
        members = [r for r in rows if r['reference_case'] == case]
        fig, (ax, status_ax) = plt.subplots(2, 1, figsize=(7, 5), sharex=True, constrained_layout=True)
        for pool in sorted({r['fixed_pool'] for r in members}):
            points = [r for r in members if r['fixed_pool'] == pool]
            valid = [r for r in points if r['valid']]
            if valid:
                first = pool == min(r['fixed_pool'] for r in members)
                ax.scatter([pool]*len(valid), [r['objective'] for r in valid], s=15, color='#4078a8', label='Total' if first else None)
                for label, keys, color in [('Transport', ('e1_distance', 'e2_distance'), '#c98536'),
                        ('Occupied work', ('e1_work', 'e2_work'), '#3c9977'),
                        ('Pool holding', ('container_pool',), '#9354a8')]:
                    values = [sum(r.get('cost_components', {}).get(k, 0) for k in keys) for r in valid]
                    ax.scatter([pool]*len(valid), values, s=12, marker='x', color=color, label=label if first else None)
            status_ax.scatter(pool, len(valid)/len(points), color='#4078a8')
            status_ax.annotate(f'{len(valid)}/{len(points)}', (pool, len(valid)/len(points)), xytext=(0, 5), textcoords='offset points', fontsize=8)
        ax.set(title=case, ylabel='Audited total cost')
        if ax.get_legend_handles_labels()[0]:
            ax.legend(fontsize=7)
        status_ax.set(xlabel='Fixed physical pool N', ylabel='Feasible / planned', ylim=(-.05, 1.2))
        fig.savefig(study/'figures'/f'e4_pool_{index:02d}.png', dpi=160)
        fig.savefig(study/'figures'/f'e4_pool_{index:02d}.pdf')
        plt.close(fig)
    # Final timelines require original completed artifacts. Synthetic unit-test
    # rows and unexecuted designs contain no witness paths.
    if any(r.get('_path') for r in rows):
        from experiments.analysis.thesis_pool_timeline import timelines
        timelines(study, rows)


def e5(study, rows):
    from models.cost.joint_batch import JointCostProfile
    from experiments.analysis.thesis_price_matching import validate_price_design
    table(study/'tables/price_input_matching.csv', validate_price_design(study, rows))
    baselines = {(r['base_case'], r['model'], r['seed']): r for r in rows if r['price_factor'] == 1. and r['valid']}
    output = []
    for row in rows:
        baseline = baselines.get((row['base_case'], row['model'], row['seed']))
        repriced = None
        if baseline:
            audit = read(baseline['_path']/'audit.json')['audit']
            config = read(study/row['case_path'])
            economic = read(config['economic_config']) if isinstance(config['economic_config'], str) else config['economic_config']
            repriced = JointCostProfile(economic).evaluate(audit, baseline['pool'])['total']
            if row['price_factor'] == 1 and not math.isclose(repriced, baseline['objective'], abs_tol=1e-6, rel_tol=1e-7):
                raise ValueError('Baseline fixed-schedule repricing does not reproduce its audited cost')
        output.append(dict(case=row['base_case'], model=row['model'], seed=row['seed'], pool_price_factor=row['price_factor'],
            status=row['status'], valid=row['valid'], reoptimized_cost=row['objective'],
            fixed_schedule_repriced_cost=repriced, reoptimized_pool=row.get('pool'),
            fixed_schedule_pool=baseline['pool'] if baseline else None, k=row.get('k'), delivery_batches=row.get('delivery_batches')))
    table(study/'tables/price_reoptimization_vs_repricing.csv', output)
    plt = plotting()
    for index, case in enumerate(sorted({r['case'] for r in output})):
        fig, axes = plt.subplots(1, 2, figsize=(10, 4), constrained_layout=True)
        for model in (6, 7, 8):
            members = [r for r in output if r['case'] == case and r['model'] == model]
            x, optimized, replayed, pools = [], [], [], []
            for price in (.5, 1., 2.):
                paired = [r for r in members if r['pool_price_factor'] == price and r['valid'] and r['fixed_schedule_repriced_cost'] is not None]
                x.append(price)
                if paired:
                    optimized.append(statistics.mean(r['reoptimized_cost'] for r in paired))
                    replayed.append(statistics.mean(r['fixed_schedule_repriced_cost'] for r in paired))
                    pools.append(statistics.mean(r['reoptimized_pool'] for r in paired))
                else:
                    optimized.append(math.nan)
                    replayed.append(math.nan)
                    pools.append(math.nan)
            line, = axes[0].plot(x, optimized, marker='o', label=f'M{model} reoptimized')
            axes[0].plot(x, replayed, ls='--', color=line.get_color(), label=f'M{model} fixed-schedule repricing')
            axes[1].plot(x, pools, marker='o', label=f'M{model}')
        fig.suptitle(case, fontsize=10)
        axes[0].set(xlabel='Pool price multiplier (synthetic scenario)', ylabel='Mean cost, paired valid runs')
        axes[1].set(xlabel='Pool price multiplier', ylabel='Mean physical pool N, paired valid runs')
        axes[0].legend(fontsize=7)
        axes[1].legend(fontsize=7)
        fig.savefig(study/'figures'/f'e5_prices_{index:02d}.png', dpi=160)
        fig.savefig(study/'figures'/f'e5_prices_{index:02d}.pdf')
        plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--study', required=True)
    parser.add_argument('--stage', choices=['e2', 'e3', 'e4', 'e5'], required=True)
    parser.add_argument('--reference-evidence', help='E4 preregistered reference_evidence.json')
    parser.add_argument('--pool-reference', help='E4 unchanged_schedule_pool_reference.json')
    args = parser.parse_args()
    study = Path(args.study).resolve()
    manifest, rows = primary_rows(study)
    (study/'figures').mkdir(exist_ok=True)
    globals()[args.stage](study, rows)
    if args.reference_evidence or args.pool_reference:
        if args.stage != 'e4' or not (args.reference_evidence and args.pool_reference):
            parser.error('E4 reference overlay requires both reference arguments')
        from experiments.analysis.thesis_pool_reference import reference_overlay
        reference_overlay(study, rows, args.reference_evidence, args.pool_reference)
    write(study/'mechanism_analysis.json', dict(stage=args.stage, planned=len(rows),
        terminal=sum(r['status'] not in ('NOT_RUN', 'RUNNING') for r in rows),
        limitations=['Conditional means use audited feasible runs only.', 'Finite call budgets; no empirical price or universal dominance claim.']))


if __name__ == '__main__':
    main()
