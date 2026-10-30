"""Independent publication checks and input appointment distribution plot."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

PROJECT=Path.cwd()
sys.path.insert(0,str(PROJECT))
from cli.route import load_config
from models.common.cycle import load_instance
from models.common.cycle.audit import audit_solution

ROOT=PROJECT/'data/scenarios/continuous_flow_100_v1'
SOURCE=PROJECT/'data/formal_benchmark27_interleaved_v1'
OUTPUT=Path(__file__).resolve().parent
def read(path):
    return json.loads(path.read_text(encoding='utf-8'))
def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

windows={'delivery_ready','delivery_due','pickup_ready','pickup_due'}
for model in (3,8):
    old=read(SOURCE/f'model_{model}/large/L05/orders.json')
    new=read(ROOT/f'model_{model}/orders.json')
    assert len(new)==len({r['customer_id'] for r in new})==100
    for before,after in zip(old,new):
        assert {k:v for k,v in before.items() if k not in windows}=={
            k:v for k,v in after.items() if k not in windows}
        assert after['release_time']==480 and after['container_count']==1
        assert after['pickup_ready']>=after['delivery_due']+after['service_time']+after['min_use_time']
    assert read(ROOT/f'model_{model}/policy.json')==read(SOURCE/f'model_{model}/large/L05/policy.json')
assert digest(ROOT/'shared/network.csv')==digest(SOURCE/'shared/large/L05/network.csv')
assert digest(ROOT/'shared/economic.json')==digest(SOURCE/'shared/large/L05/economic.json')
for filename,expected in read(ROOT/'validation/source_fingerprint.json')['files'].items():
    assert digest(PROJECT/filename)==expected,filename
scenario=read(ROOT/'shared/scenario.json')
assert digest(PROJECT/scenario['generator'])==scenario['generator_sha256']
records=[]
for case,witness in (
    ('model_3/case.json','model3_witness.json'),
    ('model_5/case.json','model5_witness.json'),
    ('model_8/case.json','M8_witness.json'),
    ('model_8/case_pool80.json','pool80_witness.json'),
    ('model_8/case_fixed_pool100.json','fixed_pool100_witness.json')):
    config,fleet=load_config(ROOT/case,use_pool_config=False)
    problem=load_instance(config,fleet)
    result=read(ROOT/'validation'/witness)
    checked=audit_solution(problem,result)
    assert checked['valid'] and checked['periodic_replay']['valid'],checked['errors']
    records.append(dict(case=case,valid=True,two_cycle_replay=True,
        e1_trips=checked['e1_trip_count'],e2_trips=checked['e2_trip_count']))

summary=read(ROOT/'validation/summary.json')
hourly=summary['appointment_midpoints_by_hour']
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
hours=list(range(8,21))
figure,axes=plt.subplots(2,1,figsize=(13,7),constrained_layout=True,sharex=True)
for ax,kind,label in zip(axes,('delivery','pickup'),('Delivery','Pickup')):
    before=[hourly[f'old_{kind}'][str(hour)] for hour in hours]
    after=[hourly[f'new_{kind}'][str(hour)] for hour in hours]
    ax.bar([hour-.18 for hour in hours],before,width=.36,color='#9CA3AF',label='Original L05')
    ax.bar([hour+.18 for hour in hours],after,width=.36,color='#1976D2' if kind=='delivery' else '#E68624',label='New appointments')
    for hour,count in zip(hours,after):
        if count:
            ax.text(hour+.18,count+.3,str(count),ha='center',fontsize=9)
    ax.set_ylabel('Customers')
    ax.set_title(f'{label} appointment midpoints | 100 customers')
    ax.set_ylim(0,24)
    ax.grid(axis='y',alpha=.2)
    ax.set_axisbelow(True)
    ax.legend(loc='upper right')
axes[-1].set_xticks(hours,[f'{hour:02d}:00' for hour in hours])
axes[-1].set_xlabel('Hour containing appointment midpoint (input windows, not optimized routes)')
figure.suptitle('Only customer time windows changed | all goods remain ready at 08:00',fontsize=13)
figure.savefig(ROOT/'appointment_distribution.png',dpi=150)
plt.close(figure)

figure,ax=plt.subplots(figsize=(13,4.5),constrained_layout=True)
delivered=summary['actual_events_by_hour']['delivery']
picked=summary['actual_events_by_hour']['pickup']
ax.bar(hours,[delivered.get(str(h),0) for h in hours],color='#1976D2',label='Delivery')
ax.bar(hours,[picked.get(str(h),0) for h in hours],bottom=[delivered.get(str(h),0) for h in hours],
       color='#E68624',label='Pickup')
ax.set_xticks(hours,[f'{h:02d}:00' for h in hours])
ax.set_ylabel('Service tasks')
ax.set_title('Constructed feasible schedule | 100 deliveries + 100 pickups | not ALNS results')
ax.grid(axis='y',alpha=.2)
ax.set_axisbelow(True)
ax.legend()
figure.savefig(ROOT/'feasible_task_distribution.png',dpi=150)
plt.close(figure)

verification=dict(configurations_checked=5,passed=5,failed=0,skipped=0,
    only_four_order_window_endpoints_changed_for_M3_M8=True,
    original_release_times_preserved=True,all_goods_ready_minutes=480,
    source_policies_preserved_for_M3_M8=True,network_and_cost_bytes_preserved=True,
    fingerprints_valid=True,records=records,gurobi_checked=False,optimization_run=False)
(OUTPUT/'independent_validation.json').write_text(json.dumps(verification,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
text=['# 新旧时间窗分布核查','',
    '新算例的模型3/8只修改顾客四个取送窗口端点。所有100单货物仍在08:00就绪，'
    '一客一箱、货物重量、固定配送站、服务时间、最低使用时间、原参考日历、车辆和费用均保持一致。','',
    '![预约时间分布](appointment_distribution.png)','',
    '上图按输入预约窗口的中点统计，不是ALNS运行结果。配送预约中点在08—17时每小时6—14单；'
    '构造见证的实际配送任务每小时6—13单，取送在日内交错。回收受先送后取约束，分布会整体后移。','',
    '![可行见证的实际任务](feasible_task_distribution.png)','',
    '5个配置独立检查全部通过（5通过、0失败、0跳过），包括连续两个运营周期的物理重放。'
    '未运行ALNS或Gurobi，不能据此宣称多批费用更低。',
    '验证用4批安排与单批安排有相同的二级路线和服务时间，但一级运输分别20趟和5趟；'
    '因此这里只验证时间窗均匀及可行性，不将额外一级运输隐藏为正常业务需要。','',
    '模型8完整允许K=1～100，并没有固定为验证见证中的4批。','']
(ROOT/'DISTRIBUTION.md').write_text('\n'.join(text)+'\n',encoding='utf-8')
print(json.dumps(verification,ensure_ascii=False,indent=2))
