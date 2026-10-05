"""Reconstruct saved solutions with a new snapshot of shared common code."""
from pathlib import Path
import gzip
import json
import shutil
import sys
from experiment import OUT,sha,write,now
ROOT=OUT.parents[2]

def freeze():
    target=OUT/'latest_source';target.mkdir()
    paths=[p for base in ('models','configuration','config') for p in sorted((ROOT/base).rglob('*'))
           if p.is_file() and p.suffix in ('.py','.json','.md') and '__pycache__' not in p.parts]
    hashes={str(p.relative_to(ROOT)):sha(p) for p in paths}
    for path in paths:
        dest=target/path.relative_to(ROOT);dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,dest)
    checks={r:sha(ROOT/r)==h==sha(target/r) for r,h in hashes.items()}
    write(OUT/'latest_source_manifest.json',hashes)
    write(OUT/'latest_source_freeze.json',dict(time=now(),passed=all(checks.values()),checks=checks))
    assert all(checks.values())

def run():
    source=OUT/'latest_source'
    for rel,digest in json.loads((OUT/'latest_source_manifest.json').read_text()).items():
        assert sha(source/rel)==digest
    sys.path.insert(0,str(source))
    from configuration.loader import load_routing_case
    from models.routing.common.cycle import load_instance
    from models.routing.common.cycle.assignment import assigned_problem,lift_result
    from models.routing.common.cycle.audit import audit_solution
    from models.routing.solvers.alns.evaluator import evaluate
    from models.routing.solvers.alns.neighborhoods import state_from_result
    assert Path(evaluate.__code__.co_filename).resolve().is_relative_to(source)
    target=OUT/'latest_fresh';target.mkdir();rows=[]
    for phase in ('verification','unaffected','pilot'):
        for path in sorted((OUT/phase).glob('[0-9]*.json')):
            raw=json.loads(path.read_text());spec=raw['job']
            config,fleet=load_routing_case(spec['case']);base=load_instance({**config,'model':spec['model']},fleet)
            problem=assigned_problem(base,raw['result']['assignment']) if base.free_assignment else base
            _,result,issues=evaluate(problem,state_from_result(problem,raw['result']))
            fixed=audit_solution(problem,result)
            if base.free_assignment:result=lift_result(base,result,raw['result']['assignment'])
            audit=audit_solution(base,result)
            valid=not issues and fixed['valid'] and fixed['periodic_replay']['valid'] and audit['valid'] and audit['periodic_replay']['valid']
            equal=abs(result['info']['objective']-raw['result']['info']['objective'])<=1e-8
            row=dict(phase=phase,name=path.name,valid=valid,equal=equal,issues=issues)
            with gzip.open(target/(phase+'_'+path.name+'.gz'),'wt') as handle:
                json.dump(dict(check=row,result=result,fixed_audit=fixed,audit=audit),handle)
            rows.append(row)
    write(OUT/'latest_fresh_audit.json',dict(time=now(),scope='Saved-solution feasibility reconstruction only, not a new solve/quality/integration trial.',passed=len(rows)==44 and all(r['valid'] and r['equal'] for r in rows),checks=rows))
    assert len(rows)==44 and all(r['valid'] and r['equal'] for r in rows)
    print('Latest shared context:44/44 fresh audited/objective-equal')

if __name__=='__main__':
    {'freeze':freeze,'run':run}[sys.argv[1]]()
