"""Same frozen M5 cases and incumbents; variants run sequentially."""
from pathlib import Path
import subprocess
import sys

here = Path(__file__).resolve().parent
root = here.parents[2]
previous = root/'output/experiments/m5_route_cg_20261001'
small = root/'output/experiments/m5_exact_routes_20261001/final_5s'
experiments = [
    ('final_60s', ['milp', 'cg', 'cg-e1'], 60, [42],
     [previous/'final_60s/inputs'/str(i)/'case.json' for i in range(3)],
     [previous/'final_60s/inputs'/str(i)/'initial_result.json' for i in range(3)]),
    ('small_5s', ['cg-e1'], 5, [42],
     [small/'inputs'/str(i)/'case.json' for i in range(7)],
     [small/f'{i:02d}_42_reference.json' for i in range(7)]),
    ('seeds_30s', ['cg', 'cg-e1'], 30, [43, 44],
     [previous/'final_60s/inputs/2/case.json'],
     [previous/'final_60s/inputs/2/initial_result.json']),
]
for name, variants, budget, seeds, cases, initials in experiments:
    command = [sys.executable, str(root/'tools/audit/benchmark_multibatch_route_cg.py'),
        '--output', str(here/name), '--time', str(budget), '--e1-time', '10',
        '--seeds', *map(str, seeds), '--variants', *variants,
        '--cases', *map(str, cases), '--initial-results', *map(str, initials)]
    with (here/(name+'_console.log')).open('w') as log:
        subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, cwd=root, check=True)
    print(name+' completed', flush=True)
