"""Preserve the temporal-only diagnostic and prepare compound route repair."""
import shutil
from bench import HERE

destination = HERE / 'sources' / 'joint'
if destination.exists():
    raise FileExistsError(destination)
shutil.copytree(HERE / 'sources' / 'temporal', destination,
                ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
shutil.copy2(HERE / 'bench.py', HERE / 'temporal_driver.py')
print(str(destination))
