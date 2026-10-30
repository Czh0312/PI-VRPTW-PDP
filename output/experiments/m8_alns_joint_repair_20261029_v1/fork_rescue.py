import shutil
from bench import HERE

destination = HERE/'sources/rescue'
if destination.exists():
    raise FileExistsError(destination)
shutil.copytree(HERE/'sources/baseline', destination, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
relative = 'models/routing/model_8/alns/neighborhoods.py'
shutil.copyfile(HERE/'sources/precedence'/relative, destination/relative)
print(str(destination))
