"""Independently recheck the known disputed complete inventory model."""
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import time
from datetime import datetime

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT/'output/experiments/model5_hybrid_round8_20261005'
OUT = BASE/'guard_validation'
VERIFIER = BASE/'focus_primal/hash0/verify.py'


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n')


def register():
    OUT.mkdir(exist_ok=False)
    plan = read(BASE/'plan.json')
    shutil.copy2(plan['guard_model'], OUT/'master.mps.gz')
    shutil.copy2(BASE/'guard_replay.mps.gz', OUT/'master_verification.mps.gz')
    paths = [OUT/'master.mps.gz', OUT/'master_verification.mps.gz',
             BASE/'guard_counterexample.json', VERIFIER, Path(__file__)]
    write(OUT/'plan.json', dict(registered_before_verification=True,
        registered_at=datetime.now().astimezone().isoformat(),
        sha256={str(p):sha(p) for p in paths}, solver_replay_limit=30,
        rules='Existing independent full variable/objective/linear/indicator matrix checks and accepted-bound reoptimization, unchanged tolerances. Known relaxation counterexample only; not M5 cold proof.'))


def verify():
    plan = read(OUT/'plan.json')
    def frozen():
        for path, digest in plan['sha256'].items():
            assert sha(path) == digest, path
    frozen()
    assert not (OUT/'validation.json').exists()
    begun = time.perf_counter()
    spec = importlib.util.spec_from_file_location('round8_independent_verifier', VERIFIER)
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    checker.frozen()
    diagnostic = read(BASE/'guard_counterexample.json')['result']
    assert diagnostic['status'] == 'PRIMARY_BOUND_REJECTED'
    assert diagnostic['reported_raw_bound'] > diagnostic['accepted_raw_bound']+1e-5
    assert diagnostic['witness']['valid']
    cert = dict(raw_bound=diagnostic['accepted_raw_bound'],
        reported_raw_bound=diagnostic['reported_raw_bound'], bound_verification=diagnostic)
    models = []
    checker.inventory_guard(cert, OUT/'master.mps.gz', models)
    frozen(); checker.frozen()
    result = dict(status='VERIFIED_COMPLETE_RELAXATION_MATRICES_AND_BOUND',
        diagnostic_sha256=sha(BASE/'guard_counterexample.json'), models=models,
        reported=diagnostic['reported_raw_bound'], accepted=diagnostic['accepted_raw_bound'],
        primary_rejected=True, seconds=time.perf_counter()-begun,
        scope='Known historical inventory relaxation diagnostic; no M5 upper or new cold run.',
        checked_at=datetime.now().astimezone().isoformat())
    write(OUT/'validation.json', result)
    print('VERIFIED GUARD', {k:v for k,v in result.items() if k!='models'}, flush=True)


if __name__ == '__main__':
    register() if sys.argv[1] == 'register' else verify()
