"""Launch the installed native Art3dViewer for interactive GLB inspection."""
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import subprocess


DEFAULT_VIEWER = Path(r'D:\UnityProjects\Civilization\Builds\Art3dViewer\Art3dModelViewer.exe')


class ModelViewer:
    def __init__(self, parent, glb, project, size=256):
        self.source = Path(glb).resolve()
        if not self.source.is_file():
            raise FileNotFoundError(self.source)
        self.executable = Path(os.environ.get('DART3S_VIEWER_EXE', str(DEFAULT_VIEWER)))
        if not self.executable.is_file():
            raise FileNotFoundError(f'Art3dViewer не найден: {self.executable}')
        # Argument lists preserve spaces/Cyrillic without invoking a shell.
        # The native GUI renders the mesh; preview size does not limit it.
        self.process = subprocess.Popen(
            [str(self.executable), str(self.source)], cwd=self.executable.parent)

    @property
    def closed(self):
        return self.process.poll() is not None

    def _windows(self):
        """Only touch windows belonging to this launched process."""
        native = ctypes.WinDLL('user32', use_last_error=True)
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        native.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
        native.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        native.IsWindowVisible.argtypes = [wintypes.HWND]
        handles = []

        @callback_type
        def collect(handle, _):
            owner = wintypes.DWORD()
            native.GetWindowThreadProcessId(handle, ctypes.byref(owner))
            if owner.value == self.process.pid and native.IsWindowVisible(handle):
                handles.append(handle)
            return True

        native.EnumWindows(collect, 0)
        return native, handles

    def focus(self):
        if self.closed:
            return
        native, handles = self._windows()
        native.IsIconic.argtypes = [wintypes.HWND]
        native.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        native.SetForegroundWindow.argtypes = [wintypes.HWND]
        for handle in handles:
            if native.IsIconic(handle):
                native.ShowWindow(handle, 9)  # SW_RESTORE
            native.SetForegroundWindow(handle)

    def close(self):
        if self.closed:
            return
        native, handles = self._windows()
        native.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        for handle in handles:
            native.PostMessageW(handle, 0x0010, 0, 0)  # WM_CLOSE
