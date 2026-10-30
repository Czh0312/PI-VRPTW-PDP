"""Frozen matched M8 experiment; no heuristic starts or changes to time/K domains."""
import argparse
from copy import deepcopy
import csv
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[2]
PREVIOUS = PROJECT/'output/experiments/m8_small7_exact_alns_gap_20261024_v1'
OPTIONS = dict(baseline=(False,False), domains=(True,False), domains_symmetry=(True,True))
PLAN = [(c,p) for c in ('S01','S03') for p in OPTIONS]+[('S02','domains_symmetry')]


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary = path.with_name(path.name+'.tmp')
    temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    temporary.replace(path)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


def imports():
    sys.path.insert(0,str(HERE/'source'))
    from tools.configuration.loader import load_routing_case
    from models.common.cycle import load_instance
    return load_routing_case,load_instance


def validate(manifest):
    assert sha(Path(__file__)) == manifest['driver_sha256']
    assert sha(HERE/'manifest.json') == read(HERE/'manifest_sha256.json')['sha256']
    for path,digest in manifest['files'].items():
        assert sha(HERE/path) == digest,path


def prepare():
    if (HERE/'manifest.json').exists():
        raise RuntimeError('Already prepared')
    files = list((PROJECT/'models').rglob('*.py'))+list((PROJECT/'tools/configuration').rglob('*.py'))
    files += [PROJECT/p for p in ('tools/__init__.py','experiments/__init__.py',
              'experiments/runs/__init__.py','experiments/runs/probe_model8_physical_bounds.py')]
    for source in files:
        target = HERE/'source'/source.relative_to(PROJECT)
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes(source.read_bytes())
    loader,load = imports()
    from models.routing.model_8.exact.preprocessing import temporal_assignment_bounds
    from models.common.cycle.audit import audit_solution
    from models.cost.comparison import evaluate_comparison_cost
    checks,diagnostics = [],{}
    for number in range(1,10):
        case = f'S{number:02d}'
        config = read(PREVIOUS/'cases'/f'{case}.json')
        for key in ('data','orders','routing_config','operating_policy','economic_config',
                    'dataset_metadata','parameter_contract','batch_count_certificate','distance_matrix','duration_matrix'):
            if not isinstance(config.get(key),str):
                continue
            source = Path(config[key])
            target = HERE/'inputs'/case/(key+source.suffix)
            target.parent.mkdir(parents=True,exist_ok=True)
            target.write_bytes(source.read_bytes())
            config[key] = str(target)
        write(HERE/'cases'/f'{case}.json',config)
        p = load(*loader(HERE/'cases'/f'{case}.json',use_pool_config=False))
        assert p.model_number == 8 and len(p.orders) == 7 and list(p.batch_count_candidates) == list(range(1,8))
        diagnostics[case] = {}
        for k in p.batch_count_candidates:
            b = temporal_assignment_bounds(p.for_count(k))
            diagnostics[case][k] = dict(domains=b['domains'],certificate=b['certificate'],
                                       pool_lower_bound=b['pool_lower_bound'],removed=len(b['removed']))
        old = PROJECT/f'output/experiments/small7_m8_alns300_20261024_v1/runs/{case}_m8_alns_300s_s42/attempt_001/result.json'
        result = read(old)
        audit = audit_solution(p,result)
        assert audit['valid'] and audit['periodic_replay']['valid'],case
        child = p.for_count(result['cycle_batch_count'])
        b = temporal_assignment_bounds(child)
        assignment = result['order_batch_assignment']
        position = {batch.id:i for i,batch in enumerate(child.operating_policy.batches)}
        quantity = {o.id:o.count for o in child.orders}
        assert b['certificate'] is None,case
        for order,batch in assignment.items():
            assert batch in b['domains'][order],(case,order,batch)
            available = sum(quantity[j] for j in b['eligible_predecessors'][order]
                            if position[assignment[j]] < position[batch])
            assert available >= position[batch],(case,order,batch,available)
        canonical = deepcopy(result)
        relabel = {}
        for site in p.satellites:
            local = [o for o in p.orders if o.satellite == site]
            key = {(o.id,kind):2*i+offset for i,o in enumerate(local)
                   for offset,kind in [(1,'d'),(2,'p')]}
            trips = [t for t in canonical['e2_trips'] if t['satellite_id'] == site]
            first = {}
            for trip in sorted(trips,key=lambda t:t['trip_index']):
                first.setdefault(trip['vehicle_id'],key[trip['events'][0]['order_id'],trip['events'][0]['kind']])
            mapping = {old:i for i,(old,_) in enumerate(sorted(first.items(),key=lambda pair:pair[1]))}
            for trip in trips:
                trip['vehicle_id'] = mapping[trip['vehicle_id']]
            relabel[site] = mapping
        canonical_audit = audit_solution(p,canonical)
        assert canonical_audit['valid'] and canonical_audit['periodic_replay']['valid'],case
        canonical_cost = evaluate_comparison_cost(p,canonical,p.cost_profile.config,audit=canonical_audit)
        assert math.isclose(canonical_cost['total'],audit['objective'],rel_tol=1e-8,abs_tol=1e-6)
        checks.append(dict(case=case,source=str(old),source_sha256=sha(old),
            original_audit=True,new_domains_and_return_covers_satisfied=True,
            canonical_vehicle_relabel_audit=True,cost_unchanged=True,mapping=relabel))
    write(HERE/'witness_checks.json',checks)
    write(HERE/'preprocessing_diagnostics.json',diagnostics)
    frozen = {p.relative_to(HERE).as_posix():sha(p) for directory in ('source','inputs','cases')
              for p in (HERE/directory).rglob('*') if p.is_file() and p.suffix != '.pyc'}
    manifest = dict(created_at=now(),driver_sha256=sha(Path(__file__)),files=frozen,plan=PLAN,
        seconds=300,seed=42,threads=1,formulation='compact',strengthening='physical_time',
        K=list(range(1,8)),options=OPTIONS,external_starts=False,custom_cuts=False,
        budget_scope='optimization_only; construction and offline audit separately reported',
        validation=dict(selected_test_files=['tests/routing/test_model8_temporal_preprocessing.py',
                    'tests/routing/test_model8_physical_bounds.py','tests/routing/test_unified_batch_milp.py',
                    'tests/routing/test_count_screening.py'],passed=62,failed=0,skipped=0),
        interpreter=sys.executable)
    write(HERE/'manifest.json',manifest)
    write(HERE/'manifest_sha256.json',dict(sha256=sha(HERE/'manifest.json')))
    summarize()
    print(json.dumps(dict(prepared=True,witnesses_checked=len(checks),calls=len(PLAN)),ensure_ascii=False),flush=True)


