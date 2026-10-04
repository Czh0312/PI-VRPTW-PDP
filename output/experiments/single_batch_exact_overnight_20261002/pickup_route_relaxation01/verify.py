"""Frozen projection and independent unpartitioned best-plane pricing audit."""
from pathlib import Path
import hashlib
import json
import math
import sys
import time

ROOT=Path(__file__).resolve().parents[4]
OUT=Path(__file__).resolve().parent
BATCH=OUT/'strength15_30'
sys.path.insert(0,str(BATCH/'source_snapshot'))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.common.cycle.audit import audit_solution
from models.routing.solvers.exact.single_batch_columns import problem_fingerprint
from models.routing.solvers.exact.single_batch_pickup_bound import pickup_contexts,project_original_schedule,e1_component_floor
from models.routing.solvers.exact.multibatch_route_pricing import TripPricingModel


def read(path):return json.loads(path.read_text())
def write(path,data):path.write_text(json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    target=BATCH/'verification';target.mkdir()
    manifest=read(BATCH/'manifest.json')
    assert sha(OUT/'diagnose.py')==manifest['driver_sha256']==sha(BATCH/'diagnose_frozen.py')
    for p,h in manifest['source_sha256'].items():assert sha(BATCH/'source_snapshot'/p)==h
    for item in manifest['input_sha256'].values():assert sha(BATCH/item['snapshot'])==item['sha256']
    contexts,projections={},[]
    for record in read(BATCH/'projection.json'):
        case=f'zhou_Set2_100-5-1b_subset20_M{record["model"]}'
        cfg,fleet=load_routing_case(BATCH/case/'case.json');p=load_instance(cfg,fleet)
        domains,by_site,_=pickup_contexts(p);contexts[case]=(p,domains,by_site)
        original=Path(record['result_path']);assert sha(original)==record['result_sha256']
        result=read(original);audit=audit_solution(p,result)
        assert audit['valid'] and audit['periodic_replay']['valid']
        assert problem_fingerprint(p)==record['problem_sha256']
        columns=project_original_schedule(p,result,by_site)
        assert sum(len(c['tasks']) for _,c in columns)==record['projected_tasks']==record['all_tasks']
        e1=e1_component_floor(p,domains)
        assert e1<=audit['distances']['e1']+1e-6
        projections.append(dict(model=p.model_number,seed=record['seed'],tasks=record['all_tasks'],
            original_objective=audit['objective'],e1_floor=e1,original_e1=audit['distances']['e1'],
            projected_e2=sum(c['cost'] for _,c in columns),audited=True,replay=True))
    rows=read(BATCH/'comparison.json');assert len(rows)==manifest['job_count']==8
    prices=[]
    for index,row in enumerate(rows,1):
        folder=BATCH/row['case']/f'bound_{row["budget"]}_{row["seed"]}'
        assert row==read(folder/'row.json')
        job,result=read(folder/'job.json'),read(folder/'result.json')
        p,domains,by_site=contexts[row['case']]
        start=read(Path(job['start_path']));audit=audit_solution(p,start)
        assert row['start_sha256']==sha(Path(job['start_path']))
        assert result['problem_sha256']==problem_fingerprint(p)
        assert result['proof_scope']=='B1_M3_M4_all_task_route_relaxation'
        assert not result['proven_optimal'] and not result['proven_infeasible']
        assert result['bound']<=audit['objective']+1e-6
        assert abs(result['bound']-result['e1_floor']-result['e2_bound'])<1e-8
        assert result['all_task_count']==2*len(p.orders)
        history=[h for h in result['history'] if h['phase']==2]
        expected=max([0.0]+[h['corrected_e2_bound'] for h in history])
        assert math.isclose(result['e2_bound'],expected,abs_tol=1e-7)
        best=max(history,key=lambda h:h['corrected_e2_bound']) if history else None
        # Nonpositive witness: zero is a safer E2 lower bound; its true minimum
        # need not be priced to validate a discarded negative certificate.
        if best is not None and result['e2_bound']>0:
            for site,terms in best['prices'].items():
                pi={tuple(k):v for k,v in terms['tasks']};nu=terms['fleet'];reported=best['pricing_lower_bounds'][site]
                deadline=time.perf_counter()+3
                pricer=TripPricingModel(p,by_site[site],seed=42,deadline=deadline)
                try:
                    independent=pricer.price(pi,nu,time_limit=max(0,deadline-time.perf_counter()))
                finally:pricer.dispose()
                lower=independent['lower_bound']
                minimum=independent['reduced_cost'] if independent['optimal'] else lower
                valid=independent['infeasible'] or reported<=minimum+1e-6
                prices.append(dict(model=p.model_number,seed=row['seed'],budget=row['budget'],site=site,
                    witness_iteration=best['iteration'],reported_lower=reported,independent=independent,checked=valid))
                write(target/'pricing.json',prices)
        print(f'verified {index}/{len(rows)} M{row["model"]} {row["seed"]}/{row["budget"]}',flush=True)
    summary=dict(original_schedule_projections=len(projections),original_audits=len(projections),periodic_replays=len(projections),
        bound_records=len(rows),independent_unpartitioned_prices=len(prices),
        complete_independent_prices=sum(r['independent']['optimal'] for r in prices),
        all_checked=all(r['checked'] for r in prices),integer_global_proofs=0,
        ignored_nonpositive_witnesses=sum(r['e2_bound']<=0 for r in rows),
        limitation='Best positive E2 witness per run independently repriced; discarded nonpositive/history planes are not claimed fully cross-checked. No original large integer solve in this diagnostic.')
    write(target/'projections.json',projections);write(target/'summary.json',summary);print(json.dumps(summary))
    assert summary['all_checked'],'Retain inconclusive/failed independent prices'


if __name__=='__main__':main()
