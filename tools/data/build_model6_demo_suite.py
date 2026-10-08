"""Deterministic small M5/M6 mechanism suite; all prices are synthetic units."""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from tools.configuration.loader import PROJECT_ROOT


def synthetic_profile(pool_rate=5):
    return dict(cost_basis='disjoint_distance_time_fixed',currency='synthetic_units',
                source_type='synthetic_mechanism',description='Artificial mechanism coefficients; not empirical business prices.',
                price_basis='Artificial unit prices, no claim of market calibration',evaluation_period='one 130-minute cycle',
                pool_cost_basis='synthetic_pool_charge',work_time_basis='occupied_route_minutes',
                distance_scale_to_km=1,container_pool_cost_per_period=pool_rate,
                e1_distance_cost_per_km=1,e2_distance_cost_per_km=1,
                e1_work_cost_per_minute=.1,e2_work_cost_per_minute=.1,
                e1_vehicle_cost_per_cycle=10,e2_vehicle_cost_per_cycle=5,
                e1_trip_cost=1,e2_trip_cost=1,cleaning_cost_per_return=.2,
                tracking_cost_per_use=.1,container_price=10,container_lifetime_uses=50)


def _write(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')


def build_suite(destination=None):
    root=Path(destination) if destination else PROJECT_ROOT/'data/model6/mechanism_v1'
    if root.exists() and any(root.iterdir()):
        raise ValueError(f'Refusing to overwrite nonempty suite directory: {root}')
    root.mkdir(parents=True,exist_ok=True)
    cases=[]
    for name,pool_rate,multiple,late in [
        ('consolidation_low_pool',5,False,False),
        ('reuse_high_pool',30,False,False),
        ('late_return_inactive_delivery',5,False,True),
        ('two_satellites',5,True,False),
    ]:
        folder=root/name; folder.mkdir(exist_ok=True)
        nodes=[('D','d',0,0,''),('S','s',5,0,''),('C1','c',6,0,'S')]
        if not late:
            nodes.append(('C2','c',7,0,'S'))
        if multiple:
            nodes.extend([('T','s',0,5,''),('C3','c',0,6,'T'),('C4','c',0,7,'T')])
        fields=['node_id','node_type','x','y','delivery_demand','service_time',
                'delivery_ready','delivery_due','pickup_ready','pickup_due','assigned_sat']
        with (folder/'network.csv').open('w',newline='',encoding='utf-8') as handle:
            writer=csv.DictWriter(handle,fieldnames=fields); writer.writeheader()
            for nid,kind,x,y,site in nodes:
                writer.writerow(dict(node_id=nid,node_type=kind,x=x,y=y,delivery_demand=0,service_time=0,
                                     delivery_ready=0,delivery_due=130,pickup_ready=0,pickup_due=130,assigned_sat=site))
        orders=[]
        for index,(nid,kind,x,y,site) in enumerate(n for n in nodes if n[1]=='c'):
            orders.append(dict(order_id='o'+nid,customer_id=nid,satellite_id=site,
                               delivery_batch_id='b1' if index%2==0 else 'b2',container_count=1,goods_weight=1,
                               delivery_ready=0,delivery_due=20 if late else 90,
                               pickup_ready=65 if late else 0,pickup_due=70 if late else 95,
                               service_time=0,min_use_time=2,release_time=0,allowed_batch_ids=['b1','b2']))
        fleet=dict(E1_vehicle_count=1,vehicles_per_satellite=1,E1_Qk=100,E1_Ck=100,E2_Qv=100,E2_Cv=100,
                   container_weight=1,container_volume=1,speed=1)
        policy=dict(policy_version='multi_batch_cutoff_v1',source_type='synthetic_mechanism',
                    description='Fixed candidate slots retained even without delivery.',
                    cycle_start=0,cycle_end=130,
                    batches=[dict(id='b1',delivery_duration=40,return_duration=20),
                             dict(id='b2',delivery_duration=40,return_duration=20)],
                    container_pool_size=len(orders),cleaning_duration=5,e2_max_trips_per_vehicle=4,time_tick_seconds=60)
        for file,value in [('orders.json',orders),('fleet.json',fleet),('policy.json',policy),
                           ('economic.json',synthetic_profile(pool_rate))]:
            _write(folder/file,value)
        common=dict(schema_version=2,solver='alns',data=str(folder/'network.csv'),
                    orders=str(folder/'orders.json'),routing_config=str(folder/'fleet.json'),
                    operating_policy=str(folder/'policy.json'),container_pool_size=len(orders),
                    economic_config=str(folder/'economic.json'),exact_time=20,alns_time=1,
                    alns_iter=1000,seed=42,save_plots=False,
                    source_kind='synthetic_mechanism',description='Algorithm mechanism validation only, not CNY economics.')
        _write(folder/'m5_case.json',dict(common,model=5,case_name=name+'_M5',exact_backend='multibatch_indexed'))
        _write(folder/'m6_case.json',dict(common,model=6,case_name=name+'_M6',exact_backend='joint_batch_indexed',
                    joint_batch_options=dict(pool_min=1,pool_max=len(orders))))
        cases.append(dict(name=name,m5_case=str(folder/'m5_case.json'),m6_case=str(folder/'m6_case.json'),
                          orders=len(orders),currency='synthetic_units',source_kind='synthetic_mechanism'))
    manifest=dict(schema_version=1,suite='model6_mechanism_v1',cases=cases,
                  description='Fixed 130-minute horizon and windows; M5 full-demand pool baseline, M6 optimized pool. All costs artificial.')
    _write(root/'manifest.json',manifest)
    return root/'manifest.json'


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output')
    args=parser.parse_args(argv)
    print(build_suite(args.output))


if __name__=='__main__':
    main()
