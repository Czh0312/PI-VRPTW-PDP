"""Strict hash-seed execution adapter; keep exploratory predecessors intact."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[2]
PREVIOUS = ROOT/'output/experiments/model5_hybrid_round8_20261005/focus_primal'
OUT = PREVIOUS/'hash0'
DRIVER = ROOT/'tools/audit/benchmark_model5_round8_focus.py'


def load_driver():
    spec = importlib.util.spec_from_file_location('round8_focus_driver', DRIVER)
    driver = importlib.util.module_from_spec(spec); spec.loader.exec_module(driver)
    driver.OUT = OUT
    return driver


def register(driver):
    OUT.mkdir(exist_ok=False)
    previous = driver.read(PREVIOUS/'plan.json')
    for directory in ('inputs','source_snapshot','probes'):
        shutil.copytree(PREVIOUS/directory, OUT/directory)
    shutil.copy2(PREVIOUS/'verify.py', OUT/'verify.py')
    verifier = (OUT/'verify.py').read_text().replace(
        "['warm_focus0','warm_focus1','native_seed50_focus1']",
        "['warm_focus0','warm_focus1','native_seed50_focus1','native_seed50_focus0']")
    (OUT/'verify.py').write_text(verifier)
    jobs = previous['jobs']+[{**previous['jobs'][-1], 'id':'native_seed50_focus0',
                              'options':{**previous['jobs'][-1]['options'],'primal_mip_focus':0}}]
    files = [*(OUT/'inputs').iterdir(), *(OUT/'source_snapshot').rglob('*.py'),
             *(OUT/'probes/checkers').glob('*.py'), OUT/'verify.py']
    plan = {**previous, 'jobs':jobs, 'registered_at':driver.datetime.now().astimezone().isoformat(),
        'sha256':{str(p.relative_to(OUT)):driver.sha(p) for p in files},
        'adapter_sha256':driver.sha(Path(__file__)),
        'actual_hash_seed_environment':os.environ['PYTHONHASHSEED'],
        'interpreter_hash_probe':hash('M5_round8_fixed_seed'),
        'correction':'Earlier warm results have unspecified actual Python hash seeding and are exploratory only. This fresh pair enforces seed0 before model imports; no original result is overwritten.',
        'native_selection_rule':'After full warm validations, choose the focus with smaller upper (ties0). Run only its registered native job. This is development selection on the same instance, not held-out evaluation.',
        'previous_unexecuted':'Exploratory focus1/native jobs remain NOT_EXECUTED_SUPERSEDED_PRE_RUN; no silent retry of a crashed solver.'}
    driver.write(OUT/'plan.json', plan)
    driver.write(OUT/'verification_plan.json', dict(registered_before_verification=True,
        validator_sha256=driver.sha(OUT/'verify.py'), rules='Original independent full-domain and physical checks, same fixed .5second reporting tolerance.'))
    driver.write(OUT/'checkpoint.json', dict(status='REGISTERED_HASH0', running=None, jobs=jobs))
    print('REGISTERED STRICT HASH0', jobs, flush=True)


if __name__ == '__main__':
    assert os.environ.get('PYTHONHASHSEED') == '0', 'Set hash seed before starting Python'
    driver = load_driver()
    if sys.argv[1] == 'register':
        register(driver)
    else:
        plan = driver.read(OUT/'plan.json')
        assert driver.sha(Path(__file__)) == plan['adapter_sha256']
        assert hash('M5_round8_fixed_seed') == plan['interpreter_hash_probe']
        driver.worker(sys.argv[1])
