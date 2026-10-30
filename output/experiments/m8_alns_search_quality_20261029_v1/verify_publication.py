"""Check the published M8 ALNS scope without replacing concurrent repository edits."""
from bench import HERE, ROOT, read, save, sha

baseline = read(HERE/'baseline_hashes.json')
publication = read(HERE/'publication.json')
target = publication['changed_algorithm_files'][0]
assert sha(ROOT/target) == publication['after_sha256']
changed_elsewhere = {path: dict(baseline_sha256=digest, observed_sha256=sha(ROOT/path))
                     for path, digest in baseline.items()
                     if path != target and sha(ROOT/path) != digest}
scope = 'models/routing/model_8/alns/'
assert all(not path.startswith(scope) for path in changed_elsewhere)
publication.update(all_other_model8_alns_source_files_match_baseline=True,
    concurrent_changes_outside_this_task=changed_elsewhere,
    preservation='Only the scheduler was copied from the frozen candidate; all other existing source edits were preserved.')
save(HERE/'publication.json', publication)
print(dict(published_M8_ALNS_files=1, other_M8_ALNS_files_match_baseline=True,
           preserved_concurrent_paths=list(changed_elsewhere)))
