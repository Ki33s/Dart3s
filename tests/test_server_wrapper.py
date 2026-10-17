"""Exercise wrapper argument forwarding without loading any GPU libraries."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from dart3s.comfy3d import PROJECT


class ServerWrapperTest(unittest.TestCase):
    def test_forwards_args_and_enables_fault_diagnostics_with_core_import_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scripts = root / "scripts"
            scripts.mkdir()
            wrapper = scripts / "comfy_server.py"
            shutil.copyfile(PROJECT / "scripts/comfy_server.py", wrapper)
            core = root / "ComfyUI_windows_portable/ComfyUI"
            core.mkdir(parents=True)
            (core / "fixture_dependency.py").write_text("value = 'core import succeeded'\n", encoding="utf-8")
            (core / "main.py").write_text(
                "import faulthandler, json, sys, fixture_dependency\n"
                "print(json.dumps({'name': __name__, 'argv': sys.argv, 'faults': faulthandler.is_enabled(), "
                "'dependency': fixture_dependency.value}))\n", encoding="utf-8")
            completed = subprocess.run([sys.executable, "-s", str(wrapper), "--listen", "127.0.0.1", "--port", "8188", "--cpu"],
                                       cwd=root, capture_output=True, text=True, encoding="utf-8", timeout=10, shell=False)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            result = json.loads(completed.stdout)
            self.assertEqual(result["name"], "__main__")
            self.assertEqual(result["argv"], [str(core / "main.py"), "--listen", "127.0.0.1", "--port", "8188", "--cpu"])
            self.assertTrue(result["faults"])
            self.assertEqual(result["dependency"], "core import succeeded")


if __name__ == "__main__":
    unittest.main()
