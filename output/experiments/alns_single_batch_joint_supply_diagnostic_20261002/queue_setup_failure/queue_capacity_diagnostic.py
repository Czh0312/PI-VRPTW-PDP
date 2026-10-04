"""Compare existing complete descent queues on frozen, held incumbents."""
import gzip,json,time,traceback
from pathlib import Path
import joint_diagnostic as d
def normalized_key(state):
    copy=d.clone_state(state);copy['e2']=[r for r in copy['e2'] if r['tasks']]
    return d.state_key(copy)[3]
def main():
    for name,h in json.loads((d.OUT/'queue_registration_hashes.json').read_text()).items():assert d.sha(d.OUT/name)==h,name
    protocol=json.loads((d.OUT/'queue_protocol.json').read_text());summaries=[]
    for index,item in enumerate(json.loads((d.OUT/'incumbent_manifest.json').read_text())):
        folder=d.OUT/'queues'/Path(item['name']).stem;folder.mkdir(parents=True)
        config,fleet=d.load_routing_case(item['case']);p=d.load_instance({**config,'model':3},fleet)
        raw=json.loads((d.OUT/'incumbents'/item['name']).read_text());state=d.state_from_result(p,raw['result'])
        base,decoded,audit=d.measure(p,state);assert base['valid'] and abs(base['objective']-raw['result']['info']['objective'])<=1e-7
        ranks={}
        for row in json.loads((d.OUT/'expanded'/Path(item['name']).stem/'all_candidates.json').read_text()):
            ranks[normalized_key(d.apply_e2(state,row['changes']))]=row['rank']
        pair=[]
        for capacity in (protocol['capacities'] if index%2==0 else list(reversed(protocol['capacities']))):
            start=time.perf_counter();deadline=start+protocol['per_queue_seconds']
            descent=d.SingleBatchDescent(p,capacity=capacity,cache_sites=False,cache_distances=True)
            best=None;rows=[];exhausted=False
            while time.perf_counter()<deadline:
                proposal=descent.propose(state,decoded,deadline)
                if proposal is None:exhausted=True;break
                measured,result,checked=d.measure(p,proposal)
                mode='e2' if normalized_key(proposal)!=normalized_key(state) else 'e1'
                rank=ranks.get(normalized_key(proposal)) if mode=='e2' else None
                row=dict(index=len(rows),mode=mode,rank=rank,measure=measured);rows.append(row)
                with gzip.open(folder/('cap%d_trial%04d.json.gz'%(capacity,row['index'])),'wt',encoding='utf-8') as handle:
                    json.dump(dict(state=proposal,measure=row,result=result,independent_audit=checked),handle,indent=2)
                if measured['valid'] and measured['objective']<base['objective']-1e-7 and (best is None or measured['objective']<best['objective']):
                    best=dict(mode=mode,rank=rank,index=row['index'],objective=measured['objective'],change_percent=100*(measured['objective']/base['objective']-1))
            d.write(folder/('cap%d_rows.json'%capacity),rows)
            summary=dict(name=item['name'],job=item['job'],capacity=capacity,baseline=base['objective'],
                exhausted=exhausted,seconds=time.perf_counter()-start,proposed=len(rows),filtered=descent.filtered,
                generated=descent.generated,deferred=descent.deferred_count,recovered=descent.recovered,
                valid=sum(r['measure']['valid'] for r in rows),best=best)
            d.write(folder/('cap%d_summary.json'%capacity),summary);pair.append(summary)
        summaries.append(dict(name=item['name'],queues=pair));d.write(d.OUT/'queue_summary.json',summaries)
        print(json.dumps(summaries[-1]),flush=True)
    d.write(d.OUT/'queue_completion.json',dict(jobs=len(summaries),all_exhausted=all(q['exhausted'] for r in summaries for q in r['queues'])))
if __name__=='__main__':main()
