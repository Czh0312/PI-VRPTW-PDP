"""Replay E1 arc loads; report departure, return, peak and distance-weighted use."""
from pathlib import Path
import json,math,csv,collections,statistics
ROOT=Path('output/mechanism_l05_3sat_balanced_20261020')
DATA=Path('data/mechanism_l05_3sat_balanced_v1')
orders=json.load((DATA/'reference_orders.json').open());fleet=json.load((DATA/'fleet.json').open())
nodes={r['node_id']:r for r in csv.DictReader((DATA/'network.csv').open())}
capacity_w=fleet['E1_Qk'];capacity_v=fleet['E1_Ck'];cw=fleet['container_weight'];cv=fleet['container_volume']
rows=[];runs=[]
for group in ['M3','M8_feasible_start','M8_K3_feasible_start']:
 for path in sorted((ROOT/group).glob('seed_*/result.json')):
  result=json.load(path.open());assert result['audit']['valid']
  assignment=result.get('order_batch_assignment',{o['order_id']:'b1' for o in orders})
  delivered_w=collections.Counter();delivered_v=collections.Counter()
  for o in orders:
   key=(assignment[o['order_id']],o['satellite_id'])
   delivered_w[key]+=o['goods_weight']+o['container_count']*cw
   delivered_v[key]+=o['container_count']*cv
  seed=int(path.parent.name.split('_')[1]);local=[]
  def dist(a,b):return math.hypot(float(nodes[a]['x'])-float(nodes[b]['x']),float(nodes[a]['y'])-float(nodes[b]['y']))
  for trip in result['e1_trips']:
   batch=trip['batch_id'];events=trip['events'];delivery=[e for e in events if e['kind']=='d'];pickup=[e for e in events if e['kind']=='p']
   weight=sum(delivered_w[batch,e['site_id']] for e in delivery);volume=sum(delivered_v[batch,e['site_id']] for e in delivery)
   start_w,start_v=weight,volume; peak_w,peak_v=weight,volume
   last='D0';distance=weighted_w=weighted_v=empty_distance=0
   for e in events:
    leg=dist(last,e['site_id']);distance+=leg;weighted_w+=leg*weight;weighted_v+=leg*volume
    if volume==0:empty_distance+=leg
    if e['kind']=='d':weight-=delivered_w[batch,e['site_id']];volume-=delivered_v[batch,e['site_id']]
    else:weight+=e['quantity']*cw;volume+=e['quantity']*cv
    assert -1e-8<=weight<=capacity_w+1e-8 and -1e-8<=volume<=capacity_v+1e-8
    peak_w=max(peak_w,weight);peak_v=max(peak_v,volume);last=e['site_id']
   leg=dist(last,'D0');distance+=leg;weighted_w+=leg*weight;weighted_v+=leg*volume
   if volume==0:empty_distance+=leg
   row=dict(group=group,seed=seed,trip_id=trip['trip_id'],batch=batch,
    delivery_boxes=sum(e['quantity'] for e in delivery),pickup_boxes=sum(e['quantity'] for e in pickup),
    has_delivery=bool(delivery),has_pickup=bool(pickup),departure_weight=start_w,departure_volume=start_v,
    return_weight=weight,return_volume=volume,peak_weight=peak_w,peak_volume=peak_v,
    departure_weight_util=start_w/capacity_w,departure_volume_util=start_v/capacity_v,
    return_weight_util=weight/capacity_w,return_volume_util=volume/capacity_v,
    distance=distance,weight_distance=weighted_w,volume_distance=weighted_v,
    distance_weight_util=weighted_w/(capacity_w*distance),distance_volume_util=weighted_v/(capacity_v*distance),empty_distance=empty_distance)
   rows.append(row);local.append(row)
  assert abs(sum(r['distance'] for r in local)-result['audit']['distances']['e1'])<1e-7
  d=[r for r in local if r['has_delivery']];p=[r for r in local if r['has_pickup']]
  run=dict(group=group,seed=seed,trips=len(local),delivery_trips=len(d),pickup_trips=len(p),
   delivery_boxes_mean=statistics.mean(r['delivery_boxes'] for r in d),
   departure_weight_mean=statistics.mean(r['departure_weight'] for r in d),
   departure_weight_util=statistics.mean(r['departure_weight_util'] for r in d),
   departure_volume_util=statistics.mean(r['departure_volume_util'] for r in d),
   return_weight_util=statistics.mean(r['return_weight_util'] for r in p),
   return_volume_util=statistics.mean(r['return_volume_util'] for r in p),
   peak_weight_util=max(r['peak_weight'] for r in local)/capacity_w,
   peak_volume_util=max(r['peak_volume'] for r in local)/capacity_v,
   distance_weight_util=sum(r['weight_distance'] for r in local)/(capacity_w*sum(r['distance'] for r in local)),
   distance_volume_util=sum(r['volume_distance'] for r in local)/(capacity_v*sum(r['distance'] for r in local)))
  runs.append(run)
