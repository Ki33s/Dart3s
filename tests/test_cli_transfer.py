"""Exercise the installed CLI's GLB transfer; this is not GPU generation."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import subprocess
import tempfile
from threading import Thread
import unittest

from dart3s.comfy3d import PROJECT, glb_download_urls, local_environment, parse_envelope


@unittest.skipUnless((PROJECT / ".venv-cli/Scripts/comfy.exe").is_file(), "Installed Comfy CLI required")
class InstalledCLITransferTest(unittest.TestCase):
    def test_real_download_accepts_saved_3d_output_url(self):
        fixture = b"transfer fixture only; not a generated 3D model"

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if not self.path.startswith("/view?"):
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "model/gltf-binary")
                self.send_header("Content-Length", str(len(fixture)))
                self.end_headers()
                self.wfile.write(fixture)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            local_url = f"http://127.0.0.1:{server.server_port}"
            prompt_id = "transfer-fixture"
            history = {prompt_id: {"outputs": {"322": {"3d": [
                {"filename": "transfer-fixture.glb", "subfolder": "", "type": "output"}]}}}}
            stdin = json.dumps({"ok": True, "command": "test fixture", "where": "local", "error": None,
                                "data": {"prompt_id": prompt_id, "outputs": glb_download_urls(history, prompt_id, local_url)}})
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                argv = [str(PROJECT / ".venv-cli/Scripts/comfy.exe"), "--json", "download", "--where", "local", "--out-dir", str(root / "outputs")]
                completed = subprocess.run(argv, cwd=PROJECT, input=stdin, env=local_environment(root, local_url),
                                           capture_output=True, text=True, encoding="utf-8", timeout=30, shell=False)
                self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
                envelope = parse_envelope(completed.stdout)
                self.assertTrue(envelope["ok"], envelope)
                path = Path(envelope["data"]["files"][0]["path"])
                self.assertEqual(path.suffix, ".glb")
                self.assertEqual(path.read_bytes(), fixture)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
