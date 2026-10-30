"""Isolate necessary pickup precedence repairs from the larger prototypes."""
import shutil
from bench import HERE

destination = HERE/'sources/precedence'
if destination.exists():
    raise FileExistsError(destination)
shutil.copytree(HERE/'sources/baseline', destination, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
relative = 'models/routing/model_8/alns/neighborhoods.py'
source = (HERE/'sources/candidate_v4'/relative).read_text(encoding='utf-8')
source = source.replace('def _batch_move(problem, evaluator, assignment, rng, operator, deadline, cutoffs=None, original_state=None):',
                        'def _batch_move(problem, evaluator, assignment, rng, operator, deadline, cutoffs=None):')
block = '''            if original_state is not None and len(problem.orders) <= 9 and time.perf_counter() < deadline:
                from models.routing.model_8.alns.small_repair import retained_repair
                repaired = retained_repair(problem, evaluator, assignment, trial, original_state,
                    problem.proposed_cutoffs(cutoffs), deadline)
                if repaired is not None and repaired[0] < construction[0]:
                    construction = (repaired[0], repaired[2], repaired[3], ())
'''
assert block in source
source = source.replace(block, '')
assert 'small_repair' not in source and 'original_state' not in source
(destination/relative).write_text(source, encoding='utf-8')
shutil.copy2(HERE/'bench.py', HERE/'fixed_driver.py')
print(str(destination))
