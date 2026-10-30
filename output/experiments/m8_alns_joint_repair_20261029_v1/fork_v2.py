import shutil
from bench import HERE

destination = HERE/'sources/candidate_v2'
if destination.exists():
    raise FileExistsError(destination)
shutil.copytree(HERE/'sources/candidate', destination, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
shutil.copy2(HERE/'bench.py', HERE/'pilot_driver.py')
print(str(destination))
