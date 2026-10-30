"""Direct cost-optimized M3/S01 matched to the M8 economic profile."""
from pathlib import Path
import sys, json, time, hashlib, math, argparse

OUT=Path(__file__).resolve().parent
ROOT=OUT.parents[2]
BASE=ROOT/'output/experiments/small7_m8_k1_4_exact1800_20261024_v1'
sys.path.insert(0,str(BASE/'source'))
from tools.configuration.loader import load_routing_case
from models.common.cycle import load_instance
from models.common.cycle.audit import audit_solution
from models.routing.service import solve_routing
from models.cost.comparison import evaluate_comparison_cost
from experiments.runs.run_model6_comparison import freeze_case
import gurobipy as gp

def read(path):return json.loads(Path(path).read_text(encoding='utf-8'))
def write(path,value):Path(path).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--seconds',type=float,default=300)
    parser.add_argument('--formulation',choices=['expanded','compact'],default='expanded')
    args=parser.parse_args();form=args.formulation
    frozen=read(BASE/'manifest.json')
    for group in ('source_hashes','input_hashes'):
        for name,digest in frozen[group].items():assert sha(BASE/name)==digest,name
    case=ROOT/'data/formal_benchmark27_spacetime_v4_1/model_3/small/S01/case.json'
    (OUT/'inputs').mkdir(exist_ok=True)
    absolute_case=read(case)
    for key in ('data','routing_config','economic_config','dataset_metadata','orders','operating_policy','parameter_contract'):
        absolute_case[key]=str(ROOT/absolute_case[key])
    bridge=OUT/'source_case_absolute_paths.json';write(bridge,absolute_case)
    cfg,source_evidence=freeze_case(str(bridge),OUT,'S01_m3')
    source_evidence[str(case)]=dict(sha256=sha(case),path_normalization_only=True)
    cfg.update(objective_mode='cost',solver='exact',exact_time=args.seconds,exact_strategy='milp',
               milp_options=dict(threads=1,formulation=form))
    contract_path=ROOT/cfg['parameter_contract'];contract=read(contract_path)
    contract.update(objective='cost',experiment_override=dict(reason='User request: same objective as M8',
        native_objective='distance',experimental_objective='cost',source_contract_sha256=sha(contract_path)))
    target=OUT/'inputs/S01_m3_parameters_cost.json';write(target,contract);cfg['parameter_contract']=str(target)
    target=OUT/'case.json';write(target,cfg)
    config,fleet=load_routing_case(target,use_pool_config=False)
    problem=load_instance(config,fleet)
    assert problem.model_number==3 and problem.cost_objective and not problem.free_assignment
    assert len(problem.orders)==7 and problem.operating_policy.container_pool_size==7
    m8_cfg,m8_fleet=load_routing_case(BASE/'cases/S01_m8_k1_4.json',use_pool_config=False)
    m8=load_instance(m8_cfg,m8_fleet)
    assert problem.cost_profile.hash==m8.cost_profile.hash
    for key in ('data','routing_config','economic_config'):
        assert sha(config[key])==sha(m8_cfg[key]),key
    m3_orders={o.id:o for o in problem.orders};m8_orders={o.id:o for o in m8.orders}
    assert set(m3_orders)==set(m8_orders)
    fields=('customer','satellite','count','goods_weight','delivery_window','pickup_window','service_time','min_use_time','release_time')
    for oid in m3_orders:
        for field in fields:assert getattr(m3_orders[oid],field)==getattr(m8_orders[oid],field),(oid,field)
    write(OUT/'manifest.json',dict(model=3,case='S01',objective_mode='cost',budget=args.seconds,
        threads=1,seed=42,formulation=form,mip_start=False,custom_callback=False,
        gurobi_version=list(gp.gurobi.version()),interpreter=sys.executable,
        source_snapshot=str(BASE/'source'),frozen_hashes_verified=True,source_evidence=source_evidence,
        cost_profile_hash=problem.cost_profile.hash,matched_to_M8=dict(network=True,fleet=True,economics=True,
        order_physical_fields=list(fields)),native_M3_decisions=dict(batch_count=1,pool_fixed=7,calendar_fixed=True),
        inputs={p.relative_to(OUT).as_posix():sha(p) for p in (OUT/'inputs').iterdir() if p.is_file()}))
    original=gp.Model.optimize
    optimize_calls=[]
    def observed(model,*a,**kw):
        assert not a and not kw and model.NumStart==0
        model.Params.OutputFlag=1
        model.Params.LogFile=(OUT.relative_to(ROOT)/(form+'_gurobi.log')).as_posix()
        optimize_calls.append(dict(variables=model.NumVars,binary=model.NumBinVars,
            integers=model.NumIntVars,linear_constraints=model.NumConstrs,indicator_constraints=model.NumGenConstrs))
        return original(model)
    gp.Model.optimize=observed
    begin=time.perf_counter()
    try:
        result=solve_routing(model=3,method='exact',problem=problem,exact_time=args.seconds,
            seed=42,exact_strategy='milp',milp_options=dict(threads=1,formulation=form))
    finally:gp.Model.optimize=original
    wall=time.perf_counter()-begin
    write(OUT/(form+'_result.json'),result)
    info=result['info'];summary=dict(info,solve_wall_seconds=wall,optimize_calls=optimize_calls)
    if info.get('objective') is not None:
        audit=audit_solution(problem,result)
        assert audit['valid'] and audit['periodic_replay']['valid'],audit
        cost=evaluate_comparison_cost(problem,result,config['economic_config'],audit=audit)
        assert cost['cost_is_optimized'] and cost['route_objective_mode']=='cost'
        assert math.isclose(cost['total'],info['objective'],rel_tol=1e-7,abs_tol=1e-6)
        write(OUT/(form+'_audit.json'),audit);write(OUT/(form+'_cost.json'),cost)
        summary.update(independent_audit_valid=True,periodic_replay_valid=True,
            objective=cost['total'],cost_components=cost['components'],distances=audit['distances'],
            pool=cost['container_pool_size'],e1_trips=audit['e1_trip_count'],e2_trips=audit['e2_trip_count'])
    write(OUT/(form+'_summary.json'),summary)
    write(OUT/(form+'_completion.json'),dict(artifacts={p.name:sha(p) for p in OUT.glob(form+'_*') if p.is_file()},
        full_unit_suite_run=False))
    print(json.dumps(summary,ensure_ascii=False,indent=2),flush=True)

if __name__=='__main__':main()
