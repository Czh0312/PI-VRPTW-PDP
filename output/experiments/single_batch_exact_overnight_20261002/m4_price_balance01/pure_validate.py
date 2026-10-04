"""Lightweight independent enumeration only: no Gurobi model or timing trial."""
from dataclasses import replace
import math
import random
import time
from common import OUT,read,write,sha,load_case,frozen_cases,external
from balance import transformed_prices,explicit_dual_bound
from models.routing.common.cycle import MultiBatchInstance
from models.routing.common.cycle.audit import audit_solution
from models.routing.solvers.exact.single_batch_columns import problem_fingerprint
from models.routing.solvers.exact.single_batch_pickup_bound import pickup_contexts,project_original_schedule
from tests.routing.test_single_batch_pickup_bound import independent_trips
from tests.routing.test_single_batch_solver_audit import tiny_problem,enumerate_tiny


def main():
    assert not (OUT/'pure_validation.json').exists();begun=time.perf_counter();cpu=time.process_time()
    rng=random.Random(61034);records=[];route_rows=assignment_rows=0
    for seed in (0,1,4,5):
        for duplicate in (False,True):
            base=tiny_problem(4,seed)
            p=(MultiBatchInstance(base.network,[base.orders[0],replace(base.orders[1],customer=base.orders[0].customer)],
                base.operating_policy,model=4) if duplicate else base)
            fp=problem_fingerprint(p);domains,contexts,check=pickup_contexts(p);assert check['passed']
            routes={s:independent_trips(p,s,c) for s,c in contexts.items()};assert all(routes.values())
            optimum=enumerate_tiny(p);assert math.isfinite(optimum)
            counts={s:c['maximum_trips'] for s,c in contexts.items()}
            for revision in range(3):
                prices={}
                for s,ctx in contexts.items():
                    prices[s]={}
                    for order in sorted({n['order'].id for n in ctx['nodes']}):
                        total=rng.uniform(-4,35);split=rng.uniform(-60,60)
                        prices[s][order,'d']=split;prices[s][order,'p']=total-split
                nu={s:-rng.uniform(0,4) for s in contexts}
                gamma={c:min(math.fsum(prices[s][o.id,k] for o in p.orders if o.customer==c for k in ('d','p'))
                             for s in choices) for c,choices in domains.items()}
                history=dict(phase=2,prices={s:dict(tasks=[[list(k),v] for k,v in pi.items()],fleet=nu[s])
                    for s,pi in prices.items()},assignment_duals=gamma,pricing_lower_bounds={s:123456 for s in contexts})
                for balanced in (False,True):
                    pp,gg=transformed_prices(p,history,balanced)
                    minima={s:min(cost-math.fsum(pp[s][0][k] for k in seq)-pp[s][1] for seq,cost in rr)
                            for s,rr in routes.items()}
                    certificate=explicit_dual_bound(pp,gg,counts,minima)
                    assert certificate['e2_bound']<=optimum+1e-7
                    for s,rr in routes.items():
                        for seq,cost in rr:
                            assert cost-math.fsum(pp[s][0][k] for k in seq)-(pp[s][1]+min(0,minima[s]))>=-1e-7
                            route_rows+=1
                    for c,choices in domains.items():
                        for s in choices:
                            assert gg[c]<=math.fsum(pp[s][0][o.id,k] for o in p.orders if o.customer==c for k in ('d','p'))+1e-7
                            assignment_rows+=1
                    for s,pi in prices.items():
                        for order in {k[0] for k in pi}:
                            assert abs(math.fsum([pp[s][0][order,'d'],pp[s][0][order,'p']])
                                       -math.fsum([pi[order,'d'],pi[order,'p']]))<1e-9
                    # Known-route clipping ceiling must dominate exact pricing correction.
                    witness={s:min(0,cost-math.fsum(pp[s][0][k] for k in seq)-pp[s][1])
                             for s,rr in routes.items() for seq,cost in rr[:1]}
                    ceiling=certificate['explicit_dual_objective']+math.fsum(counts[s]*v for s,v in witness.items())
                    assert certificate['raw_corrected']<=ceiling+1e-7
                    records.append(dict(seed=seed,duplicate=duplicate,revision=revision,balanced=balanced,
                        problem_sha256=fp,original_integer_optimum=optimum,history=history,
                        exact_full_route_minima=minima,known_route_formula_upper_ceiling=ceiling,**certificate))
            assert problem_fingerprint(p)==fp
    originals=[]
    for batch,case,folder in frozen_cases():
        p=load_case(folder);start=read(folder/'start.json');a=audit_solution(p,start)
        assert a['valid'] and a['periodic_replay']['valid'];_,contexts,check=pickup_contexts(p);assert check['passed']
        projected=project_original_schedule(p,start,contexts);assert sum(len(c['tasks']) for _,c in projected)==40
        archived=next(x for x in read(folder.parent/'projection.json') if x['case']==case)
        assert problem_fingerprint(p)==archived['problem_sha256']
        # Match archived witness routes to the original unchanged physical schedule.
        actual={(s,tuple(c['tasks'])):c['cost'] for s,c in projected}
        for s,c in archived['columns']:
            key=(s,tuple(tuple(k) for k in c['tasks']));assert abs(actual[key]-c['cost'])<1e-7
        originals.append(dict(case=case,problem_sha256=problem_fingerprint(p),start_sha256=sha(folder/'start.json'),
            original_all_tasks=40,audited=True,replay=True,archived_raw_route_witnesses_matched=True))
    write(OUT/'pure_records.json',records);write(OUT/'pure_original_projections.json',originals)
    summary=dict(tiny_original_integer_enumerations=8,repeated_customer_cases=4,price_states=len(records),
        complete_raw_route_dual_rows=route_rows,shared_assignment_dual_rows=assignment_rows,
        original_large40task_projections=2,exact_price_oracles='All independently enumerated raw elementary sequences',
        fresh_Gurobi_pricing_validation='PENDING_EXTERNAL_GATE',no_solver_models_created=True,
        seconds=time.perf_counter()-begun,cpu_seconds=time.process_time()-cpu,external=external())
    write(OUT/'pure_validation.json',summary);print({k:v for k,v in summary.items() if k!='external'})


if __name__=='__main__':main()
