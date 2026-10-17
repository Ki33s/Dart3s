"""Regress canvas restoration against the installed CLI, without GPU jobs."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / ".venv-cli/Scripts/comfy.exe"
PYTHON = ROOT / ".venv-cli/Scripts/python.exe"
SCHEMA = ROOT / "logs/object_info.json"
SOURCE = ROOT / "downloads/template.original.json"


@unittest.skipUnless(CLI.is_file() and SCHEMA.is_file(), "Installed CLI and offline object_info required")
class CLIWidgetMetadataTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=ROOT / "logs", prefix="widget-regression-")
        self.addCleanup(self.directory.cleanup)
        self.output = Path(self.directory.name) / "workflow.json"
        self.env = {**os.environ, "LOCALAPPDATA": str(ROOT / ".state"), "COMFY_NO_TELEMETRY": "1",
                    "PYTHONUTF8": "1", "COMFY_WHERE": "local"}

    def run_command(self, *args):
        result = subprocess.run([str(a) for a in args], cwd=ROOT, env=self.env, capture_output=True,
                                text=True, encoding="utf-8", timeout=60, shell=False)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        return json.loads(result.stdout)

    def assert_widgets(self, workflow, values):
        nodes = {str(n["id"]): n for n in workflow["nodes"]}
        from comfy_cli.workflow_to_api import convert_ui_to_api
        api = convert_ui_to_api(workflow, json.loads(SCHEMA.read_text(encoding="utf-8")))
        for (nid, name), expected in values.items():
            with self.subTest(node=nid, widget=name):
                node = nodes[str(nid)]
                # Modern frontend restoration picks this named map before the
                # array. Testing only CLI export would miss the original bug.
                if "widgets_values_named" in node:
                    self.assertEqual(node["widgets_values_named"][name], expected)
                if str(nid) in api:
                    actual = api[str(nid)]["inputs"][name]
                    if isinstance(actual, list):
                        # Converted widget inputs are controlled by shared
                        # primitives, so inspect the primitive's real value.
                        actual = api[str(actual[0])]["inputs"]["value"]
                    self.assertEqual(actual, expected)

    def test_builder_apply_keeps_defaults_and_named_restoration_consistent(self):
        before = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
        self.run_command(PYTHON, ROOT / "scripts/build_workflow.py", "--object-info", SCHEMA,
                         "--output", self.output)
        workflow = json.loads(self.output.read_text(encoding="utf-8"))
        self.assert_widgets(workflow, {(94, "target_resolution"): 1024, (186, "target_face_count"): 10000,
            (241, "resolution"): 256, (288, "value"): 256, (316, "value"): False,
            (3, "seed"): 56, (18, "seed"): 42, (23, "seed"): 42, (12, "seed"): 43,
            (122, "image"): "viking_wolf_rune_axe.png", (147, "texture_size"): 256,
            (196, "resolution"): 256, (224, "resolution"): 256, (233, "resolution"): 256})
        self.assertEqual(len(workflow["extra"]["favoritedWidgets"]["favorites"]), 8)
        self.assertEqual(hashlib.sha256(SOURCE.read_bytes()).hexdigest(), before)

    def test_real_apply_changes_named_primitive_and_upsample_values(self):
        self.run_command(PYTHON, ROOT / "scripts/build_workflow.py", "--object-info", SCHEMA,
                         "--output", self.output, "--model", "trellis2", "--texture-size", 512,
                         "--shape-seed", 987, "--structure-seed", 98, "--texture-seed", 765,
                         "--polygons", 12000, "--remesh-resolution", 128)
        workflow = json.loads(self.output.read_text(encoding="utf-8"))
        self.assert_widgets(workflow, {(94, "target_resolution"): 1024, (186, "target_face_count"): 12000,
            (241, "resolution"): 128, (288, "value"): 512, (316, "value"): True,
            (3, "seed"): 98, (18, "seed"): 987, (23, "seed"): 987, (12, "seed"): 765})
        # Apply a second edit to the real saved graph, proving reload + rewrite
        # works and legacy nodes without named maps do not acquire one.
        meta = json.loads(self.output.with_suffix(".parameters.json").read_text(encoding="utf-8"))
        shared = str(meta["shape_seed_node"])
        ops = Path(self.directory.name) / "ops.json"
        ops.write_text(json.dumps([{"op": "set_widget", "node": "94", "widget": "target_resolution", "value": 1536},
                                   {"op": "set_widget", "node": "288", "widget": "value", "value": 256},
                                   {"op": "set_widget", "node": shared, "widget": "value", "value": 42}]), encoding="utf-8")
        envelope = self.run_command(CLI, "--json", "workflow", "apply", self.output, "--ops", ops, "--input", SCHEMA)
        self.assertTrue(envelope["ok"], envelope)
        workflow = json.loads(self.output.read_text(encoding="utf-8"))
        nodes = {str(n["id"]): n for n in workflow["nodes"]}
        self.assertEqual(nodes["94"]["widgets_values"], [1536])
        self.assertEqual(nodes["288"]["widgets_values"][0], 256)
        self.assertNotIn("widgets_values_named", nodes[shared])
        self.assert_widgets(workflow, {(94, "target_resolution"): 1536, (288, "value"): 256,
                                      (shared, "value"): 42})
        from comfy_cli.workflow_to_api import convert_ui_to_api
        api = convert_ui_to_api(workflow, json.loads(SCHEMA.read_text(encoding="utf-8")))
        for nid in ("18", "23"):
            self.assertEqual(api[nid]["inputs"]["seed"], [shared, 0])

    def test_package_patch_check_is_idempotent(self):
        result = self.run_command(PYTHON, ROOT / "scripts/configure_cli.py", "--check")
        self.assertTrue(result["ok"], result)
        result = self.run_command(PYTHON, ROOT / "scripts/configure_cli.py")
        self.assertTrue(result["ok"], result)
        self.assertFalse(result["changed"], result)


if __name__ == "__main__":
    unittest.main()
