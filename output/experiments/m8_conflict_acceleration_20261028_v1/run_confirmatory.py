"""Freeze the two-profile comparison before using three new solver seeds."""
import argparse
from common import HERE,OLD,ROOT,save,sha
from run_trial import run

parser=argparse.ArgumentParser()
parser.add_argument('worker',choices=['A','B'])
args=parser.parse_args()
case='S03' if args.worker=='A' else 'S08'
schedule=[]
for i,seed in enumerate([11,23,37]):
    arms=['baseline','sync'] if (i+(args.worker=='B'))%2==0 else ['sync','baseline']
    schedule.extend((case,arm,seed,180) for arm in arms)
for seed in ([11,37] if args.worker=='A' else [23]):
    arms=['baseline','sync'] if seed==11 else ['sync','baseline']
    schedule.extend(('S05',arm,seed,60) for arm in arms)
files=[HERE/'run_confirmatory.py',HERE/'run_trial.py',HERE/'common.py',HERE/'master_sync.py',
       HERE/'decomposition_sync.py',OLD/'decomposition_compact.py',OLD/'route_bounds.py',
       ROOT/'experiments/runs/research_model8_shared_relaxation.py']
frozen={str(path):sha(path) for path in files}
plan=HERE/f'confirmatory_plan_{args.worker}.json'
if plan.exists():
    raise FileExistsError(plan)
save(plan,dict(worker=args.worker,schedule=schedule,seeds=[11,23,37],threads=1,max_concurrent_trials=2,
    selected_candidate='sync',source_hashes=frozen,
    selection='Only common-trip recovery and loading/recovery precedence links were added to the frozen master. '
              'Seed 42 pilot selected this minimal change; these three new seeds were not used for tuning.',
    historical_information='Both arms receive the identical old U/L; newly proven exact optima are never supplied as L.'))
for selected_case,arm,seed,seconds in schedule:
    run(selected_case,arm,seed,seconds,'confirmatory')
assert all(sha(path)==digest for path,digest in frozen.items())
