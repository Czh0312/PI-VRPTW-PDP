"""Sequential, same-incumbent M5 distance-row comparisons."""
from pathlib import Path
import subprocess
import sys

here = Path(__file__).resolve().parent
root = here.parents[2]
previous = root/'output/experiments/m5_exact_routes_20261001'
for group, budget, indices, seeds, old_group in (
    ('final_15s', 15, range(3), [42, 43, 44], 'final_15s'),
    ('final_5s', 5, range(10), [42], 'final_5s'),
    ('long_60s', 60, [2], [42], 'final_15s'),
):
    args = [sys.executable, str(root/'tools/audit/benchmark_multibatch_primal.py'),
            '--mode', 'distance-cuts', '--variants', 'reference', 'components',
            '--output', str(here/group), '--time', str(budget), '--seeds', *map(str, seeds),
            '--cases', *[str(previous/old_group/'inputs'/str(i)/'case.json') for i in indices],
            '--initial-results', *[str(previous/old_group/f'{i:02d}_42_reference.json') for i in indices]]
    with (here/f'{group}_console.log').open('w') as stream:
        subprocess.run(args, cwd=root, stdout=stream, stderr=subprocess.STDOUT, check=True)
    print(f'{group} completed', flush=True)
