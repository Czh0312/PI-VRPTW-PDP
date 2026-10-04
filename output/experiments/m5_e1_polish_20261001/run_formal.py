"""M5 sequential frozen-input E1 polish and same-budget combined runs."""
from pathlib import Path
import subprocess
import sys

here = Path(__file__).resolve().parent
root = here.parents[2]
previous = root/'output/experiments/m5_e1_inventory_20261001/final_60s'
small = root/'output/experiments/m5_exact_routes_20261001/final_5s'
cases = [previous/'inputs'/str(i)/'case.json' for i in range(3)]
initials = [previous/f'{i:02d}_42_milp.json' for i in range(3)]
experiments = [
    ('final_standalone_5s', ['fixed', 'retimed'], 5, [42, 43, 44], cases, initials),
    ('final_small_5s', ['fixed', 'retimed'], 5, [42],
     [small/'inputs'/str(i)/'case.json' for i in range(7)],
     [small/f'{i:02d}_42_reference.json' for i in range(7)]),
    ('final_cg_60s', ['cg', 'retimed-cg'], 60, [42], cases, initials),
    ('final_milp_60s', ['milp', 'retimed-milp'], 60, [42], cases[2:], initials[2:]),
]
for name, variants, budget, seeds, inputs, results in experiments:
    command = [sys.executable, str(root/'tools/audit/benchmark_multibatch_e1_polish.py'),
        '--output', str(here/name), '--time', str(budget), '--polish-time', '5',
        '--seeds', *map(str, seeds), '--variants', *variants,
        '--cases', *map(str, inputs), '--initial-results', *map(str, results)]
    with (here/(name+'_console.log')).open('w') as log:
        subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, cwd=root, check=True)
    print(name+' completed', flush=True)
