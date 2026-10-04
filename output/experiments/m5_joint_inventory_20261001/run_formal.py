from pathlib import Path
import json
import subprocess
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
BASE = ROOT/'output/experiments/m5_integer_cover_20261001'

def cases(group, indexes=None):
    jobs = json.loads((BASE/group/'manifest.json').read_text())['jobs']
    unique = {j['case']: j['initial_result'] for j in jobs}
    pairs = list(unique.items())
    return [pairs[i] for i in indexes] if indexes else pairs

for source_group, target_group, budget, variants, seeds, indexes in [
    ('final_60s', 'final_60s', 60, ['separate', 'joint'], [42, 43, 44], None),
    ('final_small_5s', 'final_small_5s', 5, ['separate', 'joint'], [42], None),
    ('final_60s', 'final_fallback_60s', 60, ['fallback'], [42], [1, 2]),
]:
    pairs = cases(source_group, indexes)
    command = [sys.executable, str(ROOT/'tools/audit/benchmark_multibatch_joint_inventory.py'),
        '--output', str(HERE/target_group), '--time', str(budget), '--variants', *variants,
        '--seeds', *map(str, seeds), '--joint-time', str(25 if budget == 60 else 1),
        '--recovery-time', str(5 if budget == 60 else 1), '--cases', *[p[0] for p in pairs],
        '--initial-results', *[p[1] for p in pairs]]
    with (HERE/(target_group+'.log')).open('w') as log:
        subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, check=True)
    print(target_group+' finished', flush=True)
