import shutil
from bench import HERE

destination = HERE/'sources/candidate_v3'
if destination.exists():
    raise FileExistsError(destination)
shutil.copytree(HERE/'sources/candidate_v2', destination, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
shutil.copy2(HERE/'bench.py', HERE/'retained_driver.py')
print(str(destination))
