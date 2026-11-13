"""Register the new current dataset and an explicit, reversible archive plan.

File moves are performed by the companion PowerShell script after validation.
No optimization, deletion, environment changes or background dispatch occur.
"""
from datetime import datetime, timezone
from pathlib import Path

from tools.configuration.loader import PROJECT_ROOT
from tools.data.build_formal_benchmark27 import digest, write_json
from tools.data.build_uniform_benchmark27 import COUNTS, DEFAULT_OUTPUT, VERSION, read
from tools.audit.audit_uniform_benchmark27 import audit_uniform


def register():
    root=PROJECT_ROOT
    dataset=root/DEFAULT_OUTPUT
    inspection=root/'output/inspections/uniform_benchmark27_20261024_v1'
    inspection.mkdir(parents=True,exist_ok=True)
    audit=audit_uniform(dataset)
    if not audit['valid']:
        raise ValueError('Refusing to archive before successful input audit')
    new_registry='benchmark27_uniform_7_20_50_v1'
    new_study='m3_m8_uniform_7_20_50_v1'
    targets=[p for p in (root/'data').iterdir() if p.is_dir() and p!=dataset]
    targets += [p for p in (root/'config/experiments').iterdir()
                if p.is_dir() and p.name not in (new_registry,new_study)]
    targets += list((root/'config/datasets').glob('*.json'))
    if (root/'config/cases/thesis_completion').exists():
        targets.append(root/'config/cases/thesis_completion')
    retired=root/'archive/datasets/retired_20261024'
    entries=[]
    for path in sorted(targets):
        relative=path.relative_to(root)
        destination=retired/relative
        if destination.exists():
            raise ValueError(f'Retired target already exists: {destination}')
        paths=[path] if path.is_file() else sorted(p for p in path.rglob('*') if p.is_file())
        entries.append(dict(source=relative.as_posix(),destination=destination.relative_to(root).as_posix(),
            kind='file' if path.is_file() else 'directory',file_count=len(paths),
            hashes={p.relative_to(root).as_posix():digest(p) for p in paths}))
    stamp=datetime.now(timezone.utc).isoformat()
    write_json(inspection/'archive_plan.json',dict(status='PREPARED',created_at=stamp,
        current_dataset=DEFAULT_OUTPUT,expected_active_customer_counts=COUNTS,
        targets=entries,deleted_files=0,solver_started=False))
    registry=root/'config/experiments'/new_registry
    manifest=read(dataset/'manifest.json')
    write_json(registry/'frozen.json',dict(schema_version=1,status='USER_REQUESTED_FROZEN',confirmed_on='2026-10-24',
        dataset=DEFAULT_OUTPUT,dataset_revision=5,manifest_sha256=digest(dataset/'manifest.json'),
        selection_sha256=digest(dataset/'selection.json'),case_index_sha256=digest(dataset/'case_index.csv'),
        base_case_count=27,model_config_count=216,models=list(range(1,9)),
        customer_count_by_size=COUNTS,customer_count_quotas={s:{str(n):9} for s,n in COUNTS.items()},
        demand_contract=manifest['demand_contract'],change_policy='New versions only; never change archived or frozen inputs in place.'))
    write_json(root/'config/datasets/uniform_7_20_50_v1.json',dict(dataset_version=VERSION,
        dataset=DEFAULT_OUTPUT,customer_count_by_size=COUNTS,cases_per_size=9,
        generator='tools/data/build_uniform_benchmark27.py',source='data/formal_benchmark27_spacetime_v4_1',
        source_location='archive/datasets/retired_20261024/data/formal_benchmark27_spacetime_v4_1',
        selection_rule='proportional_site_period_v1',preserve_source_order_fields=True,preserve_source_resources=True))
    trial_ids=[f'{prefix}{i:02d}' for prefix in ('S','M','L') for i in (1,2,3)]
    chosen=[b for b in manifest['base_cases'] if b['id'] in trial_ids]
    jobs=[]
    for seed in (42,43,44):
        for base in chosen:
            for model in (3,8):
                budget=dict(small=300,medium=600,large=1200)[base['size']]
                jobs.append(dict(id=f'{base["id"]}_m{model}_alns_{budget}s_s{seed}',case=base['id'],
                    model=model,size=base['size'],customer_count=base['customer_count'],seed=seed,
                    budget_seconds=budget,method='alns',objective='cost',case_path=base['models'][str(model)]))
    study=root/'config/experiments'/new_study
    plan=dict(schema_version=1,status='STOPPED_NOT_STARTED',created_at=stamp,dataset=DEFAULT_OUTPUT,
        dataset_manifest_sha256=digest(dataset/'manifest.json'),complete_dataset_cases=27,
        customer_count_by_size=COUNTS,complete_cases_per_size=9,trial_cases_per_size=3,
        models=[3,8],seeds=[42,43,44],budgets_seconds=dict(small=300,medium=600,large=1200),
        objective_by_model={'3':'cost','8':'cost'},m8_batch_count_candidates=[1,2,3,4],
        m8_calendar_decision_grid_seconds=60,m3_preserve_fixed_calendar=True,external_initial_solutions=False,
        required_pre_dispatch_checks=['dataset_and_input_hashes_match','actual_customer_counts_equal_7_20_50',
            'selected_cases_per_size_equal_3','model3_direct_cost_mode','m8_K_domain_equal_1_to_4',
            'minute_calendar_adapter_installed_and_output_checked'],
        selected_cases=[dict(case=b['id'],size=b['size'],customer_count=b['customer_count'],
                             model3_case=b['models']['3'],model8_case=b['models']['8']) for b in chosen],
        jobs=jobs,planned_runs=len(jobs),planned_solver_seconds=sum(j['budget_seconds'] for j in jobs),
        planned_solver_hours=10.5,automatic_start=False,user_stop_remains_effective=True)
    assert len(jobs)==54 and plan['planned_solver_seconds']==37800
    write_json(study/'plan.json',plan)
    write_json(study/'registered_hashes.json',dict(plan_sha256=digest(study/'plan.json')))
    write_json(root/'config/experiments/current.json',dict(dataset=DEFAULT_OUTPUT,
        frozen_registry=f'config/experiments/{new_registry}/frozen.json',
        alns_plan=f'config/experiments/{new_study}/plan.json',status='STOPPED_NOT_STARTED',
        customer_count_by_size=COUNTS,complete_cases_per_size=9,trial_cases_per_size=3))
    write_json(inspection/'input_audit.json',audit)
    (study/'README.md').write_text('# 当前ALNS实验方案\n\n'
        '完整数据集小7、中20、大50顾客，每档9例，共27例；模型1～8共216个入口。\n\n'
        '本轮先运行S01～S03、M01～M03、L01～L03，每档3例；模型3与8；配对种子42、43、44；共54次。'
        '小、中、大每模型每种子预算分别5、10、20分钟，总算法预算10.5小时。\n\n'
        '统一总成本目标。模型8在单份预算内搜索K=1～4，批次边界和截点整分钟；模型3保留固定日历。'
        '启动时须应用plan.json中的实验约束并检查实际顾客数，不能直接按规模标签选例。\n\n'
        '当前保持停止；本轮数据整理没有启动求解。旧实验结果不自动并入新数据的正式汇总。\n',encoding='utf-8')
    print(dict(registered=True,archive_targets=len(entries),archive_file_count=sum(e['file_count'] for e in entries),
               dataset=DEFAULT_OUTPUT,cases=27,trial_cases=9,planned_runs=54,solver_started=False))


if __name__=='__main__':register()
