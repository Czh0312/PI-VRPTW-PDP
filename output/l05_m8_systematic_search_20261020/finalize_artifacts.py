"""Build shareable tables and figures from the completed search artifacts."""
from pathlib import Path
from collections import Counter
import json,csv,math,statistics,hashlib
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
ROOT=Path('output/l05_m8_systematic_search_20261020');DATA=Path('data/mechanism_l05_5sat_balanced25_v1')
rows=json.load((ROOT/'summary.json').open());best=json.load((ROOT/'best_full_domain.json').open());origin=json.load((ROOT/'best_origin.json').open())
assert json.load((ROOT/'integrity.json').open())['passed']
audit=json.load((ROOT/'best_independent_audit.json').open());assert audit['valid'] and audit['periodic_replay']['valid']
orders={o['order_id']:o for o in json.load((DATA/'reference_orders.json').open())}
assignment=best['order_batch_assignment'];fleet=json.load((DATA/'fleet.json').open())
def stamp(t):
 seconds=round(t);hours,remainder=divmod(seconds,3600);mins,seconds=divmod(remainder,60)
 return f'{hours:02}:{mins:02}:{seconds:02}'
route_rows=[]
for trip in best['e1_trips']:
 deliveries=[e for e in trip['events'] if e['kind']=='d'];pickups=[e for e in trip['events'] if e['kind']=='p']
 sites={e['site_id'] for e in deliveries}
 cargo=[o for oid,o in orders.items() if o['satellite_id'] in sites and assignment[oid]==trip['batch_id']]
 quantity=sum(e['quantity'] for e in deliveries);weight=sum(o['goods_weight']+o['container_count']*fleet['container_weight'] for o in cargo)
 route_rows.append(dict(batch=trip['batch_id'],vehicle=trip['vehicle_id'],trip=trip['trip_id'],
  departure=stamp(trip['departure']),return_complete=stamp(trip['return_complete']),
  delivery_sites=' -> '.join(e['site_id'] for e in deliveries),delivery_boxes=quantity,delivery_weight=weight,
  volume_util=quantity/fleet['E1_Ck'],weight_util=weight/fleet['E1_Qk'],
  pickup_sites=' -> '.join(e['site_id'] for e in pickups),pickup_boxes=sum(e['quantity'] for e in pickups)))
with (ROOT/'best_e1_routes.csv').open('w',encoding='utf-8-sig',newline='') as stream:
 writer=csv.DictWriter(stream,fieldnames=list(route_rows[0]));writer.writeheader();writer.writerows(route_rows)
q=Counter((orders[oid]['satellite_id'],batch) for oid,batch in assignment.items())
qsb={s:[q[s,f'b{i+1}'] for i in range(best['cycle_batch_count'])] for s in sorted({o['satellite_id'] for o in orders.values()})}
primary=[r for r in rows if r['label']=='M8_full_cold' and r['valid']]
warm=[r for r in rows if r['label']=='M8_full_warm' and r['valid']]
reference=next((r for r in rows if r['label']=='M3_reference' and r['valid']),None)
metrics=dict(cost=best['info']['objective'],N=best['container_pool_size'],K=best['cycle_batch_count'],
 saving_rate=(100-best['container_pool_size'])/100,turnover=100/best['container_pool_size'],
 D1=audit['distances']['e1'],D2=audit['distances']['e2'],trips1=audit['e1_trip_count'],trips2=audit['e2_trip_count'],
 vehicles1=audit['e1_vehicle_count'],vehicles2=audit['e2_vehicle_count'],
 e1_departure_volume_util=statistics.mean(r['volume_util'] for r in route_rows if r['delivery_boxes']),
 e1_departure_weight_util=statistics.mean(r['weight_util'] for r in route_rows if r['delivery_boxes']),
 qsb=qsb,origin=origin,two_cycle_valid=True,
 improvement_vs_initial_diagnostic=1-best['info']['objective']/387.29026089654,
 native_cold_successes=len(primary),native_cold_best=min((r['total_cost'] for r in primary),default=None),
 full_warm_successes=len(warm),full_warm_best=min((r['total_cost'] for r in warm),default=None))
