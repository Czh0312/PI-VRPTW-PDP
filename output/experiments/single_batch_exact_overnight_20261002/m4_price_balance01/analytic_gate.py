"""Upper ceiling on a bound formula, from old audited feasible route witnesses."""
from pathlib import Path
import json
import math
import time

OUT=Path(__file__).resolve().parent
def read(p):return json.loads(p.read_text())
def write(p,v):p.write_text(json.dumps(v,indent=2,allow_nan=False)+'\n')


def main():
    assert not (OUT/'analytic_ceiling.json').exists();begun=time.perf_counter();rows=[]
    for batch in ('paired15_30','independent15_30'):
        archive=OUT/'frozen_inputs'/batch
        for projection in read(archive/'projection.json'):
            folder=archive/projection['case'];cfg=read(folder/'case.json')
            orders=read(archive/'inputs'/Path(cfg['orders']).name)
            customers={c['customer_id'] for c in orders}
            keys={c:[(o['order_id'],k) for o in orders if o['customer_id']==c for k in ('d','p')] for c in customers}
            assert projection['all_tasks']==projection['projected_tasks']==40
            columns=projection['columns'];assert sum(len(c['tasks']) for s,c in columns)==40
            for file in sorted(folder.glob('baseline*/result.json')):
                data=read(file);h=[h for h in data['history'] if h['phase']==2][-1]
                assert data['problem_sha256']==projection['problem_sha256']
                for balanced in (False,True):
                    prices={}
                    for s,rec in h['prices'].items():
                        pi={tuple(k):v for k,v in rec['tasks']}
                        if balanced:
                            for oid in sorted({k[0] for k in pi}):
                                pair=math.fsum([pi[oid,'d'],pi[oid,'p']])/2
                                pi[oid,'d']=pi[oid,'p']=pair
                        prices[s]=(pi,min(0.0,rec['fleet']))
                    gamma={c:min(h['assignment_duals'][c],*(math.fsum(pi[k] for k in kk)
                        for pi,nu in prices.values() if all(k in pi for k in kk))) for c,kk in keys.items()}
                    witness={s:0.0 for s in prices};route_rows=[]
                    for s,column in columns:
                        pi,nu=prices[s];tasks=[tuple(k) for k in column['tasks']]
                        rc=column['cost']-math.fsum(pi[k] for k in tasks)-nu
                        witness[s]=min(witness[s],rc)
                        route_rows.append(dict(site=s,tasks=column['tasks'],raw_cost=column['cost'],reduced_cost=rc))
                    dual=math.fsum(gamma.values())+math.fsum(data['maximum_trips'][s]*nu for s,(pi,nu) in prices.items())
                    ceiling=dual+math.fsum(data['maximum_trips'][s]*rc for s,rc in witness.items())
                    rows.append(dict(case=projection['case'],source_history=str(file),state=file.parent.name,
                        iteration=h['iteration'],balanced=balanced,explicit_dual=dual,
                        known_feasible_route_reduced_cost_upper_ceilings=witness,raw_certificate_upper_ceiling=ceiling,
                        original_route_witnesses=route_rows,problem_sha256=projection['problem_sha256']))
    assert len(rows)==16 and sum(r['balanced'] for r in rows)==8
    out=dict(rows=rows,balanced_all_strictly_nonpositive=all(r['raw_certificate_upper_ceiling']<-1e-6
        for r in rows if r['balanced']),seconds=time.perf_counter()-begun,
        classification='Upper ceiling on certificate formula; NEVER a problem lower bound or timing result',
        independent_fresh_validation_pending=True)
    write(OUT/'analytic_ceiling.json',out)
    for r in rows:print(r['case'],r['state'],r['balanced'],r['raw_certificate_upper_ceiling'])
    print('all balanced ceilings strictly negative:',out['balanced_all_strictly_nonpositive'])


if __name__=='__main__':main()
