"""Frozen-input, same-incumbent route-CG diagnostic; run sequentially."""
from pathlib import Path
import subprocess
import sys

here = Path(__file__).resolve().parent
root = here.parents[2]
cases = root/'output/experiments/m5_exact_routes_20261001'
incumbents = root/'output/experiments/m5_exact_certificate_cuts_20261001'
experiments = [
    ('final_60s', ['milp', 'cg'], 60,
     [cases/'final_15s/inputs'/str(i)/'case.json' for i in range(3)],
     [incumbents/name for name in ('final_15s/00_44_reference.json',
                                  'final_15s/01_42_components.json', 'long_60s/00_42_components.json')]),
    ('small_5s', ['cg'], 5,
     [cases/'final_5s/inputs'/str(i)/'case.json' for i in range(7)],
     [cases/'final_5s'/f'{i:02d}_42_reference.json' for i in range(7)]),
]
for name, variants, budget, inputs, initial in experiments:
    command = [sys.executable, str(root/'tools/audit/benchmark_multibatch_route_cg.py'),
        '--output', str(here/name), '--time', str(budget), '--seeds', '42',
        '--variants', *variants, '--cases', *map(str, inputs), '--initial-results', *map(str, initial)]
    with (here/f'{name}_console.log').open('w') as log:
        subprocess.run(command, cwd=root, stdout=log, stderr=subprocess.STDOUT, check=True)
    print(f'{name} completed', flush=True)
