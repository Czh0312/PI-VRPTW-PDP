"""Retry Windows sharing violations without changing frozen solver sources.

Copy beside a registered driver.py. Both controller and worker invocations use
this adapter; the registered manifest, model sources and inputs stay unchanged.
"""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid


def atomic_write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name+f'.{uuid.uuid4().hex}.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    telemetry = path.name == 'live.json'
    attempts = 6 if telemetry else 16
    try:
        for attempt in range(attempts):
            try:
                os.replace(temporary, path)
                return
            except PermissionError:
                if attempt == attempts-1:
                    if telemetry:
                        # Telemetry must never terminate a valid solve. The
                        # next monitor iteration still checks time and memory.
                        return
                    raise
                time.sleep(min(.02*2**attempt, .5))
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except PermissionError:
            pass


class WorkerSubprocessProxy:
    """Redirect only this study's worker bootstrap, leaving argv otherwise exact."""
    def __init__(self, frozen_driver, adapter, backend=subprocess):
        self.frozen_driver = Path(frozen_driver).resolve()
        self.adapter = Path(adapter).resolve()
        self.backend = backend

    def __getattr__(self, name):
        return getattr(self.backend, name)

    def Popen(self, command, *args, **kwargs):
        if (isinstance(command, (list, tuple)) and len(command)>2 and
                Path(command[1]).resolve() == self.frozen_driver and command[2] == 'worker'):
            command = [command[0], str(self.adapter), *command[2:]]
        return self.backend.Popen(command, *args, **kwargs)


def load_frozen(directory):
    directory = Path(directory).resolve()
    frozen = directory/'driver.py'
    spec = importlib.util.spec_from_file_location('registered_spacetime_quality', frozen)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    d = module.driver(directory)
    d.write = atomic_write
    module.subprocess = WorkerSubprocessProxy(frozen, directory/'controller_io_adapter.py')
    return module, d


def main():
    directory = Path(__file__).resolve().parent
    module, d = load_frozen(directory)
    if sys.argv[1:] == ['verify']:
        d.verify(directory, d.read(directory/'manifest.json'))
        print(json.dumps(dict(frozen_sources_and_inputs_verified=True, io_adapter_only=True)))
    else:
        module.main()


if __name__ == '__main__':
    main()
