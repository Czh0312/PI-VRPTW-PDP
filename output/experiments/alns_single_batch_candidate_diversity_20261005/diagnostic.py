"""Frozen historical-state diagnostic; selected candidates receive fresh full audits."""
from collections import defaultdict, deque
from copy import deepcopy
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
import gzip
import hashlib
import json
import os
import shutil
import sys
import time

OUT = Path(__file__).resolve().parent
OLD = OUT.parent / 'alns_single_batch_joint_supply_diagnostic_20261002'
BASE = OUT.parent / 'alns_single_batch_restart_credit_20261005'

def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def select(candidates, capacity=256):
    if capacity < 0:
        raise ValueError('Negative candidate capacity')
    # No feasibility, audit, historical-improvement or score labels are used.
    groups = defaultdict(list)
    for row in sorted(candidates, key=lambda x: x['rank']):
        key = tuple(sorted(i for i, _ in row['changes']))
        groups[key].append(row)
    pending = [deque(rows) for rows in sorted(groups.values(), key=lambda g: g[0]['rank'])]
    selected = []
    while pending and len(selected) < capacity:
        remaining = []
        for queue in pending:
            if len(selected) >= capacity:
                break
            selected.append(queue.popleft())
            if queue:
                remaining.append(queue)
        pending = remaining
    return selected

def freeze():
    assert not (OUT / 'protocol.json').exists()
    write(OUT / 'protocol.json', {
        'registered': datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),
        'hypothesis': 'Round-robin across affected-route index sets at fixed256 selected E2 candidates reaches feasible moves crowded out by pure geometric ranking.',
        'selection': 'Bucket by sorted changed route indices; order within each bucket by original geometric rank; order buckets by best rank; round-robin until min(256,N). No labels, screens or audit outcomes enter selection. One registered rule, no tuning variants after seeing outcomes.',
        'scope': 'M3 historical 16 control states already used in October2 diagnosis; six50 and ten100, correlated pairs, not new independent instances. Production untouched.',
        'checks': 'Fresh uncached decode/full independent audit and periodic replay of all16 controls and EVERY candidate in the union of baseline top256 and diversity256, even those historically screened out. Ordered task inventory must match. Save all successes/failures.',
        'admission_gate': 'All controls reproduce valid original objective; selection is deterministic, unchanged inputs, unique and <=256; at least five of the ten historical100 control states have a newly reached full-valid improving move; do not lose a baseline valid improvement. Passing only admits an online implementation trial, never production adoption.',
        'next_gate': 'Only if diagnostic passes, separately freeze an online M3-only implementation and quality protocol; all50/100 short/long budgets, complete audits, original means<=0/worst<=5/wins criteria. No reinstatement of1024queue/loadscreen or first rejected restart candidate.',
        'timeout': '120 seconds per historical state including decoding/auditing/output; unfinished states fail admission, never silently dropped.',
        'interpretation': 'Additional work on fixed old states is diagnostic, not equal-budget solver improvement or true GAP. No runtime speedup claim.',
    })
    shutil.copytree(BASE / 'before_source', OUT / 'frozen_source', ignore=shutil.ignore_patterns('__pycache__', '.pytest_cache'))
    old_manifest = json.loads((OLD / 'incumbent_manifest.json').read_text())
    entries = []
    (OUT / 'incumbents').mkdir()
    (OUT / 'candidates').mkdir()
    origins = {}
    for item in old_manifest:
        name, old_case = item['name'], Path(item['case'])
        target_dir = OUT / 'benchmark_inputs' / old_case.parent.name
        if not target_dir.exists():
            target_dir.mkdir(parents=True)
            config = json.loads(old_case.read_text())
            for key, value in list(config.items()):
                if key in ('data', 'orders', 'routing_config', 'pool_config', 'container_pool_config',
                           'algorithm_config', 'dataset_metadata', 'distance_matrix', 'duration_matrix') and isinstance(value, str):
                    source = Path(value)
                    assert source.is_file(), (key, value)
                    target = target_dir / source.name
                    if target.exists():
                        assert sha(target) == sha(source)
                    else:
                        shutil.copy2(source, target)
                    origins[str(target.relative_to(OUT))] = dict(source=str(source), sha256=sha(source))
                    config[key] = str(target)
            write(target_dir / 'case.json', config)
        raw = OLD / 'incumbents' / name
        assert sha(raw) == item['sha256']
        shutil.copy2(raw, OUT / 'incumbents' / name)
        candidates = OLD / 'expanded' / Path(name).stem / 'all_candidates.json'
        shutil.copy2(candidates, OUT / 'candidates' / name)
        origins['candidates/' + name] = dict(source=str(candidates), sha256=sha(candidates))
        entries.append(dict(name=name, case=str(target_dir / 'case.json'), size=item['job']['size'],
                            old_origin=item['origin'], raw_sha256=sha(raw)))
    write(OUT / 'manifest.json', entries)
    write(OUT / 'origins.json', origins)
    files = [p for p in OUT.rglob('*') if p.is_file()]
    write(OUT / 'registration_hashes.json', {str(p.relative_to(OUT)): sha(p) for p in sorted(files)})
    print('Frozen:', len(entries), 'historical states;', len(files), 'files')

