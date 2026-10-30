"""Same-seed old-candidate pilot and exact long-run seed replay."""
import argparse
from run_trial import run

parser=argparse.ArgumentParser()
parser.add_argument('worker',choices=['A','B'])
args=parser.parse_args()
case,seed=('S03',31) if args.worker=='A' else ('S08',19)
run(case,'baseline',42,180,'pilot')
run(case,'sync',seed,180,'matched_long_seed')
if args.worker=='A':
    run('S05','baseline',42,60,'pilot')
    run('S05','sync',42,60,'pilot')
