"""Predeclared seed-42 pilot schedules, one solver at a time per worker."""
import argparse
from types import SimpleNamespace

from run_trial import run

parser = argparse.ArgumentParser()
parser.add_argument('worker',choices=['A','B'])
args = parser.parse_args()
schedule = ([('S03','previous',180),('S03','masks',180),
             ('S05','previous',90),('S05','masks',90)] if args.worker=='A' else
            [('S08','previous',180),('S08','reduced',180),('S08','masks',180)])
for case,profile,seconds in schedule:
    run(SimpleNamespace(case=case,profile=profile,seconds=seconds,seed=42,phase='pilot'))
