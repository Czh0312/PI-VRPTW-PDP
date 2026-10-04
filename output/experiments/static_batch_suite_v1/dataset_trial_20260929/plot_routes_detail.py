"""Readable schematic: E1 facilities only; E2 zooms into the service area."""
from pathlib import Path
import sys,json,csv
ROOT=Path(__file__).resolve().parents[4];sys.path.insert(0,str(ROOT))
from configuration.loader import load_routing_case
from models.routing.variants.model_3.multibatch import load_instance
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
OUT=Path(__file__).resolve().parent
record=next(r for r in json.loads((OUT/'summary.json').read_text()) if r['family_id']=='lade_0829_30aoi')
result=json.loads((OUT/record['valid_min_pool_result']).read_text())
with (OUT/'all_runs.csv').open(encoding='utf-8-sig') as h:
 row=next(r for r in csv.DictReader(h) if r['result_path']==record['valid_min_pool_result'])
cfg,fleet=load_routing_case(row['case_path']);problem=load_instance(cfg,fleet)
customers=sorted({o.customer for o in problem.orders});short={c:str(i+1) for i,c in enumerate(customers)}
with (OUT/'lade30_node_labels.csv').open('w',encoding='utf-8-sig',newline='') as h:
 w=csv.writer(h);w.writerow(['figure_label','customer_id']);w.writerows((short[c],c) for c in customers)
fig,axes=plt.subplots(2,3,figsize=(14,9),constrained_layout=True)
for column,batch in enumerate(problem.operating_policy.batches):
 for r,layer in enumerate(('e1','e2')):
  ax=axes[r,column]
  keep=([problem.depot]+list(problem.satellites)) if layer=='e1' else list(problem.satellites)+customers
  nodes=[problem.network.nodes[n] for n in keep]
  for i,node in enumerate(nodes):
   facility=node.type!='c'
   ax.scatter(node.x,node.y,marker='s' if facility else 'o',s=55 if facility else 17,
              c='#223b53' if facility else '#c8d2dc',zorder=3)
   label=node.id if facility else short[node.id]
   ax.annotate(label,(node.x,node.y),xytext=(4,5 if i%2 else -9),textcoords='offset points',
               fontsize=9 if facility else 7,color='#142b41' if facility else '#657587',zorder=4)
  trips=[]
  for trip in result[layer+'_trips']:
   belongs=trip['batch_id']==batch.id if layer=='e1' else batch.start<=trip['departure']<batch.end
   if belongs: trips.append(trip)
  for i,trip in enumerate(trips):
   base=problem.depot if layer=='e1' else trip['satellite_id']
   seq=[base]+[e['site_id'] if layer=='e1' else problem.order_map[e['order_id']].customer for e in trip['events']]+[base]
   points=[problem.network.nodes[s] for s in seq];color=plt.get_cmap('tab10')(i%10)
   ax.plot([n.x for n in points],[n.y for n in points],linewidth=1.6,alpha=.85,color=color,label=f'v{trip["vehicle_id"]}'+(' / '+base if layer=='e2' else ''))
   for a,b in zip(points,points[1:]):
    if a.id!=b.id:
     dx,dy=b.x-a.x,b.y-a.y
     ax.annotate('',xy=(a.x+dx*.60,a.y+dy*.60),xytext=(a.x+dx*.42,a.y+dy*.42),
                 arrowprops=dict(arrowstyle='->',color=color,lw=1.0),zorder=2)
  xmin,xmax=min(n.x for n in nodes),max(n.x for n in nodes)
  ymin,ymax=min(n.y for n in nodes),max(n.y for n in nodes)
  dx,dy=max(1,xmax-xmin),max(1,ymax-ymin)
  ax.set_xlim(xmin-.1*dx,xmax+.13*dx);ax.set_ylim(ymin-.15*dy,ymax+.2*dy)
  ax.set_aspect('equal',adjustable='box');ax.grid(alpha=.1)
  ax.set_title(f'{layer.upper()} / {batch.id}'+(' (departure wave)' if layer=='e2' else ''))
  ax.set_xlabel('Projected x (km)');ax.set_ylabel('Projected y (km)')
  ax.spines['top'].set_visible(False);ax.spines['right'].set_visible(False)
fig.suptitle('LaDe 30 AOIs | N = 144 | 3 batches\nStraight lines show visit order; travel distances use the saved road matrix. Customer numbers: lade30_node_labels.csv',fontsize=12)
fig.savefig(OUT/'figures/lade30_routes.png',dpi=180);plt.close(fig)
p=OUT/'report.md';p.write_text(p.read_text().replace('figures/routes_1.png','figures/lade30_routes.png'))
print('Route figure saved')
