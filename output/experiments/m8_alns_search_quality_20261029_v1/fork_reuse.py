"""Freeze the route-only variant before adding synchronized return waits."""
import shutil
from bench import HERE

destination = HERE / 'sources' / 'reuse'
if destination.exists():
    raise FileExistsError(destination)
shutil.copytree(HERE / 'sources' / 'joint', destination,
                ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
shutil.copy2(HERE / 'bench.py', HERE / 'joint_driver.py')
print(str(destination))
