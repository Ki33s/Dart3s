"""Check the inexpensive profile really removes inference work, not just faces."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from dart3s.comfy3d import Parameters

ROOT = Path(__file__).resolve().parents[1]


class QualityValidationTests(unittest.TestCase):
    def test_invalid_quality_is_rejected(self):
        with self.assertRaises(ValueError):
            Parameters(quality="unknown").validate()


class FastRecipeTests(unittest.TestCase):
    def test_actual_cli_graph_bypasses_refinement_and_retains_texture_shape(self):
        from comfy_cli.workflow_to_api import convert_ui_to_api
        source = ROOT / "downloads/template.original.json"
        before = hashlib.sha256(source.read_bytes()).hexdigest()
        catalog = ROOT / "logs/object_info-progress.json"
        schema = json.loads(catalog.read_text(encoding="utf-8"))
        env = {**os.environ, "COMFY_NO_TELEMETRY": "1", "PYTHONUTF8": "1",
               "COMFY_WHERE": "local", "LOCALAPPDATA": str(ROOT / ".state")}
        with tempfile.TemporaryDirectory(dir=ROOT / "logs", prefix="fast-quality-") as temporary:
            for quality in ("standard", "fast"):
                with self.subTest(quality=quality):
                    output = Path(temporary) / f"{quality}.json"
                    args = [str(ROOT / ".venv-cli/Scripts/python.exe"), str(ROOT / "scripts/build_workflow.py"),
                            "--quality", quality, "--model", "trellis2", "--output", str(output),
                            "--object-info", str(catalog)]
                    if quality == "fast":
                        args += ["--polygons", "1000", "--remesh-resolution", "128", "--texture-size", "128",
                                 "--preview-directory", str(Path(temporary) / "previews")]
                    run = subprocess.run(args, cwd=ROOT, env=env, capture_output=True, encoding="utf-8", timeout=60)
                    self.assertEqual(run.returncode, 0, run.stderr + run.stdout)
                    ui = json.loads(output.read_text(encoding="utf-8"))
                    api = convert_ui_to_api(ui, schema)
                    self.assertEqual(api["122"]["inputs"]["image"], "viking_wolf_rune_axe.png")
                    self.assertEqual(len(ui["extra"]["favoritedWidgets"]["favorites"]), 8)
                    if quality == "standard":
                        self.assertEqual(api["94"]["inputs"]["target_resolution"], 1024)
                        self.assertEqual(api["3"]["inputs"]["steps"], 12)
                        self.assertEqual(api["18"]["inputs"]["steps"], 20)
                        self.assertEqual(api["12"]["inputs"]["steps"], 12)
                        self.assertEqual(api["186"]["inputs"]["target_face_count"], 10000)
                        self.assertIn("224", api)
                        self.assertIn("233", api)
                    else:
                        for omitted in ("94", "23", "224", "233"):
                            self.assertNotIn(omitted, api)
                        for node, steps in (("3", 6), ("18", 8), ("12", 6)):
                            self.assertEqual(api[node]["inputs"]["steps"], steps)
                        self.assertEqual(api["92"]["inputs"]["samples"], ["18", 0])
                        gate = api[str(api["98"]["inputs"]["shape_latent"][0])]
                        self.assertEqual(gate["class_type"], "Dart3sGeometryReady")
                        self.assertEqual(gate["inputs"]["samples"], ["18", 0])
                        self.assertEqual(api["98"]["inputs"]["positive"], ["91", 0])
                        self.assertEqual(api["98"]["inputs"]["negative"], ["91", 1])
                        self.assertEqual(api["241"]["inputs"]["resolution"], 128)
                        self.assertEqual(api["241"]["inputs"]["smooth_iters"], 1)
                        self.assertEqual(api["186"]["inputs"]["target_face_count"], 1000)
                        self.assertEqual(api["288"]["inputs"]["value"], 128)
                        self.assertNotIn("normal_map", api["210"]["inputs"])
                        self.assertNotIn("occlusion", api["210"]["inputs"])
                        self.assertIn("base_color", api["210"]["inputs"])
                        self.assertIn("metallic", api["210"]["inputs"])
                        self.assertIn("roughness", api["210"]["inputs"])
                        self.assertEqual(sum(n["class_type"].startswith("Dart3sProgress") for n in api.values()), 4)
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), before)


if __name__ == "__main__":
    unittest.main()
