"""Exact candidate-assignment census; never feasible-route/performance counts."""
from pathlib import Path
from dataclasses import replace
from collections import Counter
from itertools import product,combinations
import hashlib
import json
import math
import sys
import time

OUT=Path(__file__).resolve().parent;NIGHT=OUT.parent;SOURCE=NIGHT/'m4_price_balance01/source_snapshot'
sys.path.insert(0,str(SOURCE));sys.path.insert(0,str(NIGHT/'m4_price_balance01'))
from common import load_case,frozen_cases
from models.routing.common.cycle import MultiBatchInstance
from models.routing.common.cycle.audit import audit_solution
from models.routing.solvers.exact.single_batch_columns import problem_fingerprint
from models.routing.solvers.exact.single_batch_pickup_bound import pickup_contexts
from tests.routing.test_single_batch_solver_audit import tiny_problem


def read(p):return json.loads(p.read_text())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def write(p,v):p.write_text(json.dumps(v,indent=2,allow_nan=False)+'\n')


def counts(domains):
    sites=sorted(set(s for choices in domains.values() for s in choices));counts={}
    for size in range(1,len(sites)+1):
        for active in combinations(sites,size):
            value=0
            for k in range(len(active)+1):
                for used in combinations(active,k):
                    value+=(-1)**(len(active)-k)*math.prod(len(set(choices)&set(used)) for choices in domains.values())
            assert value>=0;counts[active]=value
    total=math.prod(len(v) for v in domains.values());assert sum(counts.values())==total
    return counts,total


def main():
    assert not (OUT/'summary.json').exists();begun=time.perf_counter();cpu=time.process_time();tiny=[];large=[]
    for seed in (0,1,4,5):
        for duplicate in (False,True):
            base=tiny_problem(4,seed)
            p=(MultiBatchInstance(base.network,[base.orders[0],replace(base.orders[1],customer=base.orders[0].customer)],base.operating_policy,model=4) if duplicate else base)
            domains,_,gate=pickup_contexts(p);assert gate['passed']
            calculated,total=counts(domains)
            direct=Counter(tuple(sorted(set(v))) for v in product(*(domains[c] for c in sorted(domains))))
            assert total==sum(direct.values()) and all(direct[mask]==n for mask,n in calculated.items())
            tiny.append(dict(seed=seed,duplicate=duplicate,customers=len(domains),orders=len(p.orders),
                assignment_candidates=total,counts=[dict(active=list(mask),count=n) for mask,n in calculated.items()]))
    for batch,case,folder in frozen_cases():
        p=load_case(folder);domains,_,gate=pickup_contexts(p);assert gate['passed'];calculated,total=counts(domains)
        start=read(folder/'start.json');audit=audit_solution(p,start);assert audit['valid'] and audit['periodic_replay']['valid']
        assignment=start['assignment'];assert set(assignment)==set(domains)
        assert all(assignment[c] in domains[c] for c in domains)
        active=tuple(sorted(set(assignment.values())));assert calculated[active]>0
        available=tuple(sorted(set(s for choices in domains.values() for s in choices)))
        large.append(dict(case=case,model=4,problem_sha256=problem_fingerprint(p),domains=domains,
            customers=len(domains),orders=len(p.orders),available_sites=list(available),candidate_assignments=total,
            active_mask_of_audited_original=list(active),all_sites_used_count=calculated[available],
            all_sites_used_fraction=calculated[available]/total,counts=[dict(active=list(mask),count=n,
                fraction=n/total) for mask,n in calculated.items()],original_all_tasks=40,
            original_schedule_audited=True,periodic_replay=True,start_sha256=sha(folder/'start.json')))
    summary=dict(tiny_cases=len(tiny),repeated_customer_cases=4,large_sources=len(large),
        no_solver_created=True,production_edits=[],seconds=time.perf_counter()-begun,cpu_seconds=time.process_time()-cpu,
        limitation='Assignment candidates before scheduling only; neither feasible-schedule counts nor a speed guarantee. No masked solver candidate started.')
    write(OUT/'tiny_counts.json',tiny);write(OUT/'large_counts.json',large);write(OUT/'summary.json',summary)
    print(summary)
    for row in large:print(row['case'],'candidates',row['candidate_assignments'],'all3active_fraction',row['all_sites_used_fraction'])


if __name__=='__main__':main()
