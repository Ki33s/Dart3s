import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import threading
import unittest

from dart3s.cli_stream import run_stream, run_cancellable
from dart3s.comfy3d import parse_envelope


class StreamTests(unittest.TestCase):
    def test_cancel_silent_client_without_waiting_for_next_progress(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stopped = threading.Event()
            def check():
                if stopped.is_set():
                    raise RuntimeError('TEST cancelled')
            for streamed in (False, True):
                stopped.clear()
                timer = threading.Timer(.25, stopped.set)
                timer.start()
                started = time.monotonic()
                try:
                    with self.assertRaisesRegex(RuntimeError, 'TEST cancelled'):
                        args = [sys.executable, '-c', 'import time; time.sleep(30)']
                        if streamed:
                            run_stream(args, cwd=root, env=None, timeout=40, stdout_path=root/'out.log',
                                       stderr_path=root/'err.log', on_event=lambda e: None, check_cancel=check)
                        else:
                            run_cancellable(args, cwd=root, env=None, timeout=40, input_text=None, check_cancel=check)
                    self.assertLess(time.monotonic()-started, 3)
                finally:
                    timer.cancel()

    def test_delivers_progress_before_completion_and_preserves_envelope(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            child = root / "child.py"
            child.write_text('''import json, time
print(json.dumps({"type":"executing", "node":"23"}), flush=True)
print(json.dumps({"type":"progress", "node":"23", "completed":7, "total":12}), flush=True)
time.sleep(.6)
print(json.dumps({"ok":True,"data":{"prompt_id":"test-job"},"error":None}), flush=True)
''', encoding="utf-8")
            events = []
            started = time.monotonic()
            result = run_stream([sys.executable, str(child)], cwd=root, env=None, timeout=5,
                                stdout_path=root/"out.log", stderr_path=root/"err.log",
                                on_event=lambda e: events.append((time.monotonic()-started, e)))
            self.assertEqual(result.returncode, 0)
            self.assertEqual([e[1]["type"] for e in events], ["executing", "progress"])
            self.assertGreater(time.monotonic()-started-events[-1][0], .45)
            self.assertEqual(parse_envelope(result.stdout)["data"]["prompt_id"], "test-job")
            self.assertEqual((root/"out.log").read_text(), result.stdout)

    def test_timeout_retains_partial_progress(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = 'import time; print(\'{"type":"progress","completed":1,"total":12}\', flush=True); time.sleep(10)'
            with self.assertRaises(subprocess.TimeoutExpired) as raised:
                run_stream([sys.executable, "-c", script], cwd=root, env=None, timeout=.5,
                           stdout_path=root/"out.log", stderr_path=root/"err.log", on_event=lambda e: None)
            self.assertIn('"completed":1', raised.exception.output)
            self.assertIn('"completed":1', (root/"out.log").read_text())


if __name__ == "__main__":
    unittest.main()
