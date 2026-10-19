"""Audit the tiered-fleet proposal without editing inputs or running a routing optimizer."""
from copy import deepcopy
import hashlib
from importlib import import_module
import json
from pathlib import Path
import sys
import zipfile

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
sys.path.insert(0,str(ROOT))

from tools.configuration.loader import load_routing_case
from tools.audit.certify_formal_benchmark27 import fixed_witness
from models.common.cycle import load_instance
from models.common.cycle.audit import audit_solution
from models.common.cycle.joint import lift_result
from models.common.cycle.uniform import attach_uniform_decisions
from models.common.cycle.calendar import attach_calendar_decisions

DATA=ROOT/'data/formal_benchmark27_one_box_v1'
PROFILE=ROOT/'config/experiments/benchmark27_one_box_v1/fleet_by_size_draft.json'


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    design,manifest=read(PROFILE),read(DATA/'manifest.json')
    before=sha(DATA/'manifest.json')
    for relative,expected in manifest['artifact_sha256'].items():
        assert sha(DATA/relative)==expected,relative
    def problem(base,model):
        cfg,fleet=load_routing_case(ROOT/base['models'][str(model)],use_pool_config=False)
        return load_instance(cfg,{**fleet,**design['by_size'][base['size']]})
    rows,failures,payloads=[],[],{}
    maxima={size:dict(max_order_weight=0,max_site_weight=0,max_site_boxes=0,
        max_total_weight=0,min_total_weight=float('inf'),max_customers=0) for size in design['by_size']}
    with zipfile.ZipFile(DATA/'validation/witnesses.zip') as original:
        for base in manifest['base_cases']:
            fixed={}
            for model in (1,3,5):
                try:
                    fixed[model]=fixed_witness(problem(base,model),model)
                except ValueError as exc:
                    failures.append(dict(case=base['id'],model=model,stage='construction',error=str(exc)))
            for model in range(1,9):
                p=problem(base,model)
                if model==1:
                    summary=maxima[base['size']]
                    summary['max_order_weight']=max(summary['max_order_weight'],max(p.task(o.id,'d')[1] for o in p.orders))
                    weights=[sum(p.task(o.id,'d')[1] for o in p.orders if o.satellite==s) for s in p.satellites]
                    boxes=[sum(o.count for o in p.orders if o.satellite==s) for s in p.satellites]
                    total=sum(weights)
                    summary['max_site_weight']=max(summary['max_site_weight'],max(weights))
                    summary['max_site_boxes']=max(summary['max_site_boxes'],max(boxes))
                    summary['max_total_weight']=max(summary['max_total_weight'],total)
                    summary['min_total_weight']=min(summary['min_total_weight'],total)
                    summary['max_customers']=max(summary['max_customers'],len(p.orders))
                old=audit_solution(p,json.loads(original.read(f'{base["id"]}/model_{model}.json')))
                row=dict(case=base['id'],size=base['size'],model=model,
                    stored_schedule_valid=old['valid'],stored_schedule_errors=old['errors'])
                try:
                    if model in fixed:
                        result=fixed[model]
                    elif model in (2,4):
                        result=deepcopy(fixed[model-1]);result['model']=model
                        result['assignment']={o.customer:o.satellite for o in p.orders}
                    else:
                        child=p.for_count(1) if model!=8 else p.fixed_calendar(1)
                        result=lift_result(child,fixed[3],{o.id:'b1' for o in child.orders},p.total,
                            'feasibility_witness',{'b1':fixed[3]['batches'][0]['cutoff']})
                        if model==6:
                            attach_uniform_decisions(p,result,1)
                        else:
                            attach_calendar_decisions(p,result,1,p.balanced_boundaries(1))
                        result.pop('audit',None)
                    audit=audit_solution(p,result)
                    row.update(constructed_schedule_valid=audit['valid'],errors=audit['errors'],
                        periodic_valid=audit.get('periodic_replay',{}).get('valid'),
                        e1_used=audit.get('e1_vehicle_count'),e2_used=audit.get('e2_vehicle_count'),
                        e1_trips=audit.get('e1_trip_count'),e2_trips=audit.get('e2_trip_count'))
                    if audit['valid']:
                        payloads[f'{base["id"]}/model_{model}.json']=(json.dumps(result,ensure_ascii=False,indent=2)+'\n').encode()
                    else:
                        failures.append(dict(case=base['id'],model=model,stage='audit',errors=audit['errors']))
                except (KeyError,ValueError) as exc:
                    row.update(constructed_schedule_valid=False,errors=[str(exc)])
                    failures.append(dict(case=base['id'],model=model,stage='projection',error=str(exc)))
                rows.append(row)
    archive=HERE/'candidate_witnesses.zip'
    if archive.exists():
        raise ValueError('Refusing to overwrite audit evidence')
    with zipfile.ZipFile(archive,'x',compression=zipfile.ZIP_DEFLATED) as zipped:
        for name,payload in payloads.items():
            zipped.writestr(name,payload)
    assert sha(DATA/'manifest.json')==before
    for relative,expected in manifest['artifact_sha256'].items():
        assert sha(DATA/relative)==expected,relative
    report=dict(profile=str(PROFILE.relative_to(ROOT)),profile_sha256=sha(PROFILE),
        dataset_manifest_sha256=before,formal_inputs_unchanged=True,
        routing_optimization_calls=0,planned=216,
        stored_schedules_passed=sum(r['stored_schedule_valid'] for r in rows),
        constructed_schedules_passed=sum(r['constructed_schedule_valid'] for r in rows),
        failed=sum(not r['constructed_schedule_valid'] for r in rows),skipped=0,
        method='Deterministic physical schedule construction and independent two-cycle replay; no exact/ALNS routing search.',
        maxima=maxima,failures=failures,cases=rows,
        witness_archive_sha256=sha(archive),
        witness_sha256={name:hashlib.sha256(payload).hexdigest() for name,payload in payloads.items()},
        optimization_claims='None: no optimality, performance or pool-reduction claim.',
        paused_pipeline_status=read(ROOT/'output/inspections/one_box_validation_20261019_v1/pipeline_state.json')['status'])
    (HERE/'profile_audit.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k not in ('cases','witness_sha256')},ensure_ascii=False))


if __name__=='__main__':
    main()
