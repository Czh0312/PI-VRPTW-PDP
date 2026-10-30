"""Preserve and register a logging-only correction before resuming the study."""
from pathlib import Path
import hashlib
import json
import zipfile

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    assert not (OUT / 'driver_correction.json').exists()
    initial = json.loads((OUT / 'plan_initial.json').read_text(encoding='utf-8'))
    original_driver = 'output/experiments/m3_small_before_after_300s_20261029_v1/study.py'
    assert sha(OUT / 'study_initial.py') == initial['hashes'][original_driver]
    for path, digest in initial['hashes'].items():
        if path != original_driver:
            assert sha(ROOT / path) == digest, path
    failed = OUT / 'runs' / 'S01_after_s42_300s'
    archived = OUT / 'failed_attempts' / failed.name
    assert failed.resolve().is_relative_to(OUT.resolve())
    assert archived.resolve().is_relative_to(OUT.resolve()) and not archived.exists()
    archived.parent.mkdir(exist_ok=True)
    failed.rename(archived)
    correction = dict(reason='The observer requested Fingerprint before pending variables/rows were flushed. Add model.update() before reading metadata; optimize would perform the same update.',
        production_solver_changed=False, mathematical_model_changed=False,
        completed_before_run_preserved='runs/S01_before_s42_300s',
        failed_attempt='failed_attempts/S01_after_s42_300s', failed_attempt_optimized=False,
        initial_plan_sha256=sha(OUT / 'plan_initial.json'), initial_driver_sha256=sha(OUT / 'study_initial.py'),
        initial_source_archive_sha256=sha(OUT / 'source_inputs.zip'), updated_driver_sha256=sha(OUT / 'study.py'))
    updated = dict(initial)
    updated['hashes'] = dict(initial['hashes'])
    updated['hashes'][original_driver] = sha(OUT / 'study.py')
    updated['driver_revision'] = correction
    save(OUT / 'plan.json', updated)
    with zipfile.ZipFile(OUT / 'source_inputs_v2.zip', 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(updated['hashes']):
            archive.write(ROOT / path, path)
    save(OUT / 'driver_correction.json', correction)
    print(json.dumps(correction, ensure_ascii=False), flush=True)
