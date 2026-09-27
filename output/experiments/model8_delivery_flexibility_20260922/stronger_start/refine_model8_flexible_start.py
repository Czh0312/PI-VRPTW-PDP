"""Post-hoc paired probe from the improved no-relay solution of 200-10-2.

Selected because the primary experiment improved delivery routing on this case.
This additional search is reported separately, never pooled into the 2x2 batch.
"""
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys
import time

from experiments.runs.run_model8_capacity_probe import ROOT, load_problem
from experiments.runs.run_model8_delivery_flexibility import DEFAULT_OUTPUT
from experiments.runs.run_model8_small_comparison import write
from experiments.analysis.summarize_model8_delivery_flexibility import remove_relay, delivery_changes
from models.routing.variants.model_8.baseline import prepare_baseline
from models.routing.variants.model_8.state import Evaluator


def main():
    folder = ROOT / DEFAULT_OUTPUT
    summary = json.loads((folder / 'summary.json').read_text())
    instance = 'Set2_200-10-2'
    case = next(c for c in summary['instances'] if c['instance'] == instance)
    seed = case['arms']['flex_no_relay']['best']['seed']
    source_result = folder / instance / f'flex_no_relay_{seed}.json'
    result = json.loads(source_result.read_text())
    assert not result['info']['state']['transfers']
    p, a, b, _ = load_problem(instance)
    routes = {s: [r for r in paths if len(r) > 2] for s, paths in result['e2_routes'].items()}
    base = prepare_baseline(p, a, b, p.fleet.e2_per_sat_count, (result['e1_routes'], routes))
    target = folder / 'stronger_start'
    target.mkdir(exist_ok=False)
    source = Path(__file__).resolve()
    shutil.copy2(source, target / source.name)
    write(target / instance / 'base.json', {'e1_routes': base['e1_routes'], 'e2_routes': base['e2_routes'],
          'info': {'objective': base['objective'], 'proven_optimal': False,
                   'source': 'no-relay feasible solution from primary experiment'}})
    dropped = sum(len(r) == 2 for paths in result['e2_routes'].values() for r in paths)
    write(target / 'manifest.json', {'instance': instance, 'budget': 20, 'seeds': [42, 7, 19],
          'source_result': str(source_result.relative_to(ROOT)),
          'source_result_sha256': hashlib.sha256(source_result.read_bytes()).hexdigest(),
          'script_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
          'baseline_fingerprint': base['route_fingerprint'], 'empty_vehicle_labels_removed': dropped,
          'scope': 'post-hoc stronger-start comparison, excluded from primary 2x2 batch; retains active baseline vehicle labels'})
    started = time.perf_counter()

    def launch(seed, arm):
        cmd = [sys.executable, '-m', 'experiments.runs.run_model8_delivery_flexibility',
               '--worker', '--instance', instance, '--arm', arm, '--seed', str(seed),
               '--time', '20', '--output', str(target.relative_to(ROOT))]
        with (target / instance / f'{arm}_{seed}.log').open('w') as log:
            try:
                run = subprocess.run(cmd, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                                     timeout=65, check=False)
                status = 'completed' if run.returncode == 0 else f'failed:{run.returncode}'
            except subprocess.TimeoutExpired:
                status = 'hard_timeout'
        return {'seed': seed, 'arm': arm, 'status': status, 'command': cmd}

    jobs = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(launch, seed, arm) for seed in (42, 7, 19)
                   for arm in ('flex_no_relay', 'flex_relay')]
        for future in as_completed(futures):
            job = future.result()
            jobs.append(job)
            write(target / 'jobs.json', {'jobs': jobs, 'elapsed': time.perf_counter() - started})
            print(json.dumps({k: v for k, v in job.items() if k != 'command'}), flush=True)
    if any(j['status'] != 'completed' for j in jobs):
        raise RuntimeError('Stronger-start batch incomplete')
    e = Evaluator(p, a, b, base, delivery_policy='flexible')
    records = []
    for job in jobs:
        saved = json.loads((target / instance / f"{job['arm']}_{job['seed']}.json").read_text())
        info = saved['info']
        audit = e.audit(info['state'])
        if (not math.isclose(audit['objective'], info['objective'], abs_tol=1e-6) or
                saved['e1_routes'] != base['e1_routes'] or
                saved['e2_routes'] != info['state']['e2_routes'] or
                info['baseline']['route_fingerprint'] != base['route_fingerprint'] or
                info['objective'] > base['objective'] + 1e-6 or
                (job['arm'] == 'flex_no_relay' and info['state']['transfers'])):
            raise RuntimeError('Stronger-start audit failed')
        changes, reordered = delivery_changes(e, info['state'])
        records.append({'seed': job['seed'], 'arm': job['arm'], 'objective': info['objective'],
                        'handoffs': info['transshipment_count'], 'split_deposits': info['split_handoff_deposit_count'],
                        'delivery_owners_changed': len(changes), 'delivery_sequences_changed': reordered,
                        'without_relay_same_routes': remove_relay(e, info['state'])})
    best = {arm: min(r['objective'] for r in records if r['arm'] == arm)
            for arm in ('flex_no_relay', 'flex_relay')}
    write(target / 'summary.json', {'status': 'PASS', 'audited_solutions': len(records) + 1,
          'baseline': base['objective'], 'empty_vehicle_labels_removed': dropped,
          'best': best, 'runs': records})
    print(json.dumps({'baseline': base['objective'], 'best': best, 'audited_solutions': len(records) + 1}), flush=True)


if __name__ == '__main__':
    main()
