"""Seed-42 pilot of the exact conditional-cost decomposition."""
import argparse
from types import SimpleNamespace
from run_decomposition_trial import run

parser = argparse.ArgumentParser()
parser.add_argument('worker',choices=['A','B'])
args = parser.parse_args()
schedule = [('S03',180),('S05',90)] if args.worker=='A' else [('S08',180)]
for case,seconds in schedule:
    run(SimpleNamespace(case=case,seconds=seconds,seed=42,phase='pilot'))
