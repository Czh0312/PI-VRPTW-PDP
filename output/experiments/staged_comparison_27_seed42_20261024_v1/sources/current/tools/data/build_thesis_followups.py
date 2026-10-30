"""Deterministic later-stage case selection, before inspecting their outcomes.

Produces designs, not a claim that their prerequisites or experiments passed.
Use run_thesis_completion prepare --design to freeze an executable study.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from pathlib import Path

from experiments.runs.run_thesis_completion import read, write
from tools.configuration.loader import PROJECT_ROOT, load_routing_case, resolve_project_path
from models.common.cycle.instance import load_instance

SEEDS = list(range(42, 47))
MODES = {6: 'uniform_cycle', 7: 'uniform_independent_cutoffs', 8: 'flexible_cycle'}
PILOT = 'config/cases/thesis_completion/pilot_v1'
DATA = 'data/model6/uniform_v2_20261005'
OUTPUT = 'output/thesis_completion'


def relative(path):
    return Path(path).resolve().relative_to(PROJECT_ROOT).as_posix()


def base_design(phase, jobs, selection, *, sources=None, prerequisites=()):
    return dict(phase=phase, jobs=jobs, workers=2, ordering_seed=20261012,
        timeout_overhead=30, overhead_basis='smoke_v1 maximum measured outside-solve overhead 3.900 s; 30 s grace',
        selection_rule=selection,
        sources=sources or {'current': {'path': '.', 'description': 'Actual source hashes frozen at prepare'}},
        prerequisites=list(prerequisites),
        limitations=['Synthetic appointments and costs; no market-price or population-significance claim.',
                     'Nested customer subsets share a source family and are not independent instances.',
                     'All planned first attempts remain in feasibility denominators.'])


def job(phase, case, path, model, method, budget, seed=42, version='current', **extra):
    return dict(id=f'{phase}_{case}_m{model}_{method}_{version}_t{budget}_s{seed}', case=case,
                case_path=relative(path), model=model, method=method, budget=budget, seed=seed,
                version=version, iterations=10**9, **extra)


def case_copy(source, target, model, *, counts=(1, 2, 3), loading=False):
    cfg = read(resolve_project_path(PROJECT_ROOT/source))
    for key, value in list(cfg.items()):
        prefix = '/Users/chenzeheng/Desktop/2E-VRPTW-PDD/'
        if isinstance(value, str) and value.startswith(prefix):
            cfg[key] = value[len(prefix):]
    cfg.update(model=model, save_plots=False)
    options = cfg.setdefault('joint_batch_options', {})
    options.update(schedule_mode=MODES[model], batch_count_candidates=list(counts))
    if loading:
        options['loading_mode'] = 'optimized'
    else:
        options.pop('loading_mode', None)
    write(target, cfg)
    problem = load_instance(*load_routing_case(target, use_pool_config=False))
    for k in counts:
        if problem.for_count(k).operating_policy.time_tick_subdivision != 1:
            raise ValueError('Registered common K domain must use the unrefined input clock')
    return problem


def build(destination):
    out = Path(destination).resolve()
    if out.exists() and any(out.iterdir()):
        raise ValueError('Refusing to overwrite generated followup inputs')
    (out/'cases').mkdir(parents=True)
    suite = read(PROJECT_ROOT/PILOT/'manifest.json')
    jobs = []
    for record in suite['cases']:
        if record['structure'] not in ('repeated_orders', 'late_return', 'tight_pool'):
            continue
        for model in (6, 7, 8):
            for seed in SEEDS:
                jobs.append(job('e1c', record['id'], PROJECT_ROOT/record['cases'][str(model)], model,
                    'alns', 30, seed, 'recycle', structure=record['structure'], split=record['split'],
                    family=record['source_family'], diagnostics=True))
    write(out/'e1c_design.json', base_design('e1c_diagnostic', jobs,
        'All six preregistered E0 repeated-order/late-return/tight-pool cases; development and holdout kept separate.',
        sources={'recycle': dict(path=f'{OUTPUT}/e1a_20261012_v1/sources/recycle',
                                description='Unchanged budget-only frozen source from E1a')},
        prerequisites=[f'{OUTPUT}/{p}_20261012_v1' for p in ('e0', 'e1a', 'e1exact')]))

    selected = [
        ('zhou_Set1_7-2-1', 'spread_narrow'), ('zhou_Set1_12-2-1', 'spread_wide'),
        ('lade_0823_20aoi', 'spread_medium'), ('zhou_Set2_100-10-1_subset30', 'spread_narrow'),
        ('zhou_Set2_100-10-1_subset50', 'spread_medium'), ('zhou_Set2_100-10-1', 'spread_wide')]
    jobs = []
    for family, variant in selected:
        name = f'{family}__{variant}'
        for model in (6, 7, 8):
            target = out/'cases'/f'e1b_{name}_m{model}.json'
            case_copy(f'{DATA}/{family}/{variant}/m6_case.json', target, model)
            for budget in (60, 300, 900):
                for seed in SEEDS:
                    jobs.append(job('e1b', name, target, model, 'alns', budget, seed,
                        family=family.split('_subset')[0], structure=variant))
    design = base_design('e1b', jobs,
        'Six source/size/window strata fixed before runs; 7/12/20/30/50/100 customers, common K={1,2,3}; five seeds and three budgets.',
        prerequisites=[f'{OUTPUT}/e1c_20261012_v1'])
    design['release_condition'] = 'Freeze source only after written E1c decision; a passed run gate alone is insufficient.'
    write(out/'e1b_design_draft.json', design)

    # E3 includes the original physical Zhou adaptation, late releases and
    # cleaning/boundary mechanisms. Both successful and infeasible rows stay.
    cases = [
        ('zhou_7_original', 'data/model6/uniform_v1_20261005/zhou_Set1_7-2-1/m6_case.json', 'zhou_Set1_7-2-1'),
        ('zhou_10_appointments', f'{DATA}/zhou_Set1_10-2-1/spread_medium/m6_case.json', 'zhou_Set1_10-2-1'),
        *[(f'release_limited_{i}', f'{PILOT}/release_limited_{i}/model_7.json', 'reuse_high_pool') for i in (0, 1)],
        *[(f'cleaning_boundary_{i}', f'{PILOT}/cleaning_boundary_{i}/model_7.json', 'reuse_high_pool') for i in (0, 1)]]
    e3_selection = deepcopy(cases)
    jobs, pilot = [], []
    for name, source, family in cases:
        for mode, model, delayed in [('standard_m7', 7, False), ('delayed_m7', 7, True), ('standard_m8', 8, False)]:
            for k in (1, 2, 3):
                case = f'{name}_{mode}_k{k}'
                target = out/'cases'/f'e3_{case}.json'
                case_copy(source, target, model, counts=(k,), loading=delayed)
                jobs.append(job('e3', case, target, model, 'exact', 300, family=family, group=mode, mechanism=name))
                if k == 2 and name in ('zhou_7_original', 'release_limited_0', 'cleaning_boundary_0'):
                    pilot.append(job('e3pilot', case, target, model, 'exact', 10, family=family, group=mode, mechanism=name))
    write(out/'e3_pilot_design.json', base_design('e3_pilot', pilot,
        'Three predefined structures, three strategies, K=2, 10 s diagnostic budget.'))
    design = base_design('e3', jobs,
        'Original 7-customer Zhou adaptation and a controlled 10-customer Zhou appointment case, two E0 release cases and both E0 cleaning-boundary cases; each strategy at K=1,2,3.',
        prerequisites=[f'{OUTPUT}/e3_pilot_20261012_v1'])
    design['release_condition'] = 'Confirm 300 s formal cap using the pilot; preserve all 54 outcomes and their bounds.'
    write(out/'e3_design_draft.json', design)

    small = [(r['id'], r['cases']['6'], r['source_family']) for r in suite['cases']
             if r['structure'] in ('ordinary', 'repeated_orders', 'tight_pool')]
    medium = [(f'{family}__spread_medium', f'{DATA}/{family}/spread_medium/m6_case.json', family.split('_subset')[0])
              for family in ('lade_0823_20aoi', 'lade_0823_30aoi', 'zhou_Set2_100-10-1_subset20',
                             'zhou_Set2_100-10-1_subset30', 'zhou_Set2_100-10-1_subset50', 'zhou_Set2_100-10-1')]
    references = []
    for size, records in [('small', small), ('medium_large', medium)]:
        for name, source, family in records:
            config = read(resolve_project_path(PROJECT_ROOT/source))
            config['joint_batch_options'] = {}
            target = out/'cases'/f'e2reference_{name}.json'
            write(target, config)
            problem = load_instance(*load_routing_case(target, use_pool_config=False))
            assignment = {o.id: o.batch if o.batch in problem.legal_batches[o.id] else
                next(iter(problem.legal_batches[o.id]), o.batch) for o in problem.orders}
            config['joint_batch_options'] = dict(pool_min=problem.total, pool_max=problem.total,
                                                 fixed_assignment=assignment)
            write(target, config)
            references.append(job('e2reference', name, target, 6, 'exact' if size == 'small' else 'alns',
                                  30 if size == 'small' else 300, family=family, size=size))
    write(out/'e2_reference_design.json', base_design('e2_reference', references,
        'All six ordinary/repeated/tight E0 cases plus six fixed medium/large strata; original fixed calendar/cutoffs and original legal assignment, earliest legal batch only when original binding is illegal; N=Q.'))

    old = read(PROJECT_ROOT/'output/experiments/m1_m4_comparison_20261004/full/manifest.json')
    cases = [r for r in old['selected_dataset_cases'] if r['size'] == 'small']
    if len(cases) != 30:
        raise ValueError('E6 must retain exactly the original 30 small instances')
    jobs = []
    for record in cases:
        config, _ = load_routing_case(record['case_path'], use_pool_config=False)
        config = deepcopy(config)
        config.update(model=2, save_plots=False)
        target = out/'cases'/f'e6_{record["case_id"]}.json'
        write(target, config)
        for seed in SEEDS:
            jobs.append(job('e6baseline', record['case_id'], target, 2, 'alns', 30, seed, 'baseline',
                            family=record.get('base_family_id', record['family_id']), diagnostics=True))
    write(out/'e6_baseline_design.json', base_design('e6_baseline', jobs,
        'All 30 original small cases, each with seeds 42..46; no selection by observed error.',
        sources={'baseline': dict(path=f'{OUTPUT}/e1a_20261012_v1/sources/recycle',
                                  description='Unmodified M2 from the preregistered pilot code')}))
    write(out/'selection.json', dict(e1b=selected, e3=e3_selection, e2_small=small, e2_medium_large=medium,
                                   status='designs_created_not_all_registered_or_executed'))
    print(f'Created later-stage designs in {out}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    build(parser.parse_args().output)
