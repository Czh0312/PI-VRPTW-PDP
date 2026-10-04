"""Inspect capacity/window crowding of the bounded geometry candidate queue.

Extra incumbent descent is descriptive, never an equal-time quality result.
"""
import gzip,json,os,time,traceback
from pathlib import Path
import joint_diagnostic as d
from models.routing.solvers.alns.neighborhoods import _goods_ready, _route_score

def main():
    for name,h in json.loads((d.OUT/'expanded_registration_hashes.json').read_text()).items():assert d.sha(d.OUT/name)==h,name
    protocol=json.loads((d.OUT/'expanded_protocol.json').read_text());summaries=[]
    for item in json.loads((d.OUT/'incumbent_manifest.json').read_text()):
        folder=d.OUT/'expanded'/Path(item['name']).stem;folder.mkdir(parents=True)
        try:
            config,fleet=d.load_routing_case(item['case']);p=d.load_instance({**config,'model':3},fleet)
            raw=json.loads((d.OUT/'incumbents'/item['name']).read_text());state=d.state_from_result(p,raw['result'])
            baseline,decoded,audit=d.measure(p,state)
            assert baseline['valid'] and abs(baseline['objective']-raw['result']['info']['objective'])<=1e-7
            start=time.perf_counter();deadline=start+protocol['per_incumbent_seconds']
            descent=d.SingleBatchDescent(p,capacity=protocol['geometry_capacity'],cache_sites=False,cache_distances=True)
            enum_deadline=min(deadline,time.perf_counter()+protocol['enumeration_seconds'])
            options=descent._enumerate_routes(state['e2'],enum_deadline)
            enumeration_timeout=time.perf_counter()>=enum_deadline
            goods=_goods_ready(p,state);rows=[];tested=0;best=None;valid=0
            for rank,(gain,serial,changes) in enumerate(options,1):
                if time.perf_counter()>=deadline:break
                checks=[]
                for index,tasks in changes:
                    route={**state['e2'][index],'tasks':list(tasks)}
                    score,complete=_route_score(p,route,goods,p.operating_policy.cycle_start)
                    distance=descent.task_distance(route['satellite'],tasks)
                    checks.append(dict(index=index,score=score,distance=distance,complete=complete,passing=score<=distance+1e-6))
                row=dict(rank=rank,e2_distance_gain=gain,changes=changes,screen=checks,screen_passed=all(c['passing'] for c in checks))
                rows.append(row)
                if not row['screen_passed']:continue
                if tested>=protocol['decode_capacity']:
                    row['decode_skipped']='decode capacity reached';continue
                candidate=d.apply_e2(state,changes);measurement,result,checked=d.measure(p,candidate)
                row['measure']=measurement;tested+=1;valid+=int(measurement['valid'])
                with gzip.open(folder/('rank_%04d.json.gz'%rank),'wt',encoding='utf-8') as handle:
                    json.dump(dict(rank=rank,state=candidate,measure=measurement,result=result,independent_audit=checked),handle,indent=2)
                if measurement['valid'] and measurement['objective']<baseline['objective']-1e-7:
                    if best is None or measurement['objective']<best['objective']:
                        best=dict(rank=rank,objective=measurement['objective'],change_percent=100*(measurement['objective']/baseline['objective']-1))
            d.write(folder/'all_candidates.json',rows)
            summary=dict(name=item['name'],job=item['job'],baseline=baseline['objective'],
                enumerated=len(options),enumeration_timeout=enumeration_timeout,inspected=len(rows),
                screen_passed=sum(r['screen_passed'] for r in rows),decoded=tested,valid=valid,best=best,
                time_limit_reached=time.perf_counter()>=deadline,seconds=time.perf_counter()-start,load=os.getloadavg(),
                strata=[dict(limit=limit,inspected=sum(r['rank']<=limit for r in rows),screen_passed=sum(r['rank']<=limit and r['screen_passed'] for r in rows),
                             decoded=sum(r['rank']<=limit and 'measure' in r for r in rows),valid=sum(r['rank']<=limit and r.get('measure',{}).get('valid',False) for r in rows),
                             improving=sum(r['rank']<=limit and r.get('measure',{}).get('valid',False) and r['measure']['objective']<baseline['objective']-1e-7 for r in rows))
                        for limit in (64,256,4096)])
            d.write(folder/'summary.json',summary)
        except Exception as exc:
            traceback.print_exc();summary=dict(name=item['name'],status='FAILED',error=repr(exc))
        summaries.append(summary);d.write(d.OUT/'expanded_summary.json',summaries)
        print(json.dumps(summary),flush=True)
    d.write(d.OUT/'expanded_completion.json',dict(jobs=len(summaries),failures=sum(r.get('status')=='FAILED' for r in summaries)))
if __name__=='__main__':main()
