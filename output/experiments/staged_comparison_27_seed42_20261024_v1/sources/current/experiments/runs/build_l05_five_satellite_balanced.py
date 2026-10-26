"""Build the user-confirmed balanced five-satellite, 25-box pilot.

No ALNS/exact search is invoked. Input checks and constructed route witnesses
are saved separately and must not be presented as optimized experiment results.
"""
from pathlib import Path
from copy import deepcopy
from collections import Counter
import csv
import hashlib
import json
import random

from experiments.runs.run_l05_dense_pilot import balanced_counts
from tools.configuration.loader import load_routing_case
from models.common.cycle import load_instance
from models.common.cycle.precheck import check_necessary_conditions
from tools.data.formal_benchmark27_bounds import productive_count_certificate

SOURCE = Path('data/formal_benchmark27_one_box_v1/shared/large/L05')
DATA = Path('data/mechanism_l05_5sat_balanced25_v1')
OUT = Path('output/l05_5sat_balanced25_design_20261020')


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')


def build():
    if DATA.exists() or OUT.exists():
        raise FileExistsError('Refusing to replace a previously generated design')
    DATA.mkdir()
    OUT.mkdir()
    for name in ('network.csv', 'economic.json'):
        (DATA/name).write_bytes((SOURCE/name).read_bytes())
    original = read(SOURCE/'reference_orders.json')
    original_by_id = {o['order_id']: o for o in original}
    sites = sorted({o['satellite_id'] for o in original})
    counts = Counter(o['satellite_id'] for o in original)
    quotas = balanced_counts([counts[s] for s in sites])
    fleet = read(SOURCE/'fleet.json')
    fleet.update(E1_Ck=25, E1_Qk=500)
    write(DATA/'fleet.json', fleet)
    policy = read(SOURCE/'reference_policy.json')
    policy['description'] = 'Balanced external appointment periods; M8 calendar remains a decision.'
    write(DATA/'reference_policy.json', policy)
    rng = random.Random(20261020)
    generated = []
    for site, quota in zip(sites, quotas):
        local = sorted((deepcopy(o) for o in original if o['satellite_id'] == site),
                       key=lambda o: o['customer_id'])
        rng.shuffle(local)
        cursor = 0
        for index, number in enumerate(quota):
            for o in local[cursor:cursor+number]:
                ready = 480+260*index+60+rng.randint(-10,10)
                o.update(delivery_ready=ready, delivery_due=ready+90,
                         pickup_ready=ready+80, pickup_due=ready+185,
                         release_time=480, delivery_batch_id=f'b{index+1}',
                         allowed_batch_ids=['b1','b2','b3'])
                generated.append(o)
            cursor += number
    generated.sort(key=lambda o:o['order_id'])
    write(DATA/'reference_orders.json', generated)
    for model in (3,8):
        label = f'M{model}'
        orders, adapted = deepcopy(generated), deepcopy(policy)
        if model == 3:
            adapted['batches'] = [dict(id='b1', delivery_duration=702, return_duration=78)]
            for o in orders:
                o.update(delivery_batch_id='b1', allowed_batch_ids=['b1'])
        write(DATA/label/'orders.json', orders)
        write(DATA/label/'policy.json', adapted)
        case = dict(schema_version=2, case_name=f'{DATA.name}_{label}', model=model,
                    solver='alns', data=str(DATA/'network.csv'), routing_config=str(DATA/'fleet.json'),
                    economic_config=str(DATA/'economic.json'), orders=str(DATA/label/'orders.json'),
                    operating_policy=str(DATA/label/'policy.json'), container_pool_size=100,
                    alns_time=60, alns_iter=1000000000, seed=42, save_plots=False,
                    base_case_id='L05_5sat_balanced25', dataset_version=DATA.name,
                    exact_backend='multibatch_indexed' if model == 3 else 'joint_batch_indexed')
        if model == 8:
            case['joint_batch_options'] = dict(schedule_mode='flexible_cycle',
                batch_count_candidates=list(range(1,101)), min_phase_minutes=1, pool_min=1, pool_max=100)
        write(DATA/label/'case.json', case)
    assert len(generated) == len({o['customer_id'] for o in generated}) == 100
    assert sum(o['container_count'] for o in generated) == 100
    for o in generated:
        for key in ('order_id','customer_id','satellite_id','container_count','goods_weight','service_time','min_use_time'):
            assert o[key] == original_by_id[o['order_id']][key]
    assert (DATA/'network.csv').read_bytes() == (SOURCE/'network.csv').read_bytes()
    a,b=read(DATA/'M3/orders.json'),read(DATA/'M8/orders.json')
    excluded={'delivery_batch_id','allowed_batch_ids'}
    assert [{k:v for k,v in o.items() if k not in excluded} for o in a] == [
        {k:v for k,v in o.items() if k not in excluded} for o in b]
    checks={}
    cfg,fc=load_routing_case(DATA/'M3/case.json',use_pool_config=False)
    m3=load_instance(cfg,fc)
    checks['M3']=check_necessary_conditions(m3)
    assert checks['M3']['passed'],checks['M3']['errors']
    cfg,fc=load_routing_case(DATA/'M8/case.json',use_pool_config=False)
    m8=load_instance(cfg,fc)
    certificate=productive_count_certificate(m8)
    assert certificate['candidates_by_model']['8']==list(range(1,101))
    write(DATA/'batch_count_certificate.json',certificate)
    child=m8.fixed_calendar(3)
    cutoffs={b.id:b.start+child.operating_policy.ticks(182) for b in child.operating_policy.batches}
    reference=child.project({o['order_id']:o['delivery_batch_id'] for o in generated},100,cutoffs)
    checks['M8_reference_K3_N100']=check_necessary_conditions(reference)
    assert checks['M8_reference_K3_N100']['passed'],checks['M8_reference_K3_N100']['errors']
    write(DATA/'input_checks.json',checks)
    design=dict(date='2026-10-20',source=str(SOURCE),user_confirmed_intent='Similar total daily demand across satellites AND similar demand across satellites within each period; recurring demand in all periods.',
        customer_count=100,order_count=100,Q=100,satellites=sites,orders_per_satellite=dict(counts),
        demand_stratum_counts=dict(zip(sites,quotas)),demand_strata=[35,35,30],
        assignment='Original five-satellite assignment preserved; no coordinates moved.',
        time_generation='Fixed seed 20261020; each 260-minute period has ready=start+60+integer U[-10,10]; delivery width90, pickup ready=delivery ready+80 and width105; release08:00.',
        E1=dict(count=5,volume=25,weight=500),E2=dict(count_per_satellite=5,total=25,volume=6,weight=180),
        selection_rule='25 box capacity is a round value above the maximum unsplit daily satellite demand22. Weight500 exceeds the maximum daily satellite delivery weight401. Selected before any routing optimization.',
        preserved=['Entire original five-site network and assignment','Goods weights and one-box demands','Service, use, cleaning and handling times','Cycle08:00-21:00','Economic coefficients'],
        changes_from_three_site_case=['Restore original five satellites and assignment','Rebuild balanced appointments using same declared generation rule','E1 volume60 to25 and weight3000 to500','E2 total24 to25 (+4.17%)'],
        comparison_scope='M3 and M8 share exactly the same physical inputs. Comparison against the previous three-site case changes several factors and cannot identify the independent effect of satellite count.',
        expected_daily_site_departure_volume_util={s:counts[s]/25 for s in sites},
        warnings=['Synthetic balanced mechanism scenario, not empirical calibration or a robustness study.',
                  'M8 actual batches remain decisions; external periods are not forced vehicle batches.',
                  'More satellites alone need not improve multi-batch economics; timing and routing determine consolidation.',
                  'Fixed vehicle, trip and work cost rates remain zero in the shared synthetic profile.'],
        source_sha256={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in SOURCE.iterdir() if p.is_file()},
        alns_search_executed_at_generation=False)
    write(DATA/'design.json',design)
    print(json.dumps({k:design[k] for k in ('orders_per_satellite','demand_stratum_counts','E1','E2')},ensure_ascii=False))


if __name__=='__main__':
    build()
