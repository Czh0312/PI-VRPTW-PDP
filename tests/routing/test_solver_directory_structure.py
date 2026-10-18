"""Directory ownership, optional imports and relocated certificate invalidation."""
from importlib import import_module
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
CORE = {
    'exact': {'__init__', 'solver', 'model', 'parameters', 'variables',
              'constraints', 'objective', 'initialization'},
    'alns': {'__init__', 'solver', 'parameters', 'state', 'construction',
             'neighborhoods', 'evaluator', 'search_control'},
}

IMPORT_ALL = r'''
import importlib
import importlib.abc
import pkgutil
import sys

number, method = sys.argv[1:]
prefix = f'models.routing.model_{number}.{method}'

class Guard(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        parts = fullname.split('.')
        if fullname.startswith('models.routing.model_'):
            assert parts[2] == 'model_'+number, fullname
            if len(parts) > 3:
                assert parts[3] == method, fullname
        if method == 'alns':
            assert not fullname.startswith('gurobipy'), fullname
        return None

sys.meta_path.insert(0, Guard())
package = importlib.import_module(prefix)
count = 0
for entry in pkgutil.walk_packages(package.__path__, prefix+'.'):
    importlib.import_module(entry.name)
    count += 1
assert count >= 7
print('IMPORTED', count)
'''


@pytest.mark.parametrize('number', range(1, 9))
@pytest.mark.parametrize('method', ['exact', 'alns'])
def test_all_components_are_owned_and_importable(number, method):
    folder = ROOT/f'models/routing/model_{number}/{method}'
    assert {p.stem for p in folder.glob('*.py')} == CORE[method]
    for child in folder.rglob('*.py'):
        assert (child.parent/'__init__.py').is_file()
    completed = subprocess.run(
        [sys.executable, '-c', IMPORT_ALL, str(number), method],
        cwd=ROOT, capture_output=True, text=True, timeout=30,
    )
    assert completed.returncode == 0, completed.stdout+'\n'+completed.stderr
    assert 'IMPORTED' in completed.stdout


def test_relocated_certificate_fingerprint_still_tracks_rules_and_solver(monkeypatch):
    module = import_module('models.routing.model_5.exact.accelerators.multibatch.logic_benders')
    seen = set()
    changed = set()

    def fingerprint(path):
        seen.add(path)
        return 'changed' if path in changed else 'original'

    monkeypatch.setattr(module, '_file_hash', fingerprint)
    before = module.source_fingerprint()
    targets = [
        ROOT/'models/routing/model_5/exact/constraints.py',
        ROOT/'models/routing/model_5/exact/accelerators/multibatch/logic_benders.py',
        ROOT/'models/common/cycle/audit.py',
        ROOT/'models/cost/joint_batch.py',
    ]
    assert set(targets) <= seen
    for target in targets:
        changed.add(target)
        assert module.source_fingerprint() != before
        changed.clear()


@pytest.mark.parametrize('organized', [False, True])
@pytest.mark.parametrize('old,new', [
    ('partitioned_assignment', 'search.partitioned_assignment'),
    ('single_batch', 'search.single_batch'),
    ('joint_solver', 'search.fixed_count'),
    ('search', 'search.calendar'),
    ('batch_search', 'search.batch_search'),
    ('calendar_search', 'solver'),
])
def test_experiment_observer_resolves_frozen_and_current_layouts(tmp_path, monkeypatch, organized, old, new):
    from types import SimpleNamespace
    from experiments.runs import run_thesis_completion as driver
    package = 'models.routing.model_8.alns'
    if organized:
        (tmp_path/'search').mkdir()
    expected = package+'.'+(new if organized else old)
    component = object()

    def load(name):
        if name == package:
            return SimpleNamespace(__file__=str(tmp_path/'__init__.py'))
        assert name == expected
        return component

    monkeypatch.setattr(driver, 'import_module', load)
    assert driver.solver_component(8, 'alns', old) is component
