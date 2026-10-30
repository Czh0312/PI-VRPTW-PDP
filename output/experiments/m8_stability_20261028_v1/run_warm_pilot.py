"""Small matched-budget pilot of audited native starts plus bound focus."""
import argparse
from types import SimpleNamespace
from run_warm_trial import run

parser = argparse.ArgumentParser()
parser.add_argument('worker',choices=['A','B'])
args = parser.parse_args()
schedule = [('S03',180),('S05',90)] if args.worker=='A' else [('S08',180)]
for case,seconds in schedule:
    run(SimpleNamespace(case=case,profile='warm_focus',seconds=seconds,seed=42,phase='pilot'))
