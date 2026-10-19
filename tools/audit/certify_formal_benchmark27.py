"""Build and independently replay feasibility witnesses; no optimization claim.

Fixed-model witnesses use a deterministic appointment construction. The M1/M3
reference assignments are admissible choices in M2/M4. The M3 one-batch schedule
is lifted and independently audited against each M6-M8 decision contract.
These witnesses are never automatically passed to benchmark solvers.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
from importlib import import_module
import json
from pathlib import Path
import random
import zipfile

from tools.configuration.loader import load_routing_case, resolve_project_path
from models.common.cycle import load_instance
from models.common.cycle.audit import audit_solution
from models.common.cycle.joint import lift_result
from models.common.cycle.uniform import attach_uniform_decisions
from models.common.cycle.calendar import attach_calendar_decisions


def read(path):
    return json.loads(Path(path).read_text())


def fixed_witness(problem, model):
    construction = import_module(f'models.routing.model_{model}.alns.construction')
    evaluator = import_module(f'models.routing.model_{model}.alns.evaluator')
    _, result, violations = evaluator.evaluate(problem, construction.appointment_state(problem,random.Random(42)))
    audited = audit_solution(problem,result)
    if violations or not audited['valid']:
        raise ValueError(f'Constructive witness failed: {violations}; {audited["errors"]}')
    keep = ('schema_version','policy_version','time_unit','model','packaging','time_tick_seconds',
            'time_tick_subdivision','objective_mode','operating_policy','batches','e1_trips','e2_trips')
    result = {key:result[key] for key in keep if key in result}
    result.update(solver='feasibility_witness',validation_only=True,
        info=dict(status='FEASIBLE',objective=audited['objective'],proven_optimal=False,
                  proven_infeasible=False,proof_scope='Existence of an audited feasible schedule only'))
    return result


def load_problem(base, model):
    config, fleet = load_routing_case(base['models'][str(model)])
    return load_instance(config,fleet)


def verify_witnesses(root):
    root = resolve_project_path(root)
    manifest=read(root/'manifest.json')
    report=read(root/'validation/feasibility.json')
    archive=root/'validation/witnesses.zip'
    if hashlib.sha256(archive.read_bytes()).hexdigest()!=report['archive_sha256']:
        raise ValueError('Feasibility archive hash mismatch')
    rows=[]
    with zipfile.ZipFile(archive) as z:
        expected={f'{b["id"]}/model_{m}.json' for b in manifest['base_cases'] for m in range(1,9)}
        if set(z.namelist())!=expected or len(z.namelist())!=216:
            raise ValueError('Witnesses must cover all 216 model/base pairs exactly once')
        for b in manifest['base_cases']:
            for m in range(1,9):
                name=f'{b["id"]}/model_{m}.json';payload=z.read(name)
                if hashlib.sha256(payload).hexdigest()!=report['witness_sha256'][name]:
                    raise ValueError(f'Witness bytes changed: {name}')
                audited=audit_solution(load_problem(b,m),json.loads(payload))
                if not audited['valid']:
                    raise ValueError(f'Witness failed independent replay: {name}: {audited["errors"]}')
                rows.append(dict(base_case_id=b['id'],model=m,valid=True))
    return dict(valid=True,audited=216,scope='Feasibility only; no optimality or search-success claim',cases=rows)


def certify(root):
    root=resolve_project_path(root)
    out=root/'validation'
    if out.exists():
        raise ValueError('Refusing to overwrite existing validation evidence')
    manifest=read(root/'manifest.json')
    witnesses,rows={},[]
    for base in manifest['base_cases']:
        fixed={m:fixed_witness(load_problem(base,m),m) for m in (1,3,5)}
        for m in range(1,9):
            problem=load_problem(base,m)
            if m in fixed:
                result=fixed[m]
            elif m in (2,4):
                result=deepcopy(fixed[m-1]);result['model']=m
                result['assignment']={o.customer:o.satellite for o in problem.orders}
            else:
                child=problem.for_count(1) if m!=8 else problem.fixed_calendar(1)
                result=lift_result(child,fixed[3],{o.id:'b1' for o in child.orders},problem.total,
                    'feasibility_witness',{'b1':fixed[3]['batches'][0]['cutoff']})
                if m==6:
                    attach_uniform_decisions(problem,result,1)
                else:
                    attach_calendar_decisions(problem,result,1,problem.balanced_boundaries(1))
                result.pop('audit',None)
            audited=audit_solution(problem,result)
            if not audited['valid']:
                raise ValueError(f'{base["id"]}/M{m}: {audited["errors"]}')
            name=f'{base["id"]}/model_{m}.json'
            witnesses[name]=(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+'\n').encode()
            rows.append(dict(base_case_id=base['id'],model=m,valid=True,
                distance=sum(audited['distances'].values()),
                minimum_pool_for_schedule=audited.get('minimum_pool_for_schedule'),
                total_container_uses=problem.total))
    out.mkdir()
    archive=out/'witnesses.zip'
    with zipfile.ZipFile(archive,'x',compression=zipfile.ZIP_DEFLATED) as z:
        for name,payload in witnesses.items():
            z.writestr(name,payload)
    code_root=Path(__file__).resolve().parents[2]
    sources={p.relative_to(code_root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
             for p in (code_root/'models').rglob('*.py')}
    report=dict(valid=True,base_case_count=27,model_config_count=216,
        source_input_manifest_sha256=hashlib.sha256((root/'manifest.json').read_bytes()).hexdigest(),
        archive_sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
        witness_sha256={name:hashlib.sha256(payload).hexdigest() for name,payload in witnesses.items()},
        models_source_sha256=sources,
        method='Deterministic appointment construction for M1/M3/M5; audited admissible projections for other models',
        solver_optimization_calls=0,not_automatically_used_as_solver_start=True,
        proof_scope='216 feasible physical schedules; no minimum-cost, minimum-pool or algorithm-success claim',
        cases=rows)
    (out/'feasibility.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    (out/'README.md').write_text('# 可行性见证\n\n'
        'witnesses.zip 含全部216个模型配置的显式调度。各份均经独立重放，检查车辆、时间窗、箱量守恒、清洗、末期归库及重复周期。'
        '这证明输入可行，不证明最优，也不表示ALNS已找到这些解。M6–M8采用可行的K=1、N=Q见证，不能用于证明循环收益。'
        '主实验不会自动读取这些解作为初始解。\n\n'
        '复核：`.venv/bin/python -m tools.audit.certify_formal_benchmark27 --verify`。\n',encoding='utf-8')
    manifest['artifact_sha256'].update({p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
        for p in out.iterdir() if p.is_file()})
    manifest['feasibility_certification']='validation/feasibility.json'
    (root/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    return verify_witnesses(root)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset',default='data/formal_benchmark27_one_box_v1')
    parser.add_argument('--verify',action='store_true')
    args=parser.parse_args()
    result=verify_witnesses(args.dataset) if args.verify else certify(args.dataset)
    print(json.dumps({k:v for k,v in result.items() if k!='cases'}))
