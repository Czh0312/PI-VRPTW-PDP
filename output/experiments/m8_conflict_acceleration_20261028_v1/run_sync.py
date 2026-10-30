"""Reproduce the tested sync candidate on one of the three frozen cases."""
import argparse
from run_trial import run

parser=argparse.ArgumentParser()
parser.add_argument('--case',choices=['S03','S08','S05'],required=True)
parser.add_argument('--seed',type=int,default=11)
parser.add_argument('--seconds',type=float,default=180)
parser.add_argument('--phase',default='manual')
args=parser.parse_args()
run(args.case,'sync',args.seed,args.seconds,args.phase)
