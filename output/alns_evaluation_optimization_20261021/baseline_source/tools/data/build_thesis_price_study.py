"""Preregister a one-factor pool/transport price-ratio sensitivity design."""
import argparse
from copy import deepcopy
from pathlib import Path

from experiments.runs.run_thesis_completion import read, write
from tools.configuration.loader import PROJECT_ROOT, load_routing_case
from tools.data.build_thesis_followups import base_design, job, SEEDS


def build(followup_cases, output):
    source, out = Path(followup_cases).resolve(), Path(output).resolve()
    if out.exists() and any(out.iterdir()):
        raise ValueError('Refusing to overwrite price design')
    out.mkdir(parents=True)
    # These cases are chosen by scale, before inspecting E1b outcomes.
    selected = ['zhou_Set1_7-2-1__spread_narrow', 'lade_0823_20aoi__spread_medium',
                'zhou_Set2_100-10-1_subset50__spread_medium']
    jobs, baseline = [], []
    for name in selected:
        for model in (6, 7, 8):
            cfg, _ = load_routing_case(source/f'e1b_{name}_m{model}.json', use_pool_config=False)
            economics = read(PROJECT_ROOT/cfg['economic_config']) if isinstance(cfg['economic_config'], str) else cfg['economic_config']
            for factor in (.5, 1., 2.):
                token = str(factor).replace('.', 'p')
                profile = deepcopy(economics)
                profile['container_pool_cost_per_period'] *= factor
                profile['description'] += f' Pool price multiplier {factor:g}; transport rates held at baseline. Synthetic scenario.'
                target = out/f'{name}_m{model}_pool{token}.json'
                config = deepcopy(cfg)
                config['economic_config'] = profile
                write(target, config)
                for seed in SEEDS:
                    item = job('e5', f'{name}_pool{token}', target, model, 'alns', 300, seed,
                        family=name.split('__')[0].split('_subset')[0], price_factor=factor, base_case=name)
                    jobs.append(item)
                if factor == 1.:
                    baseline.append(dict(case=name, model=model,
                        definition='Use this price-study baseline-price solution for fixed-schedule repricing under all factors; independently audit before repricing.'))
    write(out/'e5_design.json', base_design('e5', jobs,
        'Three E1b size strata, models 6/7/8, five seeds; pool price 0.5/1/2 times baseline with all transportation rates fixed. Reoptimize at every point; no price is an empirical quote.',
        prerequisites=['output/thesis_completion/e1c_20261012_v1']))
    write(out/'repricing_protocol.json', baseline)
    print(f'Created {len(jobs)} calls, {sum(j["budget"] for j in jobs)/3600:g} budget hours')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--followup-cases', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    build(args.followup_cases, args.output)
