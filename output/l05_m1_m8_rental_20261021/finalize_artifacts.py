"""Plot and tabulate the completed paper-versus-rental scenario without changing solvers."""
from pathlib import Path
from collections import defaultdict
import csv,json,math
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
R=Path('output/l05_m1_m8_rental_20261021')
def read(name):return json.loads((R/name).read_text())
assert read('progress.json')['status']=='COMPLETE'
assert read('integrity.json')['passed']
comparison=read('best_comparison.json');rows=read('summary.json');m8=read('best_M8.json');m1=comparison['M1']
assert read('best_M8_independent_audit.json')['valid'] and read('best_M1_independent_audit.json')['valid']
fixed=min((r for r in rows if r['label']=='M8_fixed_K3' and r['valid']),key=lambda r:r['total_cost'],default=None)
entries=[dict(label='M1 paper',K=1,N=None,total=m1['total_cost'],parts=m1['components']),
         dict(label='Best M8 rental',K=comparison['K'],N=comparison['N'],total=comparison['M8_cost'],parts=comparison['M8_components'])]
if fixed:entries.append(dict(label='M8 fixed K=3',K=3,N=fixed['N'],total=fixed['total_cost'],parts=fixed['components']))
fig,(ax,bx)=plt.subplots(1,2,figsize=(12,4.8),layout='constrained')
for i,row in enumerate(entries):
 p=row['parts'];bottom=0
 for key,color,label in [('single_use_purchase','#8b9aae','Paper purchase'),('container_pool','#5979a4','Rental'),('e1_distance','#d89b54','E1 distance'),('e2_distance','#39899c','E2 distance')]:
  amount=p.get(key,0)
  if amount:ax.bar(i,amount,bottom=bottom,color=color,width=.65,label=label if label not in ax.get_legend_handles_labels()[1] else None);bottom+=amount
 extra=sum(v for k,v in p.items() if k not in ('single_use_purchase','container_pool','e1_distance','e2_distance'))
 if extra:ax.bar(i,extra,bottom=bottom,color='#b4b4b4',width=.65,label='Other recurring' if 'Other recurring' not in ax.get_legend_handles_labels()[1] else None)
 ax.text(i,row['total']+3,f"{row['total']:.2f}\nK={row['K']}"+(f", N={row['N']}" if row['N'] else ''),ha='center',fontsize=8)
ax.set_xticks(range(len(entries)));ax.set_xticklabels([r['label'] for r in entries]);ax.set(ylabel='Synthetic cost / complete operating cycle',title='Audited feasible schedules; paper=2/use, rental=1/box/cycle');ax.legend(fontsize=8)
horizons=[1,2,3,5,10,20,30]
for r in entries:
 bx.plot(horizons,[h*r['total'] for h in horizons],marker='o',label=r['label'])
bx.set(xlabel='Number of identical complete operating cycles',ylabel='Cumulative synthetic cost',title='No acquisition, deposit or setup cost');bx.legend(fontsize=8)
for panel in (ax,bx):panel.spines[['top','right']].set_visible(False);panel.grid(axis='y',alpha=.15);panel.set_axisbelow(True)
fig.savefig(R/'cost_and_cycles.png',dpi=180);plt.close(fig)
with (R/'cumulative_costs.csv').open('w',encoding='utf-8-sig',newline='') as f:
 writer=csv.DictWriter(f,fieldnames=list(comparison['timeline'][0]));writer.writeheader();writer.writerows(comparison['timeline'])
with (R/'paired_comparison.csv').open('w',encoding='utf-8-sig',newline='') as f:
 paired=read('paired_comparison.json');writer=csv.DictWriter(f,fieldnames=list(paired[0]));writer.writeheader();writer.writerows(paired)
metrics=dict(M1_cost=m1['total_cost'],M8_cost=comparison['M8_cost'],M8_K=comparison['K'],M8_N=comparison['N'],saving_per_cycle=comparison['saving_per_cycle'],cost_reduction=comparison['cost_reduction'],first_strictly_cheaper_cycle=comparison['first_strictly_cheaper_cycle'],M8_origin=read('best_origin.json'),groups=read('group_summary.json'))
parts1=m1['components'];parts8=comparison['M8_components']
metrics.update(paper_spending=parts1['single_use_purchase'],rental_spending=parts8['container_pool'],cleaning=parts8['cleaning'],wear=parts8['container_wear'],extra_transport=sum(parts8[k]-parts1[k] for k in ('e1_distance','e2_distance')))
if fixed:metrics.update(fixed_K3_cost=fixed['total_cost'],fixed_K3_N=fixed['N'],fixed_K3_saving_vs_paper=m1['total_cost']-fixed['total_cost'])
(R/'final_metrics.json').write_text(json.dumps(metrics,indent=2)+'\n')
lines=['','## 最终累计费用与成本分解','',
 '| 成本项 | 模型1纸箱 | 模型8租赁最好方案 |', '|---|---:|---:|',
 f"| 每期纸箱购买/箱池租金 | {parts1['single_use_purchase']:.4f} | {parts8['container_pool']:.4f} |",
 f"| 一级运输 | {parts1['e1_distance']:.4f} | {parts8['e1_distance']:.4f} |",
 f"| 二级运输 | {parts1['e2_distance']:.4f} | {parts8['e2_distance']:.4f} |",
 f"| 清洗 | 0 | {parts8['cleaning']:.4f} |",
 f"| 磨损 | 0 | {parts8['container_wear']:.4f} |",
 f"| 合计 | {m1['total_cost']:.4f} | {comparison['M8_cost']:.4f} |",'',
 '| 完整运营周期数 | 模型1累计 | 模型8累计 | 模型8累计节省 |','|---|---:|---:|---:|']
for row in comparison['timeline']:
 if row['cycle'] in (1,2,3,5,10,20,30):lines.append(f"| {row['cycle']} | {row['M1_total']:.4f} | {row['M8_total']:.4f} | {row['net_saving']:.4f} |")
if comparison['K']==1:
 lines+=['','本情景下模型8最好方案选择K=1，说明当前价格下其成本优势不能归因为多批次周转；固定三批结果作为单独机制对照保留。']
if fixed:lines += ['',f"固定三批对照成本{fixed['total_cost']:.6f}，N={fixed['N']}，相对模型1每期节省{m1['total_cost']-fixed['total_cost']:.6f}。"]
lines+=['','图见cost_and_cycles.png；逐期数字见cumulative_costs.csv；同种子配对见paired_comparison.csv。','']
with (R/'report.md').open('a') as f:f.write('\n'.join(lines))
print(json.dumps(metrics,indent=2))
