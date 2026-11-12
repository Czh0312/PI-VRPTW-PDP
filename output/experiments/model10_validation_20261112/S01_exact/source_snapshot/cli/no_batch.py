"""Model 10 runs and epsilon-constraint transport/container frontier."""
import argparse
import csv
from datetime import datetime
import hashlib
import json
from pathlib import Path
import shutil
import sys

from tools.configuration.loader import PROJECT_ROOT, load_json, load_routing_case, resolve_project_path, config_sources
from models.common.no_batch.instance import load_instance
from models.routing.service import solve_routing

DEFAULT_CASE = PROJECT_ROOT/'config/experiments/examples/model10_demo.json'


def write_json(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')


def run_case(config,fleet,*,output=None,case_path=None,pool_caps=None,initial_result=None):
    problem=load_instance(config,fleet)
    out=resolve_project_path(output) if output else PROJECT_ROOT/'output/single_runs/model10'/datetime.now().strftime('%Y%m%dT%H%M%S_%f')
    if out.exists() and any(out.iterdir()):raise ValueError(f'Refusing to overwrite nonempty output directory: {out}')
    out.mkdir(parents=True,exist_ok=True)
    source_roots=('models/common','models/routing/model_10','tools/configuration','cli')
    sources=sorted({p for folder in source_roots for p in (PROJECT_ROOT/folder).rglob('*.py')}|
                   {PROJECT_ROOT/'models/routing/catalog.py',PROJECT_ROOT/'models/routing/service.py',PROJECT_ROOT/'main.py'})
    inputs=[]
    for key in ('data','orders','routing_config','operating_policy','distance_matrix','duration_matrix','algorithm_config'):
        if isinstance(config.get(key),str):inputs.extend(config_sources(config[key]))
    if case_path:inputs.extend(config_sources(case_path))
    for source in sources:
        target=out/'source_snapshot'/source.relative_to(PROJECT_ROOT)
        target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target)
    for i,source in enumerate(sorted(set(inputs))):
        target=out/'inputs'/f'{i:02d}_{source.name}';target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target)
    write_json(out/'manifest.json',dict(model=10,config=config,fleet=fleet,python=sys.version,
               policy=problem.operating_policy.as_dict(),options=problem.options,
               source_sha256={str(p.relative_to(PROJECT_ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sources},
               input_sha256={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(set(inputs))},
               scope='Fixed assignment; one container/order; no batches; all orders known; unlimited cleaning/storage; distance transport cost'))
    if initial_result is not None:write_json(out/'initial_result.json',initial_result)
    caps=[problem.pool_max] if pool_caps is None else list(pool_caps)
    results=[];frontier=[];previous=initial_result
    for cap in caps:
        selected=problem.with_options(pool_max=cap)
        kwargs=dict(model=10,method=config.get('solver','alns'),problem=selected,
                    alns_time=config.get('alns_time',120),alns_iter=config.get('alns_iter',5000),seed=config.get('seed',42),
                    exact_time=config.get('exact_time',60))
        if kwargs['method']=='exact':
            kwargs.update(exact_backend=config.get('exact_backend','highs'),milp_options=config.get('milp_options'))
        elif previous is not None and previous.get('container_pool_size',problem.total)<=cap:
            kwargs['initial_result']=previous
        result=solve_routing(**kwargs);results.append(result)
        result_path=out/('result.json' if pool_caps is None else f'pool_{cap}.json')
        write_json(result_path,result)
        row=dict(pool_cap=cap,actual_pool=result.get('container_pool_size'),transport_cost=result.get('info',{}).get('transport_cost'),
                 status=result['info']['status'],proven_optimal=result['info']['proven_optimal'],
                 proven_infeasible=result['info']['proven_infeasible'],runtime=result['info']['runtime'])
        frontier.append(row)
        print(json.dumps(row,ensure_ascii=False))
        if result.get('feasible'):previous=result
        if result['info']['proven_infeasible'] and all(x<=cap for x in caps[len(results):]):break
    if pool_caps is not None:
        with (out/'frontier.csv').open('w',encoding='utf-8-sig',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=list(frontier[0]));writer.writeheader();writer.writerows(frontier)
        write_json(out/'frontier.json',dict(points=frontier,
                   interpretation='Limited-search feasible upper bounds unless proven_optimal. No solution found is not infeasibility.'))
    print(f'M10 output: {out}')
    return results[0] if pool_caps is None else results


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model',type=int,choices=(10,),default=10)
    parser.add_argument('--case',default=str(DEFAULT_CASE))
    parser.add_argument('--solver',choices=('exact','alns'))
    parser.add_argument('--exact-backend',choices=('highs','gurobi'))
    parser.add_argument('--exact-strategy',choices=('milp','auto'))
    parser.add_argument('--exact-time',type=float)
    parser.add_argument('--time',type=float)
    parser.add_argument('--iterations',type=int)
    parser.add_argument('--seed',type=int)
    parser.add_argument('--pool-max',type=int)
    parser.add_argument('--objective-mode',choices=('transport','min_pool'))
    parser.add_argument('--transport-budget',type=float)
    parser.add_argument('--e1-max-trips',type=int,help='Optional restriction; default safe total-demand bound')
    parser.add_argument('--milp-threads',type=int)
    parser.add_argument('--initial-result',help='Independently audited M10 JSON for ALNS')
    parser.add_argument('--frontier',action='store_true',help='Solve Q,Q-1,...,1; --time/--exact-time is PER point')
    parser.add_argument('--pool-caps',type=int,nargs='+',help='Explicit frontier caps; budgets are PER point')
    parser.add_argument('--output')
    args=parser.parse_args(argv)
    config,fleet=load_routing_case(args.case,use_pool_config=False)
    config={**config,'model':10}
    if config.get('exact_backend') not in ('highs','gurobi'):
        config['exact_backend']='highs'
    for arg,key in (('solver','solver'),('exact_backend','exact_backend'),('exact_time','exact_time'),
                    ('time','alns_time'),('iterations','alns_iter'),('seed','seed')):
        if getattr(args,arg) is not None:config[key]=getattr(args,arg)
    options=dict(config.get('no_batch_options',{}))
    for arg,key in (('pool_max','pool_max'),('objective_mode','objective'),('transport_budget','transport_budget'),('e1_max_trips','e1_max_trips_per_vehicle')):
        if getattr(args,arg) is not None:options[key]=getattr(args,arg)
    config['no_batch_options']=options
    if args.milp_threads is not None:config['milp_options']={'threads':args.milp_threads}
    if args.initial_result and config.get('solver')=='exact':raise ValueError('--initial-result is for ALNS')
    initial=json.loads(resolve_project_path(args.initial_result).read_text(encoding='utf-8')) if args.initial_result else None
    if args.frontier and args.pool_caps:raise ValueError('Choose --frontier or --pool-caps')
    if (args.frontier or args.pool_caps) and options.get('objective','transport')!='transport':
        raise ValueError('A transport frontier requires objective-mode transport')
    caps=args.pool_caps
    if args.frontier:caps=list(range(load_instance(config,fleet).total,0,-1))
    return run_case(config,fleet,case_path=args.case,output=args.output,pool_caps=caps,initial_result=initial)