def job(case,profile):
    return HERE/'runs'/case/profile


def worker(case,profile):
    manifest = read(HERE/'manifest.json'); validate(manifest)
    loader,load = imports()
    from experiments.runs import probe_model8_physical_bounds as probe
    p = load(*loader(HERE/'cases'/f'{case}.json',use_pool_config=False))
    preprocess,symmetry = OPTIONS[profile]
    path = job(case,profile); path.mkdir(parents=True,exist_ok=True)
    args = SimpleNamespace(seconds=manifest['seconds'],seed=42,formulation='compact',
                           solution_limit=None,preprocessing=preprocess,vehicle_symmetry=symmetry)
    summary = probe.run(args,p,'physical_time',path)
    summary.update(case=case,profile=profile)
    write(path/'summary.json',summary)


def verify(case,profile):
    manifest = read(HERE/'manifest.json'); validate(manifest)
    loader,load = imports()
    from models.common.cycle.audit import audit_solution
    from models.cost.comparison import evaluate_comparison_cost
    p = load(*loader(HERE/'cases'/f'{case}.json',use_pool_config=False))
    path = job(case,profile); summary = read(path/'summary.json')
    summary['valid'] = False
    if summary['sol_count']:
        result = read(path/'physical_time_result.json')
        audit = audit_solution(p,result)
        assert audit['valid'] and audit['periodic_replay']['valid']
        cost = evaluate_comparison_cost(p,result,p.cost_profile.config,audit=audit)
        assert math.isclose(cost['total'],summary['objective'],rel_tol=1e-8,abs_tol=1e-6)
        write(path/'independent_audit.json',audit); write(path/'independent_cost.json',cost)
        summary['valid'] = True
    write(path/'summary.json',summary)
    write(path/'complete.json',dict(summary_sha256=sha(path/'summary.json'),verified_at=now()))


