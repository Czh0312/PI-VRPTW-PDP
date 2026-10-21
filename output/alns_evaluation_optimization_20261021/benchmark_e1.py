"""Identical seeded E1 workloads; serial alternating order, five repetitions."""
from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from models.routing.model_5.alns import evaluator as candidate
from tests.routing.test_alns_e1_insertion import random_input

OUTPUT = Path(__file__).resolve().parent
spec = spec_from_file_location('frozen_evaluator',
    OUTPUT / 'baseline_source/models/routing/model_5/alns/evaluator.py')
baseline = module_from_spec(spec)
spec.loader.exec_module(baseline)
rows = []
for sites in range(1, 6):
    inputs = [random_input(sites - 1 + 5 * i) for i in range(20)]
    for problem, state, quantities in inputs:
        assert baseline._e1_returns(problem, state, quantities) == candidate._e1_returns(problem, state, quantities)
    timings = dict(baseline=[], candidate=[])
    for repeat in range(5):
        versions = [('baseline', baseline), ('candidate', candidate)]
        for name, module in versions if repeat % 2 == 0 else reversed(versions):
            start = time.perf_counter()
            for _ in range(80):
                for problem, state, quantities in inputs:
                    module._e1_returns(problem, state, quantities)
            timings[name].append(time.perf_counter() - start)
    medians = {name: statistics.median(values) for name, values in timings.items()}
    rows.append(dict(satellites=sites, seeded_inputs=20, calls_per_repetition=1600,
                     repeats=5, seconds=timings, median_seconds=medians,
                     speedup=medians['baseline'] / medians['candidate']))
    print(sites, rows[-1]['speedup'], flush=True)
(OUTPUT / 'e1_microbenchmark.json').write_text(json.dumps(rows, indent=2) + '\n')
