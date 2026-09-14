"""Freeze the tested release and verify its relation to the measured sources."""
import hashlib
import json
import shutil
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT/'output/experiments/solver_optimization_20260913'


def main():
    policy = json.loads((OUT/'final_policy.json').read_text(encoding='utf-8'))
    assert policy['m6_stage1_ratio']==.8
    for name in ('campaign_progress','post_progress','free_seed_progress','adopted_progress','m6_ratio_progress'):
        assert json.loads((OUT/(name+'.json')).read_text())['status']=='complete'
    test_log = (OUT/'release_pytest.log').read_text(encoding='utf-8')
    assert '123 passed' in test_log and 'failed' not in test_log.lower()
    expected = Path('models/routing/alns/variants/model_6.py')
    changed = []
    for frozen in (OUT/'adopted_source/models').rglob('*.py'):
        relative = frozen.relative_to(OUT/'adopted_source')
        current = ROOT/relative
        if frozen.read_bytes()!=current.read_bytes():
            changed.append(relative)
    assert changed==[expected], changed
    original = (OUT/'adopted_source'/expected).read_text(encoding='utf-8')
    replacement = original.replace('stage1_time_ratio = .9 if len(problem.customers)>20 and max_time>=5 else .8',
                                   'stage1_time_ratio = .8')
    assert (ROOT/expected).read_text(encoding='utf-8')==replacement
    release = OUT/'release_source'
    files = [p.relative_to(ROOT) for p in (ROOT/'models').rglob('*.py')]
    files += [Path(s) for s in ('cli/route.py','config/algorithms/alns_default.json',
        'experiments/runs/optimize_solvers_20260913.py','experiments/runs/run_model12346_alns_scaling.py')]
    hashes = {}
    for relative in files:
        dest = release/relative
        if dest.exists():
            assert dest.read_bytes()==(ROOT/relative).read_bytes(), f'Release snapshot changed: {relative}'
        else:
            dest.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(ROOT/relative,dest)
        hashes[relative.as_posix()] = hashlib.sha256(dest.read_bytes()).hexdigest()
    policy.update(status='complete',final_source='release_source',source_sha256=hashes,
        source_delta_from_adopted=[expected.as_posix()],test_log='release_pytest.log',test_count=123,
        result_equivalence='Only the automatic M6 stage split changed from the adopted snapshot. '
            'Latest large M6 rows use the complete explicit-0.8 seed42 batch. '
            'Other final batches have the same effective solver behavior as this release.',
        completed_at=datetime.now().astimezone().isoformat(timespec='seconds'))
    (OUT/'final_policy.json').write_text(json.dumps(policy,ensure_ascii=False,indent=2),encoding='utf-8')
    print(f'Final source verified: {len(files)} files; regression: 123 passed; M6 ratio: 0.8')


if __name__=='__main__':
    main()
