"""Lightweight read-only process/RAM/VRAM samples during a controlled benchmark."""
import argparse
import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time
from urllib.request import ProxyHandler, build_opener

ROOT = Path(__file__).resolve().parents[1]


class ProcessMemory(ctypes.Structure):
    _fields_ = [('cb',wintypes.DWORD),('faults',wintypes.DWORD)] + [
        (name,ctypes.c_size_t) for name in ('peak_working_set','working_set','peak_paged_pool',
          'paged_pool','peak_nonpaged_pool','nonpaged_pool','pagefile','peak_pagefile','private')]


class SystemMemory(ctypes.Structure):
    _fields_ = [('length',wintypes.DWORD),('load',wintypes.DWORD)] + [
        (name,ctypes.c_ulonglong) for name in ('total_physical','available_physical','total_commit',
          'available_commit','total_virtual','available_virtual','extended_virtual')]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--pid',type=int,required=True)
    parser.add_argument('--directory',type=Path,required=True)
    args = parser.parse_args()
    directory = args.directory.resolve()
    if not directory.is_relative_to(ROOT/'logs'):
        raise ValueError('Resource reports must stay in project logs')
    directory.mkdir(parents=True,exist_ok=True)
    kernel = ctypes.WinDLL('kernel32',use_last_error=True)
    psapi = ctypes.WinDLL('psapi',use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE,ctypes.POINTER(wintypes.DWORD)]
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)]*4
    kernel.GlobalMemoryStatusEx.argtypes = [ctypes.POINTER(SystemMemory)]
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE,ctypes.POINTER(ProcessMemory),wintypes.DWORD]
    handle = kernel.OpenProcess(0x0410,False,args.pid)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    opener = build_opener(ProxyHandler({}))
    previous = None
    started = time.monotonic()
    try:
        while time.monotonic()-started < 4200:
            exit_code = wintypes.DWORD()
            if not kernel.GetExitCodeProcess(handle,ctypes.byref(exit_code)) or exit_code.value != 259:
                break
            now = time.monotonic()
            record = dict(utc=datetime.now(timezone.utc).isoformat(),pid=args.pid,elapsed=now-started)
            memory = ProcessMemory(); memory.cb = ctypes.sizeof(memory)
            if psapi.GetProcessMemoryInfo(handle,ctypes.byref(memory),memory.cb):
                record['process'] = {name:int(getattr(memory,name)) for name in
                  ('working_set','peak_working_set','private','pagefile','peak_pagefile','faults')}
            system = SystemMemory(); system.length = ctypes.sizeof(system)
            if kernel.GlobalMemoryStatusEx(ctypes.byref(system)):
                record['system'] = {name:int(getattr(system,name)) for name in
                  ('total_physical','available_physical','total_commit','available_commit','load')}
            times = [wintypes.FILETIME() for _ in range(4)]
            if kernel.GetProcessTimes(handle,*[ctypes.byref(value) for value in times]):
                cpu = sum((value.dwHighDateTime << 32) + value.dwLowDateTime for value in times[2:])/1e7
                record['cpu_seconds'] = cpu
                if previous:
                    record['cpu_percent_system'] = 100*(cpu-previous[1])/(now-previous[0])/os.cpu_count()
                previous = now,cpu
            try:
                with opener.open('http://127.0.0.1:8188/system_stats',timeout=3) as response:
                    stats = json.load(response)
                record['devices'] = stats.get('devices',[])
            except (OSError,ValueError) as error:
                record['api_error'] = str(error)
            summary = directory/'summary.json'
            try:
                state = json.loads(summary.read_text(encoding='utf-8'))
                record['benchmark_status'] = state.get('status')
                runs = state.get('runs',[])
                if runs:
                    last = runs[-1]
                    record['run'] = last.get('index')
                    record['run_status'] = last.get('status')
            except (OSError,ValueError):
                pass
            with (directory/'resources.ndjson').open('a',encoding='utf-8') as output:
                output.write(json.dumps(record,separators=(',',':'))+'\n')
            if (directory/'stop-monitor').exists():
                break
            time.sleep(15)
    finally:
        kernel.CloseHandle(handle)


if __name__ == '__main__':
    main()
