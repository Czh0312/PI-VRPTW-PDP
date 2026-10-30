"""Count calendar candidates, inspect grid constraints, replay a minute witness.

No optimize() call. Original solver sources and frozen experiment inputs untouched.
"""
from pathlib import Path
import sys,json,math,copy,hashlib,time
OUT=Path(__file__).resolve().parent
ROOT=OUT.parents[2]
BASE=ROOT/'output/experiments/small7_m8_k1_4_exact1800_20261024_v1'
sys.path.insert(0,str(BASE/'source'))
from tools.configuration.loader import load_routing_case
from models.common.cycle import load_instance
from models.common.cycle.audit import audit_solution
from models.common.cycle.calendar import attach_calendar_decisions
from models.common.cycle.joint import lift_result
from models.common.cycle.state import empty_result
from models.common.cycle.unified_milp import build_unified_model
from models.routing.model_8.exact.formulation import build_model
import gurobipy as gp

def read(p):return json.loads(Path(p).read_text(encoding='utf-8'))
def write(name,obj):(OUT/name).write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding='utf-8')

def main():
    frozen=read(BASE/'manifest.json')
    for group in ('source_hashes','input_hashes'):
        for name,digest in frozen[group].items():
            assert hashlib.sha256((BASE/name).read_bytes()).hexdigest()==digest,name
    parent=load_instance(*load_routing_case(BASE/'cases/S01_m8_k1_4.json',use_pool_config=False))
    H=parent.cycle_duration;m=parent.minimum_phase_ticks
    assert parent.operating_policy.time_tick_seconds==1 and H==46800 and m==60
    rows=[]
    for k in parent.batch_count_candidates:
        assert parent.count_clock(k)[2]==1
        nseconds=math.comb(H-2*k*m+2*k-1,2*k-1)
        nminutes=math.comb(H//60-2*k*(m//60)+2*k-1,2*k-1)
        rows.append(dict(K=k,free_calendar_points=2*k-1,second_calendars=nseconds,
                         minute_calendars=nminutes,reduction_factor=nseconds/nminutes))
    write('calendar_counts.json',dict(cycle_seconds=H,minimum_phase_seconds=m,counts=rows,
        scope='Calendar constraints only; not counts of fully routing-feasible schedules',
        formula='C(H/g - 2*K*(m/g) + 2*K - 1, 2*K - 1)',
        seconds_total=sum(r['second_calendars'] for r in rows),minutes_total=sum(r['minute_calendars'] for r in rows)))
    sizes=[]
    for grid in (1,60):
        def builder(problem,**kwargs):
            model,ctx=build_model(problem,**kwargs)
            if grid==60:
                model.update()
                for group in ('batch_starts','cutoffs'):
                    for bid,var in ctx[group].items():
                        if not isinstance(var,gp.Var) or var.LB==var.UB:continue
                        minute=model.addVar(vtype=gp.GRB.INTEGER,lb=math.ceil(var.LB/60),
                            ub=math.floor(var.UB/60),name=f'minute_{group}_{bid}')
                        model.addConstr(var==60*minute,name=f'minute_grid_{group}_{bid}')
            return model,ctx
        start=time.perf_counter()
        model,ctx=build_unified_model(parent,build_fixed=builder,compact_e2=True)
        sizes.append(dict(calendar_grid_seconds=grid,variables=model.NumVars,binary=model.NumBinVars,
            integer=model.NumIntVars,linear_constraints=model.NumConstrs,indicators=model.NumGenConstrs,
            total_constraints=model.NumConstrs+model.NumGenConstrs,build_seconds=time.perf_counter()-start))
        model.dispose()
    write('model_sizes.json',dict(formulation='compact',rows=sizes,encoding='Additional integer minute indices plus equality links',optimization_performed=False))
    seed_path=ROOT/'output/experiments/small7_m8_alns300_20261024_v1/runs/S01_m8_alns_300s_s42/attempt_001/result.json'
    seed=read(seed_path)
    attach_calendar_decisions(parent,seed,2,seed['batch_boundaries'])
    assert audit_solution(parent,seed)['valid']
    assert seed['batch_cutoffs']['b1']==43410
    boundaries=seed['batch_boundaries'];assert all(v%60==0 for v in boundaries)
    cutoffs=copy.deepcopy(seed['batch_cutoffs']);cutoffs['b1']=43440
    selected=parent.fixed_calendar(2,boundaries)
    assignment=seed['order_batch_assignment'];pool=seed['container_pool_size']
    raw=empty_result(selected.project(assignment,pool,cutoffs),'alns')
    raw['info'].update(status='FEASIBLE',objective=None,
        diagnostic_source='Deterministic minute-grid projection of prior ALNS result; no optimization')
    raw['e1_trips']=copy.deepcopy(seed['e1_trips']);raw['e2_trips']=copy.deepcopy(seed['e2_trips'])
    for trip in raw['e1_trips']:
        if trip['batch_id']=='b1':
            for event in trip['events']:
                if event['kind']=='p':event['start']+=30
            trip['return_complete']+=30
    result=lift_result(selected,raw,assignment,pool,'alns',cutoffs)
    attach_calendar_decisions(parent,result,2,boundaries)
    result['audit']=audit_solution(parent,result)
    assert result['audit']['valid'] and result['audit']['periodic_replay']['valid'],result['audit']
    assert math.isclose(result['audit']['objective'],seed['info']['objective'],abs_tol=1e-7)
    assert all(v%60==0 for v in result['batch_boundaries'])
    assert all(v%60==0 for v in result['batch_cutoffs'].values())
    write('minute_witness_result.json',result)
    write('verification.json',dict(optimization_performed=False,frozen_hashes_verified=True,
        original_nonminute_solution_audit_valid=True,minute_witness_audit_valid=True,
        minute_witness_periodic_replay_valid=True,minute_witness_cost=result['audit']['objective'],
        K=2,pool=pool,minute_witness_cutoffs=cutoffs,full_unit_suite_run=False,
        projection='b1 cutoff 12:03:30 -> 12:04:00; E1 b1 pickup and depot return completion +30 seconds; all other physical schedule values unchanged'))
    lines=['# S01 模型8：分钟批次决策空间核查','',
        '范围：K=1～4、780分钟周期、每个配送/回收阶段至少1分钟。车辆运行与服务保留原秒级时间。未调用 optimize()，生产求解器与正式数据未修改。','',
        '下表仅统计满足批次日历约束的日历数量，尚未施加订单、路径、容量、回收及库存约束，不是完整可行解数量。',
        '每个K有2K个阶段，扣除最短阶段后按整数非负组成计数，C(H/g - 2K(m/g) + 2K - 1, 2K - 1)。','',
        '| K | 自由批次时间点 | 秒级日历数 | 分钟级日历数 | 缩小倍数 |','|---|---:|---:|---:|---:|']
    for r in rows:lines.append(f"| {r['K']} | {r['free_calendar_points']} | {r['second_calendars']:,} | {r['minute_calendars']:,} | {r['reduction_factor']:.4g} |")
    lines+=['','紧凑统一MILP的结构数量（额外整数分钟索引及等式的实现方式）：','',
        '| 批次刻度 | 变量 | 二进制 | 线性约束 | 指示约束 | 合计约束 |','|---|---:|---:|---:|---:|---:|']
    for r in sizes:lines.append(f"| {r['calendar_grid_seconds']}秒 | {r['variables']} | {r['binary']} | {r['linear_constraints']} | {r['indicators']} | {r['total_constraints']} |")
    lines+=['','已构造并独立审计一个整分钟批次方案：K=2、箱池4、成本35.50906153797168。',
        '原方案第一批截点12:03:30改为12:04:00，对应一级回收事件和回库完成时刻顺延30秒，其余物理方案保持；两周期重放通过。',
        '原秒级已审计方案有非整分钟截点，不能属于分钟域；上述新方案属于分钟域。因此本例确实缩小可行域，且分钟域非空。',
        '计数缩小倍数不能等同于求解速度倍数。新增整除约束没有减少路径二进制变量，求解性能需要后续实测。','']
    (OUT/'report.md').write_text('\n'.join(lines),encoding='utf-8')
    print(json.dumps(dict(calendar_counts=rows,model_sizes=sizes,witness_cost=result['audit']['objective']),ensure_ascii=False,indent=2))

if __name__=='__main__':main()
