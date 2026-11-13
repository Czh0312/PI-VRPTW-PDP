"""Directory ownership, optional imports and retired algorithm isolation."""
from importlib import import_module
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
CORE = {
    'exact': {'__init__', 'solver', 'model', 'formulation', 'milp_solver', 'solution',
              'parameters', 'variables', 'constraints', 'objective'},
    'alns': {'__init__', 'solver', 'search', 'parameters', 'state', 'construction',
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


@pytest.mark.parametrize('number,method',
                         [(n, method) for n in range(1, 6) for method in ('exact', 'alns')])
def test_all_components_are_owned_and_importable(number, method):
    folder = ROOT/f'models/routing/model_{number}/{method}'
    assert CORE[method] <= {p.stem for p in folder.glob('*.py')}
    for child in folder.rglob('*.py'):
        assert (child.parent/'__init__.py').is_file()
    completed = subprocess.run(
        [sys.executable, '-c', IMPORT_ALL, str(number), method],
        cwd=ROOT, capture_output=True, text=True, timeout=30,
    )
    assert completed.returncode == 0, completed.stdout+'\n'+completed.stderr
    assert 'IMPORTED' in completed.stdout


@pytest.mark.parametrize('number', range(1, 7))
def test_exact_layout_has_no_retired_directories_or_forwarders(number):
    folder = ROOT/f'models/routing/model_{number}/exact'
    for name in ('accelerators', 'formulations', 'search'):
        assert not (folder/name).exists()
        assert not (folder/(name+'.py')).exists()
    for name in ('bounds', 'primal', 'branch_cut', 'assignment_search', 'k_search', 'diagnostics',
                 'enhanced_solver.py', 'initialization.py'):
        assert not (folder/name).exists()
    assert not (folder.parent/'hybrid').exists()
