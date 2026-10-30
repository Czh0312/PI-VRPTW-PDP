import shutil
from bench import HERE

destination = HERE/'sources/candidate_v4'
if destination.exists():
    raise FileExistsError(destination)
shutil.copytree(HERE/'sources/candidate_v3', destination, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
shutil.copy2(HERE/'bench.py', HERE/'beam_driver.py')
print(str(destination))
