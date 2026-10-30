"""Frozen, held-out multi-seed validation; no selection on these results."""
import argparse
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from run_trial import run as baseline
from run_compact_trial import run as candidate

HERE = Path(__file__).resolve().parent
parser = argparse.ArgumentParser()
parser.add_argument('worker',choices=['A','B'])
args = parser.parse_args()
case = 'S03' if args.worker=='A' else 'S08'
schedule = []
for i,seed in enumerate([7,19,31]):
    arms = ['previous','compact_decomposition'] if (i+(args.worker=='B'))%2==0 else ['compact_decomposition','previous']
    for arm in arms:
        schedule.append((case,arm,seed,300))
for seed in ([7,31] if args.worker=='A' else [19]):
    for arm in (['previous','compact_decomposition'] if seed==7 else ['compact_decomposition','previous']):
        schedule.append(('S05',arm,seed,120))
plan = dict(worker=args.worker,schedule=schedule,held_out_seeds=[7,19,31],
            selected_candidate='compact_decomposition',threads_per_trial=1,max_concurrent_trials=2,
            selection_rationale='Prior seed-42 pilot improved hard-case certified lower bounds and closed the fast control; '
                                'conditional empty-suffix slot removal is exact and separately validated.',
            metrics=['full_domain_certificate_success','proof_wall_seconds_if_proven','final_gap_over_audited_upper',
                     'worst_and_median_gap','total_wall_seconds','native_work_units'],
            source_hashes={name:hashlib.sha256((HERE/name).read_bytes()).hexdigest() for name in
                           ['run_trial.py','run_compact_trial.py','decomposition_compact.py','run_holdout.py']})
plan_path = HERE/f'holdout_plan_{args.worker}.json'
if plan_path.exists():
    raise FileExistsError(plan_path)
plan_path.write_text(json.dumps(plan,ensure_ascii=False,indent=2),encoding='utf-8')
for selected_case,arm,seed,seconds in schedule:
    task = SimpleNamespace(case=selected_case,profile=arm,seed=seed,seconds=seconds,phase='holdout')
    (baseline if arm=='previous' else candidate)(task)
assert all(hashlib.sha256((HERE/name).read_bytes()).hexdigest()==digest for name,digest in plan['source_hashes'].items())
