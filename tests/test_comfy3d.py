from __future__ import annotations

import json
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from dart3s.comfy3d import ComfyError, generate_3d, glb_download_urls, parse_envelope, resume_3d


def envelope(data=None, error=None):
    return json.dumps({"ok": error is None, "command": "mock", "where": "local",
                       "data": data or {}, "error": error})


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name)
        scripts = self.project / ".venv-cli" / "Scripts"
        scripts.mkdir(parents=True)
        (scripts / "comfy.exe").touch()
        (scripts / "python.exe").touch()
        self.image = self.project / "source with spaces.png"
        self.image.write_bytes(b"image content is transported, not decoded by this adapter")
        self.calls = []
        self.download_glb = True
        self.glb_bytes = b"glTF" + struct.pack("<II", 2, 20) + struct.pack("<II", 0, 0x4E4F534A)
        history = patch("dart3s.comfy3d._Session.extra_model_urls", return_value=["http://127.0.0.1:8188/view?filename=model.glb&type=output"])
        history.start()
        self.addCleanup(history.stop)
        ready = patch("dart3s.comfy3d.server_ready", return_value=True)
        ready.start()
        self.addCleanup(ready.stop)
        cleanup = patch("dart3s.comfy3d.release_idle_models", return_value={"status": "release_observed"})
        self.release_models = cleanup.start()
        self.addCleanup(cleanup.stop)

    def portable_fixture(self):
        portable = self.project / "ComfyUI_windows_portable"
        (portable / "python_embeded").mkdir(parents=True)
        (portable / "python_embeded/python.exe").touch()
        (portable / "ComfyUI").mkdir()
        (portable / "ComfyUI/main.py").touch()
        (self.project / "scripts").mkdir(exist_ok=True)
        (self.project / "scripts/comfy_server.py").touch()
        return portable

    def fake_run(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        command = argv[2] if argv[1] == "--json" else "builder"
        if command == "upload":
            text = envelope({"uploads": [{"cloud_name": "uploaded-source.png", "subfolder": "inputs"}]})
        elif command == "builder":
            output = Path(argv[argv.index("--output") + 1])
            output.write_text("{}", encoding="utf-8")
            text = json.dumps({"workflow": str(output), "favorites": 8})
        elif command == "run":
            text = envelope({"prompt_id": "job-123"})
        elif command == "jobs":
            text = envelope({"prompt_id": argv[4], "status": "completed", "outputs": []})
        elif command == "download":
            destination = Path(argv[argv.index("--out-dir") + 1])
            files = []
            if self.download_glb:
                glb = destination / "job-123_0.glb"
                glb.write_bytes(self.glb_bytes)
                files.append({"path": str(glb)})
            text = envelope({"files": files, "prompt_id": json.loads(kwargs["input"])["data"]["prompt_id"]})
        else:
            self.fail(f"Unexpected command {argv}")
        return subprocess.CompletedProcess(argv, 0, text, "")

    def test_actual_path_and_local_shell_free_transport(self):
        with patch("dart3s.comfy3d.subprocess.run", side_effect=self.fake_run):
            result = generate_3d(self.image, project=self.project, shape_seed=999)
        self.assertTrue(result.glb_path.is_absolute())
        self.assertEqual(result.glb_path.read_bytes(), self.glb_bytes)
        self.assertEqual(result.prompt_id, "job-123")
        for argv, kwargs in self.calls:
            self.assertFalse(kwargs["shell"])
            self.assertEqual(kwargs["env"]["COMFY_WHERE"], "local")
            self.assertEqual(kwargs["env"]["COMFY_NO_TELEMETRY"], "1")
            self.assertEqual(kwargs["env"]["LOCALAPPDATA"], str(self.project / ".state"))
        self.assertIn(str(self.image), self.calls[0][0])
        build_args = self.calls[1][0]
        self.assertEqual(build_args[build_args.index("--image") + 1], "inputs/uploaded-source.png")
        self.assertEqual(build_args[build_args.index("--shape-seed") + 1], "999")
        metadata = json.loads(result.metadata_path.read_text(encoding="utf-8"))
        self.assertEqual(metadata["status"], "completed")
        self.assertEqual(metadata["memory_cleanup"]["status"], "release_observed")
        self.release_models.assert_called_once_with("http://127.0.0.1:8188")
        self.assertEqual(len(metadata["steps"]), 5)
        self.assertIn("model.glb", json.loads(self.calls[-1][1]["input"])["data"]["outputs"][0])

    def test_error_hint_and_job_id_survive_failed_watch(self):
        def fail_watch(argv, **kwargs):
            if "watch" in argv:
                self.calls.append((argv, kwargs))
                return subprocess.CompletedProcess(argv, 1, envelope(error={"code": "execution_error",
                    "message": "Out of memory", "hint": "Reduce resolution", "details": {"node_id": "18"}}), "GPU error")
            return self.fake_run(argv, **kwargs)
        with patch("dart3s.comfy3d.subprocess.run", side_effect=fail_watch):
            with self.assertRaises(ComfyError) as caught:
                generate_3d(self.image, project=self.project)
        self.assertEqual(caught.exception.code, "execution_error")
        self.assertEqual(caught.exception.hint, "Reduce resolution")
        self.assertEqual(caught.exception.details, {"node_id": "18"})
        metadata = json.loads(caught.exception.metadata_path.read_text(encoding="utf-8"))
        self.assertEqual(metadata["prompt_id"], "job-123")
        self.assertEqual(metadata["status"], "failed")
        self.assertFalse(any("download" in argv for argv, _ in self.calls))

    def test_prepare_only_never_uploads_or_submits(self):
        with patch("dart3s.comfy3d.subprocess.run", side_effect=self.fake_run):
            result = generate_3d(self.image, project=self.project, prepare_only=True)
        self.assertEqual(result["status"], "prepared")
        self.assertNotIn("glb_path", result)
        self.assertEqual(len(self.calls), 1)

    def test_resume_watches_existing_id_without_second_submission(self):
        with patch("dart3s.comfy3d.subprocess.run", side_effect=self.fake_run):
            result = resume_3d("job-existing", project=self.project)
        self.assertEqual(result.prompt_id, "job-existing")
        self.assertEqual([a[2] for a, _ in self.calls], ["jobs", "download"])

    def test_missing_or_corrupt_glb_is_never_success(self):
        for glb_exists in (False, True):
            with self.subTest(glb_exists=glb_exists):
                self.download_glb = glb_exists
                self.glb_bytes = b"not a binary glTF"
                with patch("dart3s.comfy3d.subprocess.run", side_effect=self.fake_run):
                    with self.assertRaises(ComfyError) as caught:
                        resume_3d("job-existing", project=self.project)
                self.assertEqual(caught.exception.code, "glb_invalid" if glb_exists else "glb_output_missing")
                self.release_models.assert_not_called()

    def test_client_timeout_preserves_prompt_id_and_never_resubmits(self):
        def time_out(argv, **kwargs):
            if "watch" in argv:
                raise subprocess.TimeoutExpired(argv, kwargs["timeout"], output=b"waiting", stderr=b"still active")
            return self.fake_run(argv, **kwargs)
        with patch("dart3s.comfy3d.subprocess.run", side_effect=time_out):
            with self.assertRaises(ComfyError) as caught:
                generate_3d(self.image, project=self.project, wait_timeout=1)
        self.assertEqual(caught.exception.code, "client_timeout")
        self.assertEqual(caught.exception.details["prompt_id"], "job-123")
        self.assertEqual(sum("run" in argv for argv, _ in self.calls), 1)
        self.release_models.assert_not_called()

    def test_cleanup_unavailable_does_not_lose_a_valid_glb(self):
        self.release_models.return_value = {"status": "unavailable", "error": "endpoint unavailable"}
        with patch("dart3s.comfy3d.subprocess.run", side_effect=self.fake_run):
            result = resume_3d("job-existing", project=self.project)
        self.assertTrue(result.glb_path.is_file())
        metadata = json.loads(result.metadata_path.read_text(encoding="utf-8"))
        self.assertEqual(metadata["status"], "completed")
        self.assertEqual(metadata["memory_cleanup"]["status"], "unavailable")

    def test_ndjson_envelope_and_malformed_output(self):
        value = parse_envelope('{"type":"queued"}\n' + envelope({"prompt_id": "abc"}))
        self.assertEqual(value["data"]["prompt_id"], "abc")
        with self.assertRaises(ComfyError):
            parse_envelope("not JSON")

    def test_external_server_is_rejected_before_subprocess(self):
        with patch("dart3s.comfy3d.subprocess.run") as run:
            with self.assertRaises(ValueError):
                generate_3d(self.image, project=self.project, local_url="http://example.com:8188")
        run.assert_not_called()

    def test_saved_3d_history_is_converted_to_cli_download_urls(self):
        history = {"job-id": {"outputs": {"322": {"3d": [
            {"filename": "model name.glb", "subfolder": "nested", "type": "output"},
            {"filename": "preview.png", "type": "output"}]}, "1": {"images": []}}}}
        self.assertEqual(glb_download_urls(history, "job-id", "http://127.0.0.1:8188"),
                         ["http://127.0.0.1:8188/view?filename=model+name.glb&subfolder=nested&type=output"])
        self.assertEqual(glb_download_urls(history, "another-job", "http://127.0.0.1:8188"), [])

    def test_existing_server_is_reused_without_launching(self):
        with patch("dart3s.comfy3d.subprocess.Popen") as popen:
            with patch("dart3s.comfy3d.subprocess.run", side_effect=self.fake_run):
                result = generate_3d(self.image, project=self.project)
        popen.assert_not_called()
        metadata = json.loads(result.metadata_path.read_text(encoding="utf-8"))
        self.assertFalse(metadata["server"]["started_by_adapter"])

    def test_missing_server_starts_portable_and_records_pid(self):
        portable = self.portable_fixture()
        process = Mock(pid=12345)
        process.poll.return_value = None
        with patch("dart3s.comfy3d.server_ready", side_effect=[False, True]):
            with patch("dart3s.comfy3d.subprocess.Popen", return_value=process) as popen:
                with patch("dart3s.comfy3d.subprocess.run", side_effect=self.fake_run):
                    result = generate_3d(self.image, project=self.project)
        argv = popen.call_args.args[0]
        self.assertEqual(argv[0], str(portable / "python_embeded/python.exe"))
        self.assertEqual(argv[1], "-s")
        self.assertEqual(argv[2], str(self.project / "scripts/comfy_server.py"))
        self.assertEqual(argv[argv.index("--listen") + 1], "127.0.0.1")
        self.assertEqual(popen.call_args.kwargs["cwd"], portable)
        self.assertFalse(popen.call_args.kwargs["shell"])
        self.assertEqual(popen.call_args.kwargs["creationflags"], subprocess.CREATE_NO_WINDOW)
        metadata = json.loads(result.metadata_path.read_text(encoding="utf-8"))
        self.assertEqual(metadata["server"]["pid"], 12345)
        process.terminate.assert_not_called()

    def test_failed_server_stops_before_upload_with_log_hint(self):
        self.portable_fixture()
        process = Mock(pid=12345)
        process.poll.return_value = 7
        with patch("dart3s.comfy3d.server_ready", return_value=False):
            with patch("dart3s.comfy3d.subprocess.Popen", return_value=process):
                with patch("dart3s.comfy3d.subprocess.run") as run:
                    with self.assertRaises(ComfyError) as caught:
                        generate_3d(self.image, project=self.project)
        run.assert_not_called()
        self.assertEqual(caught.exception.code, "server_start_failed")
        self.assertEqual(caught.exception.details["exit_code"], 7)
        self.assertIn("comfyui.stderr.log", caught.exception.hint)

    def test_startup_deadline_is_bounded_without_killing_process(self):
        self.portable_fixture()
        process = Mock(pid=12345)
        process.poll.return_value = None
        with patch("dart3s.comfy3d.server_ready", return_value=False):
            with patch("dart3s.comfy3d.subprocess.Popen", return_value=process):
                with patch("dart3s.comfy3d.time.monotonic", side_effect=[0, 181]):
                    with self.assertRaises(ComfyError) as caught:
                        generate_3d(self.image, project=self.project)
        self.assertEqual(caught.exception.code, "server_start_timeout")
        self.assertEqual(caught.exception.details["pid"], 12345)
        process.terminate.assert_not_called()
        process.kill.assert_not_called()

    def test_offline_preparation_never_checks_or_starts_server(self):
        catalog = self.project / "object_info.json"
        catalog.write_text("{}", encoding="utf-8")
        with patch("dart3s.comfy3d.server_ready") as ready:
            with patch("dart3s.comfy3d.subprocess.Popen") as popen:
                with patch("dart3s.comfy3d.subprocess.run", side_effect=self.fake_run):
                    result = generate_3d(self.image, project=self.project, prepare_only=True, object_info=catalog)
        self.assertEqual(result["status"], "prepared")
        ready.assert_not_called()
        popen.assert_not_called()

    def test_no_start_flag_rejects_unavailable_server_without_process(self):
        with patch("dart3s.comfy3d.server_ready", return_value=False):
            with patch("dart3s.comfy3d.subprocess.Popen") as popen:
                with self.assertRaises(ComfyError) as caught:
                    generate_3d(self.image, project=self.project, auto_start=False)
        popen.assert_not_called()
        self.assertEqual(caught.exception.code, "server_not_running")


if __name__ == "__main__":
    unittest.main()
