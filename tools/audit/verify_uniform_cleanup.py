"""Verify archived bytes and the only active dataset without optimization."""
from datetime import datetime, timezone

from tools.configuration.loader import PROJECT_ROOT, load_routing_case
from tools.data.build_uniform_benchmark27 import DEFAULT_OUTPUT, VERSION, read
from tools.data.build_formal_benchmark27 import digest, write_json
from tools.audit.audit_uniform_benchmark27 import audit_uniform
from models.common.cycle import load_instance


def verify():
    root=PROJECT_ROOT
    inspection=root/'output/inspections/uniform_benchmark27_20261024_v1'
    archive=root/'archive/datasets/retired_20261024'
    plan=read(inspection/'archive_plan.json')
    checked=0
    for entry in plan['targets']:
        for relative,expected in entry['hashes'].items():
            path=archive/relative
            if not path.is_file() or digest(path)!=expected:
                raise ValueError(f'Archived input differs: {relative}')
            checked+=1
    supplementary=inspection/'additional_archive.json'
    if supplementary.exists():
        extra=read(supplementary)
        if digest(archive/extra['relative_path'])!=extra['sha256']:
            raise ValueError('Archived original data README differs')
        checked+=1
    data_folders=sorted(p.name for p in (root/'data').iterdir() if p.is_dir())
    if data_folders!=[VERSION]:
        raise ValueError(f'Other active datasets remain: {data_folders}')
    config_folders=sorted(p.name for p in (root/'config/experiments').iterdir() if p.is_dir())
    if config_folders!=['benchmark27_uniform_7_20_50_v1','examples','m3_m8_uniform_7_20_50_v1']:
        raise ValueError(f'Other active experiment cases remain: {config_folders}')
    if sorted(p.name for p in (root/'config/datasets').glob('*.json'))!=['uniform_7_20_50_v1.json']:
        raise ValueError('Other active generation configurations remain')
    for alias in (root/'config/experiments/examples').glob('*.json'):
        cfg,fleet=load_routing_case(alias,use_pool_config=False)
        if len(load_instance(cfg,fleet).orders)!=7:
            raise ValueError(f'Example no longer aliases current S01: {alias}')
    audit=audit_uniform(DEFAULT_OUTPUT)
    write_json(inspection/'input_audit_after_archive.json',audit)
    state=read(root/'config/experiments/current.json')
    if state['status']!='STOPPED_NOT_STARTED':
        raise ValueError('Study must remain stopped')
    result=dict(status='PASS',verified_at=datetime.now(timezone.utc).isoformat(),
        current_dataset=DEFAULT_OUTPUT,active_data_directories=data_folders,
        base_cases=27,model_configurations=216,customer_count_by_size=dict(small=7,medium=20,large=50),
        complete_cases_per_size=9,trial_cases_per_size=3,archived_paths=len(plan['targets']),
        archived_files_verified=checked,all_archived_hashes_unchanged=True,deleted_files=0,
        solver_runs=0,gurobi_checked=False,study_status='STOPPED_NOT_STARTED')
    write_json(inspection/'cleanup_verification.json',result)
    print(result)


if __name__=='__main__':verify()
