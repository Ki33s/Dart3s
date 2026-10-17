"""Progress observers must transport existing objects without changing generation."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from dart3s.comfy3d import generate_3d

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / ".venv-cli/Scripts/comfy.exe"
PYTHON = ROOT / ".venv-cli/Scripts/python.exe"
CATALOG = ROOT / "logs/object_info-progress.json"


class ProgressAdapterTests(unittest.TestCase):
    def test_prepare_reports_directory_before_build_and_keeps_generation_parameters(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            image = project / "image.png"
            image.touch()
            scripts = project / ".venv-cli/Scripts"
            scripts.mkdir(parents=True)
            (scripts / "comfy.exe").touch()
            (scripts / "python.exe").touch()
            events, calls = [], []

            def build(argv, **kwargs):
                calls.append(argv)
                output = Path(argv[argv.index("--output") + 1])
                output.write_text("{}", encoding="utf-8")
                return subprocess.CompletedProcess(argv, 0, json.dumps({"workflow": str(output)}), "")

            with patch("dart3s.comfy3d.subprocess.run", side_effect=build):
                result = generate_3d(image, project=project, prepare_only=True, object_info=image,
                                     visual_progress=True, preview_size=512, progress=events.append)
            building = next(e for e in events if e["status"] == "building")
            preview = Path(building["preview_directory"])
            self.assertTrue(preview.is_dir())
            self.assertEqual(preview.parent, Path(result["workflow"]).parent)
            self.assertEqual(building["preview_size"], 512)
            self.assertEqual(calls[0][calls[0].index("--preview-size") + 1], "512")
            self.assertEqual(calls[0][calls[0].index("--preview-directory") + 1], str(preview))
            metadata = json.loads(Path(result["metadata_path"]).read_text(encoding="utf-8"))
            self.assertEqual(metadata["preview_directory"], str(preview))
            self.assertEqual(metadata["parameters"]["remesh_resolution"], 256)
            self.assertEqual(metadata["parameters"]["texture_size"], 256)
            self.assertNotIn("preview_size", metadata["parameters"])

    def test_invalid_preview_settings_fail_before_server_or_files(self):
        for kwargs in ({"preview_size": 64}, {"preview_size": True}, {"preview_size": "256"},
                       {"visual_progress": "yes"}):
            with self.subTest(kwargs=kwargs), patch("dart3s.comfy3d._Session") as session:
                with self.assertRaises(ValueError):
                    generate_3d("nonexistent.png", **kwargs)
                session.assert_not_called()


@unittest.skipUnless(CLI.is_file() and CATALOG.is_file(), "Real progress-node object_info required")
class ProgressRecipeCLITests(unittest.TestCase):
    def test_actual_cli_apply_preserves_model_calculation_for_each_preview_size(self):
        from comfy_cli.workflow_to_api import convert_ui_to_api
        source = ROOT / "downloads/template.original.json"
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        original_ids = {str(n["id"]) for n in json.loads(source.read_text(encoding="utf-8"))["nodes"]}
        schema = json.loads(CATALOG.read_text(encoding="utf-8"))
        env = {**os.environ, "LOCALAPPDATA": str(ROOT / ".state"), "COMFY_NO_TELEMETRY": "1",
               "PYTHONUTF8": "1", "COMFY_WHERE": "local"}
        with tempfile.TemporaryDirectory(dir=ROOT / "logs", prefix="progress-pipeline-") as directory:
            def build(name, *extra):
                output = Path(directory) / (name + ".json")
                process = subprocess.run([str(PYTHON), str(ROOT / "scripts/build_workflow.py"),
                    "--object-info", str(CATALOG), "--output", str(output), "--model", "trellis2", *extra],
                    env=env, cwd=ROOT, capture_output=True, encoding="utf-8", timeout=60)
                self.assertEqual(process.returncode, 0, process.stderr + process.stdout)
                workflow = json.loads(output.read_text(encoding="utf-8"))
                return workflow, convert_ui_to_api(workflow, schema)

            for quality, size in (("standard", 128), ("standard", 256), ("standard", 512), ("fast", 256)):
                with self.subTest(quality=quality, size=size):
                    baseline, baseline_api = build(f"baseline-{quality}-{size}", "--quality", quality)
                    workflow, api = build(f"preview-{quality}-{size}", "--quality", quality,
                                          "--preview-directory", str(Path(directory) / "previews"),
                                          "--preview-size", str(size))
                    observers = {nid: n for nid, n in api.items() if n["class_type"].startswith("Dart3sProgress")}
                    gates = {nid: n for nid, n in api.items() if n["class_type"] == "Dart3sGeometryReady"}
                    self.assertEqual(len(observers), 4)
                    self.assertEqual(len(gates), 1)
                    self.assertEqual(len(api), len(baseline_api) + 5)
                    gate_id, gate = next(iter(gates.items()))
                    self.assertEqual(api["98"]["inputs"]["shape_latent"], [gate_id, 0])
                    self.assertEqual(gate["inputs"]["samples"], baseline_api["98"]["inputs"]["shape_latent"])
                    geometry_id = str(gate["inputs"]["mesh"][0])
                    self.assertEqual(observers[geometry_id]["inputs"]["stage"], "geometry")
                    def upstream(nid):
                        parents = {str(v[0]) for v in api[nid]["inputs"].values() if isinstance(v, list)}
                        return parents | {p for parent in parents for p in upstream(parent)}
                    texture_dependencies = upstream("12")
                    self.assertTrue({"92", gate_id, geometry_id} <= texture_dependencies)
                    self.assertFalse({"241", "186"} & texture_dependencies)
                    for observer in observers.values():
                        self.assertEqual(observer["inputs"]["preview_size"], size)
                        if observer["class_type"] == "Dart3sProgressVoxel":
                            self.assertEqual(observer["inputs"]["model_frame"], "z_up")
                    for nid, node in api.items():
                        if nid in observers or nid in gates:
                            continue
                        # Shared primitive receives a leaderless id on each build.
                        if node["class_type"] == "PrimitiveInt" and nid not in baseline_api:
                            base_node = next(n for k, n in baseline_api.items() if k not in original_ids)
                            self.assertEqual(node["inputs"], base_node["inputs"])
                            continue
                        expected = baseline_api[nid]
                        self.assertEqual(node["class_type"], expected["class_type"])
                        for field, value in node["inputs"].items():
                            if field == "seed" and isinstance(value, list) and nid in ("18", "23"):
                                self.assertEqual(api[str(value[0])]["inputs"], baseline_api[str(expected["inputs"][field][0])]["inputs"])
                            elif isinstance(value, list) and str(value[0]) in gates:
                                self.assertEqual(gates[str(value[0])]["inputs"]["samples"], expected["inputs"][field])
                            elif isinstance(value, list) and str(value[0]) in observers:
                                observed = observers[str(value[0])]
                                original_slot = "voxel" if observed["class_type"] == "Dart3sProgressVoxel" else "base_color" if observed["class_type"] == "Dart3sProgressTexture" else "mesh"
                                self.assertEqual(observed["inputs"][original_slot], expected["inputs"][field])
                            else:
                                self.assertEqual(value, expected["inputs"][field])
                    self.assertEqual(workflow["extra"]["favoritedWidgets"]["favorites"][:2],
                                     baseline["extra"]["favoritedWidgets"]["favorites"][:2])
            self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), digest)

    def test_pixal_structure_frame_and_model_selector_survive_cli_apply(self):
        from comfy_cli.workflow_to_api import convert_ui_to_api
        env = {**os.environ, "LOCALAPPDATA": str(ROOT / ".state"), "COMFY_NO_TELEMETRY": "1",
               "PYTHONUTF8": "1", "COMFY_WHERE": "local"}
        with tempfile.TemporaryDirectory(dir=ROOT / "logs", prefix="progress-pixal-") as directory:
            output = Path(directory) / "workflow.json"
            process = subprocess.run([str(PYTHON), str(ROOT / "scripts/build_workflow.py"),
                "--object-info", str(CATALOG), "--output", str(output), "--model", "pixal3d",
                "--preview-directory", str(Path(directory) / "previews")],
                env=env, cwd=ROOT, capture_output=True, encoding="utf-8", timeout=60)
            self.assertEqual(process.returncode, 0, process.stderr + process.stdout)
            api = convert_ui_to_api(json.loads(output.read_text(encoding="utf-8")),
                                    json.loads(CATALOG.read_text(encoding="utf-8")))
            voxel = next(n for n in api.values() if n["class_type"] == "Dart3sProgressVoxel")
            self.assertEqual(voxel["inputs"]["model_frame"], "y_up")
            self.assertFalse(api["316"]["inputs"]["value"])


if __name__ == "__main__":
    unittest.main()
