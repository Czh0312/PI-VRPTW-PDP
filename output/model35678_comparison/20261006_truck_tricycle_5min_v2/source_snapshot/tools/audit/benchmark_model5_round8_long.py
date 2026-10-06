"""Registered2700second public cold run, with no historical initial or bound."""
import importlib.util
import os
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[2]
PREVIOUS = ROOT/'output/experiments/model5_hybrid_round8_20261005/focus_primal/hash0'
OUT = PREVIOUS.parent/'long2700'
DRIVER = ROOT/'tools/audit/benchmark_model5_round8_focus.py'


def load_driver():
    spec = importlib.util.spec_from_file_location('round8_focus_driver', DRIVER)
    driver = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(driver)
    driver.OUT = OUT
    return driver


def register(driver):
    assert driver.read(PREVIOUS/'native_seed50_focus1_validation.json')['status'] == 'VERIFIED'
    second = PREVIOUS.parent/'rebalance/native_seed50_rebalance_validation.json'
    assert driver.read(second)['status'] == 'VERIFIED'
    previous = driver.read(PREVIOUS/'plan.json')
    OUT.mkdir(exist_ok=False)
    for name in ('inputs', 'source_snapshot', 'probes'):
        shutil.copytree(PREVIOUS/name, OUT/name)
    verifier = (PREVIOUS/'verify.py').read_text().replace(
        "['warm_focus0','warm_focus1','native_seed50_focus1','native_seed50_focus0']",
        "['native_seed50_2700']")
    (OUT/'verify.py').write_text(verifier)
    original = next(j for j in previous['jobs'] if j['id'] == 'native_seed50_focus1')
    job = {**original, 'id':'native_seed50_2700', 'budget':2700, 'options':{
        **original['options'], 'time_limit':2700, 'cover_time_limit':1460,
        'conditional_time_limit':305, 'primal_revisit_time_limit':300}}
    files = [*(OUT/'inputs').iterdir(), *(OUT/'source_snapshot').rglob('*.py'),
             *(OUT/'probes/checkers').glob('*.py'), OUT/'verify.py']
    plan = {**previous, 'jobs':[job],
        'registered_at':driver.datetime.now().astimezone().isoformat(),
        'sha256':{str(p.relative_to(OUT)):driver.sha(p) for p in files},
        'adapter_sha256':driver.sha(Path(__file__)),
        'interpreter_hash_probe':hash('M5_round8_fixed_seed'),
        'predecessor_validation_sha256':{str(p):driver.sha(p) for p in (
            PREVIOUS/'native_seed50_focus1_validation.json', second)},
        'native_selection_rule':'One budget exploration; no seed retry or silent replacement.',
        'hypothesis':'Increase primary full-domain pricing1095 vs870 seconds, retain25percent subset repricing, allow300second primal revisit and305second conditional inventory, then resume unused original budget.',
        'native_scope':'Public cold2700seconds, fresh15second ALNS, no previous solution/routes/floor. This jointly changes time and stage limits. It is NOT a1500second algorithm gain or multi-seed stability proof.',
        'changed_parameters':['time_limit:1500->2700', 'cover:1160->1460',
            'conditional:105->305', 'revisit:180->300'],
        'runtime_preference':'Optional budget preference question has no reply after more than60seconds; proceed with stated2700second assumption. A later user constraint supersedes this.',
        'development':'Selected after two independently verified1500second failures; not held-out evaluation.'}
    driver.write(OUT/'plan.json', plan)
    driver.write(OUT/'verification_plan.json', dict(registered_before_verification=True,
        validator_sha256=driver.sha(OUT/'verify.py'),
        rules='Same full-domain, physical and accepted-bound checks, unchanged fixed.5second budget tolerance.'))
    driver.write(OUT/'checkpoint.json', dict(status='REGISTERED_LONG2700', running=None, jobs=[job]))
    print('REGISTERED LONG2700', job, flush=True)


if __name__ == '__main__':
    assert os.environ.get('PYTHONHASHSEED') == '0'
    driver = load_driver()
    if sys.argv[1] == 'register':
        register(driver)
    else:
        plan = driver.read(OUT/'plan.json')
        assert driver.sha(Path(__file__)) == plan['adapter_sha256']
        assert hash('M5_round8_fixed_seed') == plan['interpreter_hash_probe']
        driver.worker(sys.argv[1])
