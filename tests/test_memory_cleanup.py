"""Exercise ComfyUI maintenance requests against an isolated HTTP server."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
import unittest
from unittest.mock import patch

from dart3s.memory_cleanup import release_idle_models


class MemoryCleanupTests(unittest.TestCase):
    def fixture(self, queue=None, reject_free=False, initially_freed=False):
        calls = []
        state = {"freed": initially_freed}
        queue = {"queue_running": [], "queue_pending": []} if queue is None else queue

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                calls.append(("GET", self.path, None))
                if self.path == "/queue":
                    value = queue
                elif self.path == "/system_stats":
                    allocated = 35 * 1024**2 if state["freed"] else 1600 * 1024**2
                    value = {"devices": [{"index": 0, "type": "cuda",
                        "torch_vram_total": allocated, "torch_vram_free": 0,
                        "vram_free": 8 * 1024**3 - allocated,
                        "vram_total": 8 * 1024**3}]}
                else:
                    self.send_error(404)
                    return
                payload = json.dumps(value).encode()
                self.send_response(200)
                self.end_headers()
                self.wfile.write(payload)

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                calls.append(("POST", self.path, body))
                if reject_free or self.path != "/free":
                    self.send_error(503)
                    return
                state["freed"] = True
                self.send_response(200)
                self.end_headers()

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def close():
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

        self.addCleanup(close)
        return f"http://127.0.0.1:{server.server_port}", calls

    def test_release_keeps_results_and_process_without_submitting_a_job(self):
        url, calls = self.fixture()
        with patch("dart3s.memory_cleanup.time.sleep"):
            report = release_idle_models(url)
        self.assertEqual(report["status"], "release_observed")
        self.assertEqual([c for c in calls if c[0] == "POST"], [
            ("POST", "/free", {"unload_models": True, "free_memory": False})])
        self.assertGreater(report["before"]["0"]["allocated"], report["after"]["0"]["allocated"])

    def test_active_or_queued_work_is_left_alone(self):
        for key in ("queue_running", "queue_pending"):
            with self.subTest(key=key):
                queue = {"queue_running": [], "queue_pending": []}
                queue[key] = [[1, "another-job"]]
                url, calls = self.fixture(queue)
                report = release_idle_models(url)
                self.assertEqual(report, {"status": "skipped", "reason": "server_busy"})
                self.assertEqual(calls, [("GET", "/queue", None)])

    def test_already_free_gpu_does_not_add_another_wait_or_request(self):
        url, calls = self.fixture(initially_freed=True)
        self.assertEqual(release_idle_models(url)["status"], "already_released")
        self.assertFalse(any(c[0] == "POST" for c in calls))

    def test_unknown_queue_cannot_trigger_a_maintenance_request(self):
        url, calls = self.fixture({"queue_running": []})
        self.assertEqual(release_idle_models(url)["reason"], "unrecognized_queue")
        self.assertEqual(calls, [("GET", "/queue", None)])

    def test_maintenance_failure_is_reported_without_raising(self):
        url, calls = self.fixture(reject_free=True)
        report = release_idle_models(url)
        self.assertEqual(report["status"], "unavailable")
        self.assertIn("503", report["error"])
        self.assertEqual(len([c for c in calls if c[0] == "POST"]), 1)


if __name__ == "__main__":
    unittest.main()
