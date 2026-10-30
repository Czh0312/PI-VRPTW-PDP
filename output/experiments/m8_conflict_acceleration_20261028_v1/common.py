"""Frozen inputs and resilient experiment-only I/O; never use the new optimum as L."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import time

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
OLD=ROOT/'output/experiments/m8_stability_20261028_v1'
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(OLD))
sys.path.insert(0,str(HERE))

from models.common.cycle import load_instance
from models.common.cycle.audit import audit_solution
from tools.configuration.loader import load_routing_case


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def save(path,value):
    payload=json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)
    for attempt in range(10):
        try:
            Path(path).write_text(payload,encoding='utf-8')
            return
        except PermissionError:
            if attempt==9:
                raise
            time.sleep(min(.02*2**attempt,.5))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def module(path,name):
    spec=importlib.util.spec_from_file_location(name,path)
    value=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def load(case):
    folder=OLD/'inputs'/case
    context=read(folder/'context.json')
    p=load_instance(*load_routing_case(Path(context['case_path']),use_pool_config=False))
    witness=read(context['witness_path'])
    audit=audit_solution(p,witness)
    assert audit['valid'] and audit['periodic_replay']['valid']
    assert abs(audit['objective']-context['audited_upper_bound'])<=1e-6
    assert context['reduced_N'][0]==context['reduced_N'][1]
    for k in context['reduced_K']:
        child=p.for_count(k).operating_policy
        assert (child.time_tick_seconds,child.time_tick_subdivision)==(
            p.operating_policy.time_tick_seconds,p.operating_policy.time_tick_subdivision)
    original=read(folder/'trip_library.json')
    allowed=set().union(*[{row['mask'] for row in read(folder/f'filtered_library_K{k}.json')}
                          for k in context['reduced_K']])
    library=[row for row in original if row['mask'] in allowed]
    phase=module(ROOT/'output/experiments/m8_s04_reduction_validation_20261026_v1/phase_bound.py','phase_'+case)
    assert all(sha(path)==digest for path,digest in context['input_hashes'].items())
    return p,witness,context,library,phase


def project(problem,result):
    audit=audit_solution(problem,result)
    assert audit['valid'] and audit['periodic_replay']['valid']
    k=result['cycle_batch_count']
    batches=[b.id for b in problem.for_count(k).operating_policy.batches]
    index={o.id:i for i,o in enumerate(problem.orders)}
    trip_recovery={}
    for row in audit['cutoff_snapshots']:
        for trip in row['eligible_events']:
            trip_recovery[trip]=min(trip_recovery.get(trip,k),batches.index(row['batch_id']))
    masks=[]
    recovery={}
    for trip in result['e2_trips']:
        masks.append(sum(1<<(index[event['order_id']]+(len(index) if event['kind']=='p' else 0))
                         for event in trip['events']))
        for event in trip['events']:
            if event['kind']=='p':
                recovery[event['order_id']]=trip_recovery[trip['trip_id']]
    return dict(K=k,masks=sorted(masks),delivery_batches={oid:batches.index(bid)
        for oid,bid in result['order_batch_assignment'].items()},recovery_batches=recovery)
