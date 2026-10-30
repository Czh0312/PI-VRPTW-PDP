"""Raw-input-only IO protocol for a continuing cold-start exact experiment."""
import hashlib
import json
import os
from pathlib import Path
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
SOURCE = HERE/'source'


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def save(path,value):
    path = Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    payload = json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)
    temporary = path.with_name(path.name+'.tmp')
    for attempt in range(8):
        try:
            temporary.write_text(payload,encoding='utf-8')
            temporary.replace(path)
            return
        except PermissionError:
            if attempt == 7:
                raise
            time.sleep(.02*(attempt+1))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def path_key(path):
    return os.path.normcase(os.path.abspath(os.fsdecode(path)))


class WorkspaceReadGuard:
    def __init__(self,allowed):
        self.root = path_key(ROOT)+os.sep
        self.source = path_key(SOURCE)+os.sep
        self.allowed = {path_key(p) for p in allowed}
        self.code = {path_key(HERE/name) for name in ('bench.py','protocol.py')}
        self.read_inputs,self.blocked = set(),[]

    def __call__(self,event,args):
        if event != 'open' or isinstance(args[0],int):
            return
        path,mode,flags = args
        if flags & os.O_WRONLY:
            return
        key = path_key(path)
        if not key.startswith(self.root) or key in self.code:
            return
        if key in self.allowed:
            self.read_inputs.add(key)
            return
        if key.startswith(self.source) and Path(key).suffix in ('.py','.pyc','.pyd','.dll'):
            return
        self.blocked.append(key)
        raise PermissionError('Cold-start read allowlist rejected: '+key)
