"""Real Windows sharing-lock regression and worker-command identity."""
import ctypes
from ctypes import wintypes
import json
from pathlib import Path
import sys
from threading import Timer
from types import SimpleNamespace

import pytest

from experiments.runs import spacetime_windows_io_adapter as adapter


@pytest.mark.skipif(sys.platform != 'win32', reason='Requires a native Windows file-sharing handle')
def test_replace_retries_a_real_windows_read_handle_without_delete_sharing(tmp_path):
    target = tmp_path/'progress.json'
    target.write_text('{"before":true}', encoding='utf-8')
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,
        wintypes.LPVOID,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateFileW(str(target),0x80000000,3,None,3,0,None)
    assert handle != ctypes.c_void_p(-1).value
    timer = Timer(.2,lambda:kernel.CloseHandle(handle))
    timer.start()
    try:
        adapter.atomic_write(target,dict(after=True))
    finally:
        timer.join()
    assert json.loads(target.read_text()) == dict(after=True)
    assert not list(tmp_path.glob('*.tmp'))


@pytest.mark.parametrize('filename,raises', [('live.json',False),('completion.json',True)])
def test_persistent_lock_preserves_previous_file_and_only_drops_telemetry(tmp_path,monkeypatch,filename,raises):
    target = tmp_path/filename
    target.write_text('{"previous":true}')
    def locked(*args):
        raise PermissionError('sharing violation')
    monkeypatch.setattr(adapter.os,'replace',locked)
    monkeypatch.setattr(adapter.time,'sleep',lambda value:None)
    if raises:
        with pytest.raises(PermissionError):
            adapter.atomic_write(target,dict(new=True))
    else:
        adapter.atomic_write(target,dict(new=True))
    assert json.loads(target.read_text()) == dict(previous=True)
    assert not list(tmp_path.glob('*.tmp'))


def test_worker_redirection_keeps_all_solver_arguments_and_original_command(tmp_path):
    driver, wrapper = tmp_path/'driver.py',tmp_path/'controller_io_adapter.py'
    seen = []
    backend = SimpleNamespace(Popen=lambda command,**kwargs:seen.append((command,kwargs)))
    proxy = adapter.WorkerSubprocessProxy(driver,wrapper,backend)
    original = ['python.exe',str(driver),'worker','--job','S08_m8_exact_300s_s42','--attempt','2']
    proxy.Popen(original,cwd=str(tmp_path))
    assert original[1] == str(driver)
    assert seen[0][0] == [original[0],str(wrapper),*original[2:]]
    assert seen[0][1] == dict(cwd=str(tmp_path))


def test_unrelated_subprocess_is_not_redirected(tmp_path):
    seen = []
    proxy = adapter.WorkerSubprocessProxy(tmp_path/'driver.py',tmp_path/'adapter.py',
        SimpleNamespace(Popen=lambda command:seen.append(command)))
    command = ['python.exe',str(tmp_path/'other.py'),'worker']
    proxy.Popen(command)
    assert seen == [command]


def test_resource_queue_dispatches_immediately_with_headroom(tmp_path):
    state = adapter.wait_for_dispatch_memory(tmp_path,dict(id='job',method='exact'),
        available=lambda:1024**3,pause=lambda seconds:pytest.fail('No delay needed'),clock=lambda:0)
    assert state['status']=='READY'
    assert state['queue_wait_seconds']==0
    assert not state['solver_started']


def test_resource_queue_waits_for_stable_headroom_outside_solve_budget(tmp_path):
    elapsed = [0]
    values = iter([100,900,900,900])
    def pause(seconds):
        elapsed[0] += seconds
    state = adapter.wait_for_dispatch_memory(tmp_path,dict(id='job',method='alns'),
        available=lambda:next(values)*1024**2,pause=pause,clock=lambda:elapsed[0])
    assert state['status']=='READY'
    assert state['queue_wait_seconds']==6
    assert not state['queue_wait_in_solver_budget']
    assert not list(tmp_path.glob('runs/*'))


def test_stop_during_memory_wait_keeps_registered_attempt_unstarted(tmp_path):
    def pause(seconds):
        (tmp_path/'STOP').write_text('maintenance')
    state = adapter.wait_for_dispatch_memory(tmp_path,dict(id='job',method='exact'),
        available=lambda:100*1024**2,pause=pause,clock=lambda:0)
    assert state['status']=='DEFERRED_RESOURCE_STOP'
    assert not (tmp_path/'runs').exists()
