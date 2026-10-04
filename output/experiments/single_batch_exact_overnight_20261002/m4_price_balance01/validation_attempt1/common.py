from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json
import os
import sys
import time

OUT = Path(__file__).resolve().parent
NIGHT = OUT.parent
ROOT = NIGHT.parents[2]
sys.path.insert(0, str(OUT/'source_snapshot'))


def read(p): return json.loads(Path(p).read_text())
def write(p, v): Path(p).write_text(json.dumps(v, indent=2, allow_nan=False)+'\n')
def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def external():
    checkpoints = {n: read(ROOT/'output/experiments'/n/'checkpoint.json') for n in
        ('alns_single_batch_overnight_20261001', 'm5_overnight_20261001', 'model5_alns_overnight_20261001')}
    active = {n: {k: p[k] for k in ('active_process', 'running', 'active_worker') if p.get(k)}
              for n, p in checkpoints.items()}
    return dict(observed_utc=datetime.now(timezone.utc).isoformat(), loadavg=os.getloadavg(),
                checkpoints=checkpoints, recorded_active={n: p for n, p in active.items() if p},
                limitation='No complete process inventory or CPU exclusivity; running fields included.')


def gate(stage):
    while True:
        state = external()
        if datetime.now(timezone.utc) >= datetime.fromisoformat('2026-10-01T23:00:00+00:00'):
            raise RuntimeError('07:00 local reached; save and stop new pricing work')
        if not state['recorded_active']: return state
        with (OUT/'wait_activity.jsonl').open('a') as f:
            f.write(json.dumps(dict(stage=stage, **state))+'\n')
        p = read(NIGHT/'checkpoint.json')
        p.update(status='M4_PRICE_BALANCE_WAITING_EXTERNAL', active_process=None,
                 balance_waiting_pid=os.getpid(), balance_waiting_stage=stage)
        write(NIGHT/'checkpoint.json', p)
        time.sleep(30)


def load_case(folder):
    from configuration.loader import load_routing_case
    from models.routing.common.cycle import load_instance
    cfg = read(folder/'case.json')
    for key in ('data', 'orders', 'routing_config', 'operating_policy', 'dataset_metadata', 'pool_config'):
        cfg[key] = str((folder.parent/'inputs'/Path(cfg[key]).name).resolve())
    rebased = folder/'case_balance_rebased.json'
    if rebased.exists(): assert read(rebased) == cfg
    else: write(rebased, cfg)
    config, fleet = load_routing_case(rebased)
    return load_instance(config, fleet)


def frozen_cases():
    for batch in ('paired15_30', 'independent15_30'):
        for folder in sorted((OUT/'frozen_inputs'/batch).glob('zhou*')):
            yield batch, folder.name, folder
