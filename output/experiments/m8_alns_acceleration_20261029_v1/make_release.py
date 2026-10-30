"""Freeze a release that preserves the legacy evaluator above nine orders."""
import shutil
from run import HERE

destination = HERE / 'sources' / 'released'
if destination.exists():
    raise FileExistsError(destination)
shutil.copytree(HERE / 'sources' / 'candidate', destination,
                ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
print(str(destination))
