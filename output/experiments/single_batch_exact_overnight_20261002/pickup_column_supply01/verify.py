"""Validate frozen root pairs and independently reprice positive witnesses."""
from pathlib import Path
import hashlib
import json
import math
import sys
import time

ROOT=Path(__file__).resolve().parents[4];OUT=Path(__file__).resolve().parent
BATCH=OUT/(sys.argv[1] if len(sys.argv)>1 else 'paired15_30')
sys.path.insert(0,str(BATCH/'source_snapshot'))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.common.cycle.audit import audit_solution
from models.routing.solvers.exact.single_batch_columns import problem_fingerprint
from models.routing.solvers.exact.single_batch_pickup_bound import pickup_contexts,project_original_schedule,e1_component_floor
from models.routing.solvers.exact.multibatch_route_pricing import TripPricingModel
def read(p):return json.loads(p.read_text())
def write(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()

def main():
    target=BATCH/'verification';target.mkdir()
    m=read(BATCH/'manifest.json')
    driver=BATCH/'compare_frozen.py'
    assert sha(driver)==m['driver_sha256']
    for p,h in m['source_sha256'].items():assert sha(BATCH/'source_snapshot'/p)==h
    for item in m['input_sha256'].values():assert sha(BATCH/item['snapshot'])==item['sha256']
    contexts={};projections=[]
    for record in read(BATCH/'projection.json'):
        folder=BATCH/record['case'];cfg,fleet=load_routing_case(folder/'case.json');p=load_instance(cfg,fleet)
        domains,by_site,_=pickup_contexts(p);contexts[record['case']]=(p,domains,by_site)
        start=read(folder/'start.json');audit=audit_solution(p,start)
        assert audit['valid'] and audit['periodic_replay']['valid']
        assert problem_fingerprint(p)==record['problem_sha256']
        cols=project_original_schedule(p,start,by_site)
        assert sum(len(c['tasks']) for _,c in cols)==record['all_tasks']==2*len(p.orders)
        assert e1_component_floor(p,domains)<=audit['distances']['e1']+1e-6
        projections.append(dict(case=record['case'],tasks=record['all_tasks'],audited=True,replay=True))
    rows=read(BATCH/'comparison.json');assert len(rows)==m['job_count']
    prices=[];pair_groups={}
    for row in rows:
        folder=BATCH/row['case']/f'{"candidate" if row["heuristic"] else "baseline"}_{row["budget"]}_{row["seed"]}'
        assert row==read(folder/'row.json');job=read(folder/'job.json');r=read(folder/'result.json')
        p,domains,by_site=contexts[row['case']];start=read(Path(job['start_path']));audit=audit_solution(p,start)
        assert row['start_sha256']==sha(Path(job['start_path']))
        assert r['problem_sha256']==problem_fingerprint(p)
        assert not r['proven_optimal'] and not r['proven_infeasible']
        assert r['bound']<=audit['objective']+1e-6
        assert math.isclose(r['bound'],r['e1_floor']+r['e2_bound'],abs_tol=1e-7)
        assert r['all_task_count']==2*len(p.orders) and r['heuristic_columns']==row['heuristic']
        assert all(h['corrected_e2_bound'] is None for h in r['heuristic_history'])
        # Discovery passes cannot certify closure. No more than two successful
        # passes may appear between exact pricing records, including phase I.
        sequence=sorted([(h['iteration'],'heuristic',h['added']) for h in r['heuristic_history']]+
            [(h['iteration'],'exact',0) for h in r['history']])
        consecutive=0
        for _,kind,added in sequence:
            consecutive=consecutive+1 if kind=='heuristic' and added else 0
            assert consecutive<=2
        history=[h for h in r['history'] if h['phase']==2]
        assert math.isclose(r['e2_bound'],max([0.0]+[h['corrected_e2_bound'] for h in history]),abs_tol=1e-7)
        if r['e2_bound']>0:
            best=max(history,key=lambda h:h['corrected_e2_bound'])
            for site,terms in best['prices'].items():
                pi={tuple(k):v for k,v in terms['tasks']};nu=terms['fleet'];reported=best['pricing_lower_bounds'][site]
                deadline=time.perf_counter()+3
                pricer=TripPricingModel(p,by_site[site],deadline=deadline,seed=42)
                try:independent=pricer.price(pi,nu,time_limit=max(0,deadline-time.perf_counter()))
                finally:pricer.dispose()
                minimum=independent['reduced_cost'] if independent['optimal'] else independent['lower_bound']
                valid=independent['infeasible'] or reported<=minimum+1e-6
                prices.append(dict(case=row['case'],seed=row['seed'],budget=row['budget'],heuristic=row['heuristic'],
                    site=site,reported_lower=reported,independent=independent,checked=valid))
                write(target/'pricing.json',prices)
        key=(row['case'],row['seed'],row['budget']);pair_groups.setdefault(key,{})[row['heuristic']]=row
        print(f'verified {row["case"]} {row["seed"]}/{row["budget"]} heuristic{row["heuristic"]}',flush=True)
    pairs=[]
    for (case,seed,budget),group in pair_groups.items():
        assert set(group)=={False,True};b,c=group[False],group[True]
        assert b['start_sha256']==c['start_sha256'] and b['full_original_upper']==c['full_original_upper']
        pairs.append(dict(case=case,seed=seed,budget=budget,baseline=b['bound'],candidate=c['bound'],
            difference=c['bound']-b['bound'],candidate_heuristic_seconds=c['heuristic_runtime'],
            candidate_added=c['heuristic_added']))
    summary=dict(original_schedule_projections=len(projections),bound_records=len(rows),pairs=len(pairs),
        independent_prices=len(prices),complete_independent_prices=sum(p['independent']['optimal'] for p in prices),
        all_checked=all(p['checked'] for p in prices),integer_global_proofs=0,
        nonpositive_witnesses_not_repriced=sum(r['e2_bound']<=0 for r in rows),
        heuristic_quota_max_overrun=max([0.0]+[r['heuristic_runtime']-.1*r['budget'] for r in rows]),
        limitation='Only positive best certificates independently repriced; no end-to-end performance or original integer global proof')
    write(target/'pairs.json',pairs);write(target/'projections.json',projections);write(target/'summary.json',summary)
    print(json.dumps(summary));assert summary['all_checked'],'Keep inconclusive or failed prices'

if __name__=='__main__':main()