def summarize():
    rows = []
    for case,profile in PLAN:
        path = job(case,profile)
        if not (path/'complete.json').exists():
            continue
        check = read(path/'complete.json'); assert sha(path/'summary.json') == check['summary_sha256']
        s = read(path/'summary.json')
        row = {k:s.get(k) for k in ['case','profile','status','valid','proven_optimal','objective','best_bound',
                 'mip_gap','first_solver_incumbent_seconds','build_time','mip_runtime','mip_work','node_count',
                 'variables','constraints','selected_K','pool','built_batch_counts','screened_batch_counts']}
        row['screened_batch_counts'] = list(row['screened_batch_counts'] or {})
        rows.append(row)
    write(HERE/'comparison.json',rows)
    if rows:
        with (HERE/'comparison.csv').open('w',encoding='utf-8-sig',newline='') as h:
            writer = csv.DictWriter(h,fieldnames=list(rows[0])); writer.writeheader();writer.writerows(rows)
    write(HERE/'progress.json',dict(completed=len(rows),planned=len(PLAN),updated_at=now()))
    return rows


def run():
    manifest = read(HERE/'manifest.json'); validate(manifest)
    env = dict(os.environ,OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1',
               NUMEXPR_NUM_THREADS='1',PYTHONUTF8='1')
    for case,profile in PLAN:
        if (HERE/'STOP').exists():
            break
        path = job(case,profile)
        if (path/'complete.json').exists():
            continue
        path.mkdir(parents=True,exist_ok=True)
        print(json.dumps(dict(start=case,profile=profile,seconds=300,timestamp=now())),flush=True)
        write(HERE/'active.json',dict(case=case,profile=profile,status='RUNNING',started_at=now()))
        with (path/'stdout.log').open('w',encoding='utf-8') as log:
            process = subprocess.Popen([sys.executable,str(Path(__file__)),'worker','--case',case,'--profile',profile],
                cwd=PROJECT,env=env,stdout=log,stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            begun = time.perf_counter()
            while process.poll() is None:
                if (HERE/'STOP').exists() or time.perf_counter()-begun>900:
                    process.terminate();process.wait(timeout=10)
                    raise RuntimeError('Stopped or process exceeded independent wall cap; preserve logs for inspection')
                time.sleep(2)
        if process.returncode:
            raise RuntimeError(f'Worker failed: {case}/{profile}; inspect stdout.log')
        with (path/'verify.log').open('w',encoding='utf-8') as log:
            subprocess.run([sys.executable,str(Path(__file__)),'verify','--case',case,'--profile',profile],
                cwd=PROJECT,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=120,check=True)
        validate(manifest)
        rows = summarize()
        print(json.dumps(dict(complete=case,profile=profile,result=rows[-1]),ensure_ascii=False),flush=True)
    write(HERE/'active.json',dict(status='COMPLETE' if len(summarize())==len(PLAN) else 'STOPPED',updated_at=now()))


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('action',choices=['prepare','run','worker','verify'])
parser.add_argument('--case',choices=['S01','S02','S03'])
parser.add_argument('--profile',choices=list(OPTIONS))
if __name__ == '__main__':
    args = parser.parse_args()
    if args.action == 'prepare': prepare()
    elif args.action == 'run': run()
    elif args.action == 'worker': worker(args.case,args.profile)
    else: verify(args.case,args.profile)
