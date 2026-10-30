"""Publish only the conditional rescue, preserving unrelated workspace changes."""
import shutil
from bench import HERE, ROOT, read, save, sha, sources

baseline, proposed = sources('baseline'), sources('rescue')
changed = [path for path in baseline if baseline[path] != proposed[path]]
expected = sorted('models/routing/model_8/alns/'+path for path in
                  ['neighborhoods.py', 'search.py', 'fixed_calendar_search.py', 'k_search/scheduler.py'])
assert sorted(changed) == expected and baseline.keys() == proposed.keys()
replay = read(HERE/'replay_summary.json')
assert replay['recovered_from_timing_cycles'] > 0 and replay['lost_feasible_proposals'] == 0
assert replay['preserved_feasible_proposals'] == replay['baseline_valid']
for path in changed:
    assert sha(ROOT/path) == baseline[path], f'Concurrent change: {path}'
for path in changed:
    shutil.copyfile(HERE/'sources/rescue'/path, ROOT/path)
    assert sha(ROOT/path) == proposed[path]
tests = {'drafts/precedence_checks.py': 'tests/routing/test_model8_repair_precedence.py',
         'drafts/rescue_checks.py': 'tests/routing/test_model8_cycle_rescue.py'}
for source, target in tests.items():
    assert not (ROOT/target).exists()
    shutil.copyfile(HERE/source, ROOT/target)
save(HERE/'publication.json', dict(variant='rescue', algorithm_files={path: proposed[path] for path in changed},
    test_files={target: sha(ROOT/target) for target in tests.values()},
    basis='9 recovered fixed-work repairs; all 175 originally feasible proposals preserved exactly.',
    complexity_prototypes_published=False, standalone_precedence_published=False,
    cold_benchmark_of_this_variant=False, experiment_root_seeds=[42]))
print(read(HERE/'publication.json'))