def tests():
    rows = [dict(rank=i+1, changes=[(0, [('a','d')])]) for i in range(300)]
    rows += [dict(rank=301, changes=[(1, [('b','d')])])]
    original = deepcopy(rows)
    chosen = select(rows)
    assert len(chosen) == 256 and len({r['rank'] for r in chosen}) == 256
    assert 301 in {r['rank'] for r in chosen}
    assert rows == original and select(list(reversed(rows))) == chosen
    assert select(rows, 0) == [] and select([], 256) == []
    assert select(rows, 400) == select(rows, len(rows))
    poisoned = deepcopy(rows)
    for r in poisoned:
        r.update(measure={'valid': r['rank'] % 2, 'objective': -r['rank']}, screen_passed=False)
    assert [r['rank'] for r in select(poisoned)] == [r['rank'] for r in chosen]
    try:
        select(rows, -1)
        raise AssertionError('negative cap accepted')
    except ValueError:
        pass
    write(OUT / 'selector_tests.json', dict(passed=True, checks=[
        'capacity', 'uniqueness', 'route_bucket_coverage', 'input_immutability', 'deterministic_input_order',
        'zero_and_empty', 'exhaustion', 'ignores_outcome_labels', 'negative_capacity']))

def run():
    for rel, digest in json.loads((OUT / 'registration_hashes.json').read_text()).items():
        assert sha(OUT / rel) == digest, rel
    sys.path.insert(0, str(OUT / 'frozen_source'))
    from configuration.loader import load_routing_case
    from models.routing.common.cycle import load_instance
    from models.routing.common.cycle.audit import audit_solution
    from models.routing.solvers.alns.evaluator import evaluate
    from models.routing.solvers.alns.neighborhoods import state_from_result, clone_state
    assert Path(evaluate.__code__.co_filename).resolve().is_relative_to(OUT / 'frozen_source')
    output = OUT / 'results'
    output.mkdir()
    summary = []
    def measure(problem, state):
        _, decoded, issues = evaluate(problem, state)
        audit = audit_solution(problem, decoded)
        valid = not issues and audit['valid'] and audit.get('periodic_replay', {}).get('valid', False)
        return dict(valid=valid, objective=decoded['info'].get('objective'), issues=issues), decoded, audit
    for index, item in enumerate(json.loads((OUT / 'manifest.json').read_text())):
        started = time.perf_counter()
        write(OUT / 'checkpoint.json', dict(status='running', index=index, name=item['name'], pid=os.getpid()))
        config, fleet = load_routing_case(item['case'])
        problem = load_instance({**config, 'model': 3}, fleet)
        raw = json.loads((OUT / 'incumbents' / item['name']).read_text())
        state = state_from_result(problem, raw['result'])
        original_state = deepcopy(state)
        baseline, result, audit = measure(problem, state)
        assert baseline['valid'] and abs(baseline['objective'] - raw['result']['info']['objective']) <= 1e-8
        candidates = json.loads((OUT / 'candidates' / item['name']).read_text())
        selected = select(candidates)
        controls = {r['rank'] for r in sorted(candidates, key=lambda r: r['rank'])[:256]}
        treated = {r['rank'] for r in selected}
        union = controls | treated
        assert len(treated) == min(256, len(candidates))
        folder = output / Path(item['name']).stem
        folder.mkdir()
        write(folder / 'control.json', dict(state=state, measure=baseline, decoded=result, audit=audit))
        write(folder / 'selection.json', dict(baseline=sorted(controls), diversity=[r['rank'] for r in selected]))
        rows = []
        inventory = sorted(t for route in state['e2'] for t in route['tasks'])
        for row in candidates:
            if row['rank'] not in union:
                continue
            if time.perf_counter() - started > 120:
                break
            proposal = clone_state(state)
            for route_index, tasks in row['changes']:
                proposal['e2'][route_index]['tasks'] = [tuple(t) for t in tasks]
            proposal['e2'] = [r for r in proposal['e2'] if r['tasks']]
            assert sorted(t for route in proposal['e2'] for t in route['tasks']) == inventory
            checked, decoded, full_audit = measure(problem, proposal)
            measured = dict(rank=row['rank'], baseline=row['rank'] in controls,
                            diversity=row['rank'] in treated, **checked)
            with gzip.open(folder / ('rank_%04d.json.gz' % row['rank']), 'wt') as handle:
                json.dump(dict(state=proposal, check=measured, decoded=decoded, audit=full_audit), handle)
            rows.append(measured)
        assert state == original_state
        write(folder / 'summary.json', rows)
        wins = {label: [r for r in rows if r[label] and r['valid'] and r['objective'] < baseline['objective'] - 1e-7]
                for label in ('baseline', 'diversity')}
        record = dict(name=item['name'], size=item['size'], baseline_objective=baseline['objective'],
                      original_valid_equal=True, selected=len(treated), unique_checked=len(rows), expected_checks=len(union),
                      complete=len(rows)==len(union), baseline_improvements=len(wins['baseline']),
                      diversity_improvements=len(wins['diversity']),
                      diversity_best=min((r['objective'] for r in wins['diversity']), default=None),
                      new_improving_ranks=[r['rank'] for r in wins['diversity'] if not r['baseline']],
                      seconds=time.perf_counter()-started, load=os.getloadavg())
        summary.append(record)
        write(OUT / 'summary.json', summary)
        print(item['name'], item['size'], len(rows), record['baseline_improvements'],
              record['diversity_improvements'], record['complete'], flush=True)
    complete = all(r['complete'] and r['original_valid_equal'] and r['selected'] <= 256 for r in summary)
    reached = sum(r['size']==100 and bool(r['new_improving_ranks']) for r in summary)
    no_loss = all(r['diversity_improvements'] >= r['baseline_improvements'] for r in summary)
    admitted = complete and reached >= 5 and no_loss
    write(OUT / 'decision.json', dict(status='ADMITTED_TO_ONLINE_TRIAL' if admitted else 'REJECTED_MECHANISM_GATE',
          complete=complete, reached_100_controls=reached, required=5, no_baseline_loss=no_loss,
          production_adopted=False, limitation='Fixed historical-state coverage, not equal-budget solve improvement.'))
    write(OUT / 'checkpoint.json', dict(status='completed', active_process=None, completed_states=len(summary)))

if __name__ == '__main__':
    {'freeze': freeze, 'tests': tests, 'run': run}[sys.argv[1]]()
