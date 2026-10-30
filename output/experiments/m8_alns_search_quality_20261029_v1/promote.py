"""Publish only the audited, frozen allocation variant after paired evaluation."""
import json
import shutil
from bench import HERE, ROOT, read, save, sha, sources

TARGET = 'models/routing/model_8/alns/k_search/scheduler.py'
baseline, candidate = sources('baseline'), sources('candidate')
assert baseline.keys() == candidate.keys()
assert [path for path in baseline if baseline[path] != candidate[path]] == [TARGET]
analysis = read(HERE/'analysis.json')['phases']['allocation_validation']
assert analysis['runs'] == 18
assert analysis['variants']['candidate']['mean_gap_percent'] < analysis['variants']['baseline']['mean_gap_percent']
assert all(r['valid'] and r['periodic_valid'] for r in analysis['rows'])
target = ROOT/TARGET
assert sha(target) == baseline[TARGET], 'Production changed since freezing; review before replacing.'
before = sha(target)
shutil.copyfile(HERE/'sources/candidate'/TARGET, target)
assert sha(target) == candidate[TARGET]
save(HERE/'publication.json', dict(variant='candidate', changed_algorithm_files=[TARGET],
    before_sha256=before, after_sha256=sha(target),
    all_other_model_source_files_match_baseline=all(sha(ROOT/path) == digest
        for path, digest in baseline.items() if path != TARGET),
    prototypes_published=False, root_seeds=[42]))
print(json.dumps(read(HERE/'publication.json'), ensure_ascii=False))
