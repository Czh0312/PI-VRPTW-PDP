"""One pre-registered native budget followup; preserve all earlier attempts."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[2]
PREVIOUS = ROOT/'output/experiments/model5_hybrid_round8_20261005/focus_primal/hash0'
OUT = PREVIOUS.parent/'rebalance'
DRIVER = ROOT/'tools/audit/benchmark_model5_round8_focus.py'


def load_driver():
    spec = importlib.util.spec_from_file_location('round8_focus_driver', DRIVER)
    driver = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(driver)
    driver.OUT = OUT
    return driver


def register(driver):
    previous = driver.read(PREVIOUS/'plan.json')
    validation = driver.read(PREVIOUS/'native_seed50_focus1_validation.json')
    assert validation['status'] == 'VERIFIED' and not validation['summary']['target_reached']
    OUT.mkdir(exist_ok=False)
    for name in ('inputs', 'source_snapshot', 'probes'):
        shutil.copytree(PREVIOUS/name, OUT/name)
    verifier = (PREVIOUS/'verify.py').read_text().replace(
        "['warm_focus0','warm_focus1','native_seed50_focus1','native_seed50_focus0']",
        "['native_seed50_rebalance']")
    (OUT/'verify.py').write_text(verifier)
    original = next(j for j in previous['jobs'] if j['id'] == 'native_seed50_focus1')
    job = {**original, 'id':'native_seed50_rebalance', 'options':{
        **original['options'], 'cover_time_limit':1020, 'subset_time_fraction':.15}}
    files = [*(OUT/'inputs').iterdir(), *(OUT/'source_snapshot').rglob('*.py'),
             *(OUT/'probes/checkers').glob('*.py'), OUT/'verify.py']
    plan = {**previous, 'jobs':[job],
        'registered_at':driver.datetime.now().astimezone().isoformat(),
        'sha256':{str(p.relative_to(OUT)):driver.sha(p) for p in files},
        'adapter_sha256':driver.sha(Path(__file__)),
        'interpreter_hash_probe':hash('M5_round8_fixed_seed'),
        'predecessor_result_sha256':driver.sha(PREVIOUS/'native_seed50_focus1.json'),
        'predecessor_validation_sha256':driver.sha(PREVIOUS/'native_seed50_focus1_validation.json'),
        'hypothesis':'Keep approximately the same primary pricing allowance (867 vs870 seconds), reduce subset allowance (153 vs290), and release140seconds for primal and inventory. Both predecessors subsets improved their sites, so retain repricing; gains are a hypothesis.',
        'native_selection_rule':'Only this one registered budget followup; no focus selection or silent retry.',
        'native_scope':'Public cold1500 with a new15second ALNS initial, no historical state or floor. Same input/source/hash seed/solver seed/total budget as previous candidate; wall-limited initial and intermediate pools may differ. Observational joint-budget trial, not exact same-initial causal comparison.',
        'changed_parameters':['cover_time_limit:1160->1020', 'subset_time_fraction:.25->.15'],
        'development':'This configuration is selected after observing the predecessor failure, not a held-out stability test.'}
    driver.write(OUT/'plan.json', plan)
    driver.write(OUT/'verification_plan.json', dict(registered_before_verification=True,
        validator_sha256=driver.sha(OUT/'verify.py'),
        rules='Unchanged full-domain, physical, raw-bound and fixed.5second budget checks.'))
    driver.write(OUT/'checkpoint.json', dict(status='REGISTERED_REBALANCE', running=None, jobs=[job]))
    print('REGISTERED REBALANCE', job, flush=True)


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
