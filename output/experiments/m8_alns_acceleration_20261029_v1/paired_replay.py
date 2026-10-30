"""Alternate old/new evaluators within one process to reduce timing noise."""
from collections import OrderedDict
import importlib.util
import statistics
import sys
import time
from run import HERE, ROOT, read, save

sys.path.insert(0, str(ROOT))
from models.common.cycle import load_instance
from tools.configuration.loader import load_routing_case


def implementation(variant):
    path = HERE / f'sources/{variant}/models/routing/model_8/alns/evaluator.py'
    spec = importlib.util.spec_from_file_location('paired_' + variant, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


versions = {name: implementation(name) for name in ['baseline', 'candidate']}
rows = []
for case in ['S02', 'S03', 'S09', 'M01', 'L01']:
    size = {'S': 'small', 'M': 'medium', 'L': 'large'}[case[0]]
    p = load_instance(*load_routing_case(ROOT / f'data/formal_benchmark27_uniform_7_20_50_v1/model_8/{size}/{case}/case.json', use_pool_config=False))
    records = read(HERE / 'corpus' / f'{case}.json')
    children = {}
    for row in records:
        for route in row['state']['e2']:
            route['tasks'] = [tuple(task) for task in route['tasks']]
        key = row['K'], tuple(row['boundaries'])
        if key not in children:
            children[key] = p.fixed_calendar(*key)
    rounds = {name: [] for name in versions}
    cpu_rounds = {name: [] for name in versions}
    for repeat in range(6):
        order = list(versions) if repeat % 2 == 0 else list(reversed(versions))
        for name in order:
            implementation = versions[name]
            contexts = OrderedDict()
            begun = time.perf_counter()
            cpu_begun = time.process_time()
            for row in records:
                key = row['K'], tuple(row['boundaries'])
                if key not in contexts:
                    contexts[key] = implementation.JointEvaluator(children[key])
                    if len(contexts) > 24:
                        contexts.popitem(last=False)
                contexts.move_to_end(key)
                contexts[key](row['assignment'], row['state'])
            elapsed = time.perf_counter() - begun
            cpu_elapsed = time.process_time() - cpu_begun
            if repeat:
                rounds[name].append(elapsed)
                cpu_rounds[name].append(cpu_elapsed)
    medians = {name: statistics.median(values) for name, values in rounds.items()}
    cpu_medians = {name: statistics.median(values) for name, values in cpu_rounds.items()}
    result = dict(case=case, records=len(records), measured_rounds=5, warmup_rounds=1,
                  rounds_seconds=rounds, median_seconds=medians,
                  cpu_rounds_seconds=cpu_rounds, cpu_median_seconds=cpu_medians,
                  cpu_time_reduction_percent=100*(1-cpu_medians['candidate']/cpu_medians['baseline']),
                  speedup=medians['baseline']/medians['candidate'],
                  evaluation_time_reduction_percent=100*(1-medians['candidate']/medians['baseline']))
    rows.append(result)
    save(HERE / 'paired_replay.json', rows)
    print(result, flush=True)