aggregates={}
for group in sorted({r['group'] for r in rows}):
 local=[r for r in rows if r['group']==group];d=[r for r in local if r['has_delivery']];p=[r for r in local if r['has_pickup']]
 aggregates[group]=dict(runs=3,trips=len(local),delivery_trips=len(d),pickup_trips=len(p),
  mean_boxes_per_delivery_trip=statistics.mean(r['delivery_boxes'] for r in d),
  departure_weight_util=statistics.mean(r['departure_weight_util'] for r in d),
  departure_volume_util=statistics.mean(r['departure_volume_util'] for r in d),
  return_weight_util=statistics.mean(r['return_weight_util'] for r in p),
  return_volume_util=statistics.mean(r['return_volume_util'] for r in p),
  peak_weight_util=max(r['peak_weight'] for r in local)/capacity_w,
  peak_volume_util=max(r['peak_volume'] for r in local)/capacity_v,
  distance_weight_util=sum(r['weight_distance'] for r in local)/(capacity_w*sum(r['distance'] for r in local)),
  distance_volume_util=sum(r['volume_distance'] for r in local)/(capacity_v*sum(r['distance'] for r in local)))
out=dict(capacity_weight=capacity_w,capacity_volume=capacity_v,container_weight=cw,container_volume=cv,
 goods_weight_total=sum(o['goods_weight'] for o in orders),delivery_weight_total=sum(o['goods_weight']+o['container_count']*cw for o in orders),
 units='Original benchmark units; no kg or m3 calibration is asserted.',
 averaging='Departure: only trips carrying deliveries; return: only trips carrying empties. Group averages pool all trips across 3 seeds. Distance-weighted use includes all driven arcs and empty legs; excludes waiting time.',
 aggregates=aggregates,runs=runs)
(ROOT/'load_analysis/summary.json').write_text(json.dumps(out,indent=2))
with (ROOT/'load_analysis/trips.csv').open('w',encoding='utf-8-sig',newline='') as stream:
 writer=csv.DictWriter(stream,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
lines=['# 一级车辆装载率复核','',
 '容量：重量3000、体积200；一个箱体积1、空箱重量2。重量/体积沿用基准单位，未经校准不能直接写成kg/m³。',
 '配送出仓重量含货物与箱体；回库重量仅为空箱。组内按全部实际趟次合并平均；有配送任务的趟次计入配送均值，有回收任务的趟次计入回库均值。','',
 '| 组别 | 3种子一级趟次合计 | 配送平均箱数/趟 | 配送出仓重量装载率 | 配送出仓体积装载率 | 空箱回库重量装载率 | 空箱回库体积装载率 |',
 '|---|---:|---:|---:|---:|---:|---:|']
for group,a in aggregates.items():
 lines.append(f"| {group} | {a['trips']} | {a['mean_boxes_per_delivery_trip']:.2f} | {a['departure_weight_util']:.2%} | {a['departure_volume_util']:.2%} | {a['return_weight_util']:.2%} | {a['return_volume_util']:.2%} |")
lines+=['','逐趟数据见trips.csv，逐种子与距离加权结果见summary.json。',
 '距离加权装载率=Σ(弧长×该弧实际载荷)/(车辆容量×Σ弧长)，计入空载路段，不计停车等待时间。',
 '装载率统计依据可行初解启动的M8结果；冷启动M8无可行解，不能计算其有效装载率。','']
(ROOT/'load_analysis/report.md').write_text('\n'.join(lines))
print(json.dumps(out,indent=2))
