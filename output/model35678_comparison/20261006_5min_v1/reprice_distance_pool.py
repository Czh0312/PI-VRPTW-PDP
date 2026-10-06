"""Reprice archived schedules after excluding time, vehicle and trip charges."""
from copy import deepcopy
from pathlib import Path
import json
import math
import sys

OUT=Path(__file__).resolve().parent
DEST=OUT/'reprice_distance_pool_v1'
sys.path.insert(0,str(OUT/'source_snapshot'))
from models.cost.joint_batch import JointCostProfile


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write(path,value):
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')


def main():
    plan=read(OUT/'experiment.json')
    original=read(OUT/'inputs/common_economic.json')
    original_profile=JointCostProfile(original)
    revised=deepcopy(original)
    excluded=('e1_work_cost_per_minute','e2_work_cost_per_minute',
              'e1_vehicle_cost_per_cycle','e2_vehicle_cost_per_cycle','e1_trip_cost','e2_trip_cost')
    for field in excluded:
        revised[field]=0
    revised['description']+=' User scenario: occupied time, per-vehicle fixed charges and per-trip fixed charges excluded.'
    profile=JointCostProfile(revised)
    profile_path=DEST/'common_economic.json'
    write(profile_path,revised)
    rows=[]
    for job in plan['jobs']:
        source=OUT/'results'/job['id']
        result=read(source/'result.json')
        audit=read(source/'independent_audit.json')
        old_cost=read(source/'comparison_cost.json')
        assert audit['valid'] and audit['periodic_replay']['valid']
        assert old_cost['economic_profile_hash']==original_profile.hash==plan['economic_profile_hash']
        releases=audit['depot_releases']
        returned=sum(r['quantity'] for r in releases)
        collected=sum(e['quantity'] for t in result['e1_trips'] for e in t['events'] if e['kind']=='p')
        issued=sum(b['container_demand'] for b in result['batches'])
        assert returned==collected==audit['container_balance']['returned_to_depot']==audit['container_balance']['issued']==issued
        assert all(r['clean_ready']<=result['operating_policy']['cycle_end'] for r in releases)
        pool=old_cost['container_pool_size']
        # Physical audit was verified under the original result contract. Do not
        # rewrite its objective/hash to pretend it was solved for the new prices.
        cost=profile.evaluate(audit,pool)
        assert all(cost['components'][field]==0 for field in ('e1_work','e2_work','e1_vehicles','e2_vehicles','e1_trips','e2_trips'))
        omitted=sum(old_cost['components'][field] for field in ('e1_work','e2_work','e1_vehicles','e2_vehicles','e1_trips','e2_trips'))
        assert math.isclose(cost['total'],old_cost['total']-omitted,abs_tol=1e-6)
        assert math.isclose(cost['components']['cleaning'],returned*profile.rates['cleaning_cost_per_return'],abs_tol=1e-8)
        trip_to_batch={t['trip_id']:t['batch_id'] for t in result['e1_trips']}
        by_round={b['id']:0 for b in result['batches']}
        for release in releases:
            by_round[trip_to_batch[release['trip_id']]]+=release['quantity']
        cost.update(source_job=job['id'],source_result=str((source/'result.json').resolve()),
                    source_profile_hash=original_profile.hash,container_pool_size=pool,
                    cost_is_reoptimized=False,interpretation='Repriced archived route, not a new optimizer output.',
                    cleaning_box_uses=returned,cleaning_return_quantities_by_calendar_round=by_round)
        reference=profile.evaluate(audit,audit['minimum_pool_for_schedule'])
        row=dict(id=job['id'],family=job['family'],model=job['model'],seed=job['seed'],
            original_cost=old_cost['total'],revised_cost=cost['total'],pool=pool,minimum_pool=audit['minimum_pool_for_schedule'],
            minimum_pool_reference_cost=reference['total'],distance_cost=cost['components']['e1_distance']+cost['components']['e2_distance'],
            pool_cost=cost['components']['container_pool'],cleaning_cost=cost['components']['cleaning'],
            tracking_cost=cost['components']['tracking'],wear_cost=cost['components']['container_wear'],
            issued_box_uses=issued,returned_box_uses=returned,returns_by_calendar_round=by_round,
            components=cost['components'],economic_profile_hash=profile.hash,cost_is_reoptimized=False)
        rows.append(row)
        write(DEST/'results'/job['id']/'cost.json',cost)
        write(DEST/'results'/job['id']/'cleaning_returns.json',dict(
            cleaning_rate=profile.rates['cleaning_cost_per_return'],returned_box_uses=returned,
            cost=cost['components']['cleaning'],by_calendar_round=by_round,actual_depot_returns=releases))
        cfg=read(job['case_path'])
        cfg.update(economic_config=str(profile_path.resolve()),case_name=cfg['case_name']+'_distance_pool_v1',
                   alns_time=300,alns_iter=1000000,seed=42)
        write(DEST/'cases'/Path(job['case_path']).name,cfg)
    cases=[]
    for case in plan['cases']:
        selected={str(m):next(r for r in rows if r['family']==case['family'] and r['model']==m) for m in (3,5,6,7,8)}
        cases.append(dict(family=case['family'],customers=case['customers'],demand=case['demand'],rows=selected))
    write(DEST/'summary.json',dict(scope='New common-price scenario, repricing the same 30 archived schedules only.',
        source_profile_hash=original_profile.hash,revised_profile_hash=profile.hash,excluded_fields=list(excluded),
        repriced=30,independently_audited_source_solutions=30,cost_is_reoptimized=False,cases=cases))
    write(DEST/'rows.json',rows)
    lines=['# 排除时间费、车辆固定费和趟次固定费后的重新计价','',
        '已将一级、二级的占用时间费、每车固定费、每趟固定费六个系数统一设为0。箱池、距离、清洗、追踪和磨损费用保持原参数。','',
        '这是现有30个方案的重新计价，没有用新目标重新搜索；不得把下表排名当成新目标下的模型最优排名。模型6–8的箱池和路线仍是原目标搜索得到的。原实验输入、方案及费用均保留。','',
        f'原费用哈希：`{original_profile.hash}`；新费用哈希：`{profile.hash}`。所有模型使用同一个新哈希。','',
        '## 保留的费用','',
        '| 项目 | 系数 |', '|---|---:|',
        '| 箱池 / 箱 / 周期 | 5 |', '| 一级、二级距离 / 数据距离单位 | 1、1 |',
        '| 清洗 / 每次回库的箱次 | 0.2 |', '| 追踪 / 使用箱次 | 0.1 |',
        '| 磨损 / 使用箱次 | 10 / 50 = 0.2 |','',
        '周期成本 = 5×箱池N + 一级及二级距离费用 + 0.2×实际回库箱次 + 0.1×发出使用箱次 + 0.2×发出使用箱次。所有费用为合成单位，箱体初始购置投入仍单列，不加入周期费用。车辆容量、数量上限、行驶时间、时间窗、清洗时长和期末回收约束继续生效；不计某项费用不意味着取消对应约束。','',
        '## 原方案按新参数重新计价','',
        '| 算例 | 点数 | M3 | M5 | M6 | M7 | M8 |', '|---|---:|---:|---:|---:|---:|---:|']
    for case in cases:
        values=[f'{case["rows"][str(m)]["revised_cost"]:.2f}' for m in (3,5,6,7,8)]
        lines.append(f'| {case["family"]} | {case["customers"]} | '+ ' | '.join(values)+' |')
    lines += ['', '原M3六例中五例在这些已保存方案里费用最低，另一例为M7。其原因是原联合目标曾允许增加距离来节省时间费；现在时间费已为0，但原联合路线没有重新优化。不能据此判定模型6–8在新目标下更差，它们的可行域允许重新寻找更短或更少箱的方案。','',
        '## 清洗费按实际回库箱次累计','',
        '清洗费 = 清洗单价 × 各次回库并清洗的箱数之和。同一个物理箱子复用后再次返回，需要再次计清洗；未使用的初始清洁库存不洗，不对每个日历段机械收取整个箱池的清洗费。末次回库也必须清洗，并在周期结束前完成。','',
        '例如30个箱子供货3次，每次30个实际返回并清洗，总清洗量为90箱次，单价0.2时费用18；箱池是30，不代表只收费30箱次。若某轮只回20个，就只按该轮20个计价。','',
        '第一组50点实际回库量：','',
        '| 模型 | 各日历回收轮次实际回库箱次 | 总回库箱次 | 清洗费 |', '|---|---|---:|---:|']
    first=cases[0]
    for model in (3,5,6,7,8):
        row=first['rows'][str(model)]
        quantities='、'.join(f'{k}: {v}' for k,v in row['returns_by_calendar_round'].items())
        lines.append(f'| M{model} | {quantities} | {row["returned_box_uses"]} | {row["cleaning_cost"]:.2f} |')
    lines += ['',
        '当前所有订单一次配送对应一次回收，并要求周期末全回库清洗；同一算例的总需求使用箱次Q固定，因此总回库清洗箱次也等于Q。模型5及多批候选可减少箱池N，却不会仅因分批而改变总清洗量Q。此时周期费用可写为 距离费用 + 5N + 0.5Q；清洗、追踪、磨损对该算例是常数，影响模型选择的是距离与箱池权衡。这里没有额外的每轮清洗设备启动固定费。','',
        '排除时间费后，之前推迟发车或提前分界但不改路线/箱池的处理，不再直接产生费用节省。分界仍影响可行性和是否能赶上后续供箱，所以仍可能间接改变距离与箱池。','',
        '## 核验与可重跑配置','',
        '已核对30个独立审计通过的原方案：逐次回库数量之和、一级回收事件量、账本回库量和订单发出箱次完全一致；六项排除费用均为0；新总费用等于旧总费用减去排除分项。','',
        '新共同参数：common_economic.json；可按新目标重跑的30份配置：cases/；费用分项及逐次回库清洗量：results/；汇总：summary.json。新配置的ALNS预算为300秒、种子42，但本次未执行这些重跑。','']
    (DEST/'report.md').write_text('\n'.join(lines),encoding='utf-8')
    print(json.dumps(dict(repriced=len(rows),reoptimized=False,new_profile_hash=profile.hash,
        sample_case=cases[0]['family'],sample_costs={m:r['revised_cost'] for m,r in cases[0]['rows'].items()}),ensure_ascii=False))
    print('PASS: 30 price updates and per-return cleaning quantity checks.')


if __name__=='__main__':
    main()
