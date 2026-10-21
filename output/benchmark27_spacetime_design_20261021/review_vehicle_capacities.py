"""Read-only load envelopes over the audited witnesses; do not alter inputs."""
from pathlib import Path
from collections import Counter,defaultdict
import csv,json,statistics,zipfile,math
R=Path('data/formal_benchmark27_spacetime_v2');O=Path('output/benchmark27_spacetime_design_20261021')
m=json.loads((R/'manifest.json').read_text());rows=[];base_rows=[]
with zipfile.ZipFile(R/'validation/witnesses.zip') as z:
 for b in m['base_cases']:
  root=R/'shared'/b['size']/b['id'];f=json.loads((root/'fleet.json').read_text());orders=json.loads((root/'reference_orders.json').read_text());od={o['order_id']:o for o in orders};unit=[o['goods_weight']+f['container_weight'] for o in orders]
  base_rows.append(dict(id=b['id'],size=b['size'],n=b['customer_count'],sites=b['satellite_count'],periods=b['external_period_count'],
    E1_C=f['E1_Ck'],E1_W=f['E1_Qk'],E2_C=f['E2_Cv'],E2_W=f['E2_Qv'],max_box_weight=max(unit),
    E1_weight_upper_bound_at_full_volume=f['E1_Ck']*max(unit),E2_weight_upper_bound_at_full_volume=f['E2_Cv']*max(unit),
    E1_weight_redundant=f['E1_Qk']>=f['E1_Ck']*max(unit),E2_weight_redundant=f['E2_Qv']>=f['E2_Cv']*max(unit)))
  for model in range(1,9):
   s=json.loads(z.read(f'{b["id"]}/model_{model}.json'))
   assignment=s.get('order_batch_assignment') or {o['order_id']:('b1' if model<=4 else o['delivery_batch_id']) for o in orders}
   for layer in ('e1','e2'):
    wc=f['E1_Qk'] if layer=='e1' else f['E2_Qv'];vc=f['E1_Ck'] if layer=='e1' else f['E2_Cv']
    for trip in s[layer+'_trips']:
     deliveries=[e for e in trip['events'] if e['kind']=='d']
     if layer=='e1':
      sites={e['site_id'] for e in deliveries};cargo=[o for o in orders if o['satellite_id'] in sites and assignment[o['order_id']]==trip['batch_id']]
     else:cargo=[od[e['order_id']] for e in deliveries]
     w=sum(o['goods_weight']+o['container_count']*f['container_weight'] for o in cargo);v=sum(o['container_count']*f['container_volume'] for o in cargo)
     depart_w,depart_v=w,v;peakw,peakv=w,v
     for e in trip['events']:
      if layer=='e1':
       if e['kind']=='d':
        local=[o for o in cargo if o['satellite_id']==e['site_id']];dw=sum(o['goods_weight']+o['container_count']*f['container_weight'] for o in local)
       else:dw=e['quantity']*f['container_weight']
       dv=e['quantity']*f['container_volume']
      else:
       order=od[e['order_id']];dw=(order['goods_weight'] if e['kind']=='d' else 0)+order['container_count']*f['container_weight'];dv=order['container_count']*f['container_volume']
      w+=dw if e['kind']=='p' else -dw;v+=dv if e['kind']=='p' else -dv
      peakw=max(peakw,w);peakv=max(peakv,v)
     assert peakw<=wc+1e-6 and peakv<=vc+1e-6 and w>=-1e-6 and v>=-1e-6
     rows.append(dict(id=b['id'],size=b['size'],model=model,layer=layer,trip=trip['trip_id'],
       weight_capacity=wc,volume_capacity=vc,departure_weight=depart_w,departure_volume=depart_v,
       peak_weight=peakw,peak_volume=peakv,peak_weight_util=peakw/wc,peak_volume_util=peakv/vc))
summary={}
for size in ('small','medium','large'):
 for layer in ('e1','e2'):
  group=[r for r in rows if r['size']==size and r['layer']==layer and r['model']==5]
  summary[f'{size}_{layer}_M5_witness']=dict(trips=len(group),mean_departure_volume_util=statistics.mean(r['departure_volume']/r['volume_capacity'] for r in group),
    mean_peak_weight_util=statistics.mean(r['peak_weight_util'] for r in group),max_peak_weight_util=max(r['peak_weight_util'] for r in group),
    max_peak_volume_util=max(r['peak_volume_util'] for r in group))
proposed=defaultdict(list)
for r in rows:
 cap=r['volume_capacity']*20 if r['layer']=='e1' else 120
 if r['peak_weight']>cap+1e-8:proposed[r['layer']].append(dict(id=r['id'],model=r['model'],trip=r['trip'],peak=r['peak_weight'],cap=cap))
result=dict(scope='All 216 feasibility witnesses; not optimized ALNS solution load factors',bases=base_rows,
  all_E1_weight_limits_redundant=all(r['E1_weight_redundant'] for r in base_rows),
  all_E2_weight_limits_redundant=all(r['E2_weight_redundant'] for r in base_rows),
  M5_witness_summary=summary,
  hypothetical_weight_only_review=dict(E1_formula='20 * E1 volume capacity',E2_weight=120,
    existing_witness_trips_exceeding=dict(proposed),note='No dataset changes or alternative-route searches; failing witnesses do not imply infeasible instances.'))
(O/'vehicle_capacity_review.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
with (O/'vehicle_capacity_trip_loads.csv').open('w',encoding='utf-8-sig',newline='') as f:
 writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
print(json.dumps({k:v for k,v in result.items() if k!='bases'},ensure_ascii=False,indent=2))
print('hypothetical_failures_count',{k:len(v) for k,v in proposed.items()})