if reference:metrics.update(M3_cost=reference['total_cost'],cost_reduction_vs_M3=1-best['info']['objective']/reference['total_cost'])
(ROOT/'best_metrics.json').write_text(json.dumps(metrics,indent=2))
# Show all observations rather than hiding poor seeds or failed runs.
labels=list(dict.fromkeys(r['label'] for r in rows if r['model']==8))
fig,(ax,bx)=plt.subplots(1,2,figsize=(13,5),layout='constrained')
colors={'M8_full_cold':'#c57843','M8_full_warm':'#377aa2'}
for i,label in enumerate(labels):
 local=[r for r in rows if r['label']==label and r['valid']]
 for j,r in enumerate(local):ax.scatter(i+(j-(len(local)-1)/2)*.09,r['total_cost'],s=45,c=colors.get(label,'#4b9570'))
 if not local:ax.text(i,420,'No feasible\nsolution',ha='center',fontsize=8)
ax.axhline(387.29026089654,color='#888',ls='--',label='Prior diagnostic incumbent: 387.29')
ax.axhline(best['info']['objective'],color='#285c42',ls=':',label='Best validated feasible solution')
ax.set_xticks(range(len(labels)));ax.set_xticklabels([x.replace('M8_','').replace('_','\n') for x in labels],fontsize=8)
ax.set(title='ALNS run endpoints; native and seeded runs separated',ylabel='Synthetic cost');ax.legend(fontsize=7)
if reference:
 for i,(name,parts) in enumerate([('M3',reference['cost_components']),('Best M8',best['cost_components'])]):
  bottom=0
  for key,color,title in [('container_pool','#62748c','Box pool'),('e1_distance','#e5a250','E1 distance'),('e2_distance','#3b87a3','E2 distance'),('cleaning','#b8b8b8','Cleaning + wear')]:
   amount=parts[key]+(parts['container_wear'] if key=='cleaning' else 0)
   bx.bar(i,amount,bottom=bottom,color=color,width=.6,label=title if i==0 else None);bottom+=amount
  bx.text(i,bottom+5,f'{bottom:.2f}',ha='center',fontsize=9)
 bx.set_xticks([0,1]);bx.set_xticklabels(['M3 reference','Best feasible M8']);bx.legend(fontsize=8)
bx.set(title='Common cost accounting (different search efforts)',ylabel='Synthetic cost')
for panel in (ax,bx):panel.spines[['top','right']].set_visible(False);panel.grid(axis='y',alpha=.12);panel.set_axisbelow(True)
fig.savefig(ROOT/'search_summary.png',dpi=180);plt.close(fig)
# E1 timeline makes simultaneous vehicles and reuse explicit.
fig,ax=plt.subplots(figsize=(12,4.8),layout='constrained')
for trip in best['e1_trips']:
 start=trip['departure']/3600;end=trip['return_complete']/3600;vehicle=trip['vehicle_id']
 ax.barh(vehicle,end-start,left=start,height=.6,color='#397da0',alpha=.8)
 ds=[e for e in trip['events'] if e['kind']=='d'];name='+'.join(e['site_id'] for e in ds)
 ax.text((start+end)/2,vehicle,f'{trip["batch_id"]}: {name}',ha='center',va='center',fontsize=7,color='white')
for b in best['batches']:
 ax.axvline(b['start']/3600,color='#777',alpha=.35,ls='--')
 ax.axvline(b['cutoff']/3600,color='#c26543',alpha=.6,ls=':')
ax.set(xlim=(8,21),xticks=range(8,22),yticks=range(fleet['E1_vehicle_count']),xlabel='Hour (dashed: batch start; dotted: recovery cutoff)',ylabel='Physical E1 vehicle ID',title='Best M8: physical vehicles, route intervals and multi-satellite deliveries')
fig.savefig(ROOT/'best_e1_timeline.png',dpi=180);plt.close(fig)
lines=['','## 最好方案的逐趟一级配送','',
 '| 批次 | 物理车辆ID | 配送仓顺序 | 箱数 | 出仓体积装载率 | 出发 | 回库完成 |',
 '|---|---:|---|---:|---:|---|---|']
for r in route_rows:lines.append(f"| {r['batch']} | {r['vehicle']} | {r['delivery_sites']} | {r['delivery_boxes']} | {r['volume_util']:.2%} | {r['departure']} | {r['return_complete']} |")
lines+=['',f"最好方案平均一级配送出仓体积装载率{metrics['e1_departure_volume_util']:.2%}、重量装载率{metrics['e1_departure_weight_util']:.2%}。车辆数按物理ID去重，趟次按实际路线计数。",'',
 f"相对本轮开始前已知387.2903诊断方案的额外改善为{metrics['improvement_vs_initial_diagnostic']:.2%}。该比例只衡量本轮新增搜索进展，不把之前的合并诊断贡献记作ALNS新收益。",'']
with (ROOT/'report.md').open('a',encoding='utf-8') as stream:stream.write('\n'.join(lines))
print(json.dumps(metrics,indent=2))
