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
from datetime import datetime, timezone


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


def wait_for_dispatch_memory(directory, job, *, available=None, pause=time.sleep, clock=time.monotonic):
    """Queue outside the solve clock until there is stable physical headroom."""
    if available is None:
        import psutil
        available = lambda: psutil.virtual_memory().available
    directory = Path(directory)
    begun, ready_since, pressure = clock(), None, False
    threshold, stable_seconds = 768*1024**2, 4
    state_file = directory/f'resource_wait_{job["method"]}.json'
    def record(status, free_bytes=None):
        state = dict(job=job['id'], method=job['method'], status=status,
            available_bytes=free_bytes, minimum_dispatch_available_bytes=threshold,
            queue_wait_seconds=clock()-begun, updated_at=datetime.now(timezone.utc).isoformat(),
            solver_started=False, queue_wait_in_solver_budget=False)
        atomic_write(state_file,state)
        return state
    while True:
        if (directory/'STOP').exists():
            return record('DEFERRED_RESOURCE_STOP')
        free = available()
        if free >= threshold:
            if not pressure:
                return record('READY',free)
            ready_since = clock() if ready_since is None else ready_since
            if clock()-ready_since >= stable_seconds:
                return record('READY',free)
        else:
            pressure, ready_since = True, None
        record('WAITING_FOR_MEMORY',free)
        pause(2)


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
    original_execute = module.execute
    def resource_aware_execute(out, manifest, job):
        dispatch = wait_for_dispatch_memory(out,job)
        if dispatch['status'] != 'READY':
            # No attempt directory or completion marker is created, so the
            # registered job remains pending on the next normal resume.
            return d.terminal_row(job,'DEFERRED_RESOURCE_STOP','No worker started during resource wait')
        row = original_execute(out,manifest,job)
        attempt = out/'runs'/job['id']/f'attempt_{row["attempt"]:03d}'
        atomic_write(attempt/'dispatch.json',dispatch)
        return row
    module.execute = resource_aware_execute
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
