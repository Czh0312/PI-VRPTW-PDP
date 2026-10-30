import shutil
from bench import HERE

target = HERE/'sources/bounded'
if target.exists():
    raise FileExistsError(target)
shutil.copytree(HERE/'sources/structured', target, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
shutil.copy2(HERE/'bench.py', HERE/'pilot_driver.py')
print(str(target))
