"""Sequential same-input comparisons; every backend gets the original budget."""
from pathlib import Path
import subprocess
import sys

here = Path(__file__).resolve().parent
root = here.parents[2]
previous = root/'output/experiments/m5_route_pool_20261001/final_standalone_10s'
small = root/'output/experiments/m5_exact_routes_20261001/final_5s'
cases = [previous/'inputs'/str(i)/'case.json' for i in range(3)]
initials = [previous/f'{i:02d}_42_pool.json' for i in range(3)]
groups = [
    ('final_60s', 60, ['cg', 'hybrid'], [42, 43, 44], cases, initials),
    ('final_small_5s', 5, ['cg', 'hybrid'], [42],
     [small/'inputs'/str(i)/'case.json' for i in range(7)],
     [small/f'{i:02d}_42_reference.json' for i in range(7)]),
    ('final_fallback_60s', 60, ['fallback'], [42], cases[1:], initials[1:]),
]
for name, budget, variants, seeds, inputs, results in groups:
    command = [sys.executable, str(root/'tools/audit/benchmark_multibatch_enumerated_pricing.py'),
        '--output', str(here/name), '--time', str(budget), '--e1-time', '10',
        '--seeds', *map(str, seeds), '--variants', *variants,
        '--cases', *map(str, inputs), '--initial-results', *map(str, results)]
    with (here/(name+'_console.log')).open('w') as log:
        subprocess.run(command, cwd=root, stdout=log, stderr=subprocess.STDOUT, check=True)
    print(name+' completed', flush=True)
