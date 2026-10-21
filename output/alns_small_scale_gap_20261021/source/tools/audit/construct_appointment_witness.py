"""Capacity-sized deterministic appointment packing for feasibility evidence.

This is not used by routing solvers. It supplies explicit validation schedules
when the legacy two-order appointment seed does not fill the available trucks.
All proposals are decoded and independently audited by the caller.
"""
from importlib import import_module
import random


def packed_appointment_state(problem,model):
    construction=import_module(f'models.routing.model_{model}.alns.construction')
    state=construction.chronological_state(problem,random.Random(42));state['e2']=[]
    p,f=problem.operating_policy,problem.fleet
    supply={}
    for b in p.batches:
        for sites in state['deliveries'][b.id]:
            t=b.start+p.e1_load_time;last=problem.depot
            for s in sites:
                t+=problem.travel(last,s)+problem.service(s)
                supply[s,b.id]=t;last=s
    for site in problem.satellites:
        remaining={o.id:o for o in problem.orders if o.satellite==site}
        available=[p.cycle_start]*f.e2_per_sat_count;slots=[0]*f.e2_per_sat_count

        def simulate(orders,vehicle,reverse):
            if slots[vehicle]>=p.e2_max_trips_per_vehicle:return None
            if sum(o.goods_weight+o.count*f.container_weight for o in orders)>f.e2_weight_cap+1e-8:return None
            if sum(o.count*f.container_volume for o in orders)>f.e2_volume_cap+1e-8:return None
            t=max(available[vehicle]+p.e2_dispatch_prep_time,
                  supply[site,orders[0].batch]+p.e2_dispatch_prep_time,
                  orders[0].delivery_window[0]-problem.travel(site,orders[0].customer))
            departure=t;last=site;delivered={};distance=0.
            for o in orders:
                t=max(t+problem.travel(last,o.customer),o.delivery_window[0])
                if t>o.delivery_window[1]:return None
                if len(p.batches)==1 and t+o.service_time>p.batches[0].cutoff:return None
                delivered[o.id]=t;distance+=problem.distance(last,o.customer)
                t+=o.service_time;last=o.customer
            picked=list(reversed(orders)) if reverse else orders
            for o in picked if problem.reusable else ():
                t=max(t+problem.travel(last,o.customer),o.pickup_window[0],
                      delivered[o.id]+o.service_time+o.min_use_time)
                if t>o.pickup_window[1]:return None
                distance+=problem.distance(last,o.customer);t+=o.service_time;last=o.customer
            distance+=problem.distance(last,site)
            finish=t+problem.travel(last,site)+p.e2_return_unload_time
            if finish+p.e2_turnaround_time>p.cycle_end:return None
            if problem.reusable and finish>problem.batch_map[orders[0].batch].cutoff:return None
            return departure,finish,distance,picked

        def best_simulation(orders,vehicle):
            choices=[r for reverse in (False,True) if (r:=simulate(orders,vehicle,reverse)) is not None]
            return min(choices,key=lambda r:(r[1],r[2])) if choices else None

        while remaining:
            first=min(remaining.values(),key=lambda o:(o.delivery_window[0],o.delivery_window[1],o.id))
            choices=[]
            for vehicle in range(f.e2_per_sat_count):
                orders=[first];simulation=best_simulation(orders,vehicle)
                if simulation is None:continue
                while True:
                    trial=[];present={o.id for o in orders}
                    for o in remaining.values():
                        if o.id in present or o.batch!=first.batch:continue
                        if o.delivery_window[0]>min(x.delivery_window[1] for x in orders):continue
                        for position in range(len(orders)+1):
                            candidate=orders[:position]+[o]+orders[position:]
                            checked=best_simulation(candidate,vehicle)
                            if checked is not None:trial.append((checked[2],checked[1],o.id,position,candidate,checked))
                    if not trial:break
                    *_,orders,simulation=min(trial,key=lambda r:r[:4])
                choices.append((-len(orders),simulation[1],simulation[2],vehicle,orders,simulation))
            if not choices:raise ValueError(f'No capacity-sized constructive appointment route for {first.id}')
            _,_,_,vehicle,orders,simulation=min(choices,key=lambda r:r[:4])
            departure,finish,_,picked=simulation
            tasks=[(o.id,'d') for o in orders]+([(o.id,'p') for o in picked] if problem.reusable else [])
            state['e2'].append(dict(satellite=site,vehicle=vehicle,tasks=tasks,depart_after=departure))
            available[vehicle]=finish+p.e2_turnaround_time;slots[vehicle]+=1
            for order in orders:remaining.pop(order.id)
    return state
