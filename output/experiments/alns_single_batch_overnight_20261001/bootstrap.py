"""Freeze an overnight diagnostic without changing production solvers."""
from pathlib import Path
import hashlib
import json
import shutil
from datetime import datetime
from zoneinfo import ZoneInfo

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
PRIOR = ROOT / 'output/experiments/alns_single_batch_terminal_tail_20261001'

def hashes(base):
    return {str(p.relative_to(base)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(base.rglob('*')) if p.is_file() and '__pycache__' not in p.parts}

if __name__ == '__main__':
    assert not (OUT / 'before_source').exists(), 'Do not overwrite a frozen run'
    for folder in ('models', 'configuration', 'config'):
        shutil.copytree(ROOT / folder, OUT / 'before_source' / folder,
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    inputs = OUT / 'benchmark_inputs'
    shutil.copytree(PRIOR / 'benchmark_inputs', inputs)
    for case in inputs.glob('*/case.json'):
        config = json.loads(case.read_text())
        for field in ('data', 'orders', 'routing_config'):
            config[field] = str(case.parent / Path(config[field]).name)
        case.write_text(json.dumps(config, indent=2))
    jobs = json.loads((PRIOR / 'quality_manifest.json').read_text())['jobs']
    results = OUT / 'baseline_results'
    results.mkdir()
    for i, job in enumerate(jobs):
        job['case'] = str(inputs / job['family'] / 'case.json')
        shutil.copyfile(PRIOR / 'quality' / ('%03d_before_source.json' % i),
                        results / ('%03d.json' % i))
    shutil.copyfile(PRIOR / 'after_source/models/routing/solvers/alns/partitioned_assignment.py',
                    OUT / 'archived_tail_helpers.py')
    (OUT / 'diagnostic_jobs.json').write_text(json.dumps({'jobs': jobs}, indent=2))
    (OUT / 'before_source_manifest.json').write_text(json.dumps(hashes(OUT / 'before_source'), indent=2))
    (OUT / 'input_hashes.json').write_text(json.dumps(hashes(inputs), indent=2))
    (OUT / 'baseline_result_hashes.json').write_text(json.dumps(hashes(results), indent=2))
    checkpoint = dict(status='diagnosis_pending', automation_id='m1-m4-alns',
                      start=datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),
                      stop='2026-10-02T08:00:00+08:00',
                      scope='single-batch M1-M4 ALNS, 50/100 customers',
                      active_process=None, production_edits=[],
                      prior_candidates='Rejected; preserve restored accepted baseline',
                      next_action='Run diagnostic.py against eight frozen audited M2 incumbents')
    (OUT / 'checkpoint.json').write_text(json.dumps(checkpoint, ensure_ascii=False, indent=2))
    print(json.dumps(dict(frozen_files=len(hashes(OUT / 'before_source')),
                         jobs=len(jobs), input_files=len(hashes(inputs))), indent=2))
