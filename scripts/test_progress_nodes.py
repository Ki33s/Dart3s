"""CPU-only tests of pass-through observations, worker ordering and display proxies."""
from __future__ import annotations
import importlib
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "ComfyUI_windows_portable/ComfyUI"))

import numpy as np
from PIL import Image
import torch
from comfy_api.latest import Types
from dart3s import progress_preview as p
nodes = importlib.import_module("custom_nodes.dart3s_progress.nodes")


def triangle():
    return Types.MESH(torch.tensor([[[-1., -1., 0.], [1., -1., 0.], [0., 1., 0.]]]),
                      torch.tensor([[[0, 1, 2]]]),
                      uvs=torch.tensor([[[0., 0.], [1., 0.], [.5, 1.]]]))


class ProgressTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / "outputs", prefix="progress-test-")
        self.directory = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_path_containment_and_sizes(self):
        self.assertEqual(p.preview_path(str(self.directory)), self.directory)
        self.assertIsNone(p.preview_path(""))
        for bad in (ROOT, ROOT / "outputs", ROOT / "outputs/../logs"):
            with self.assertRaises(ValueError):
                p.preview_path(str(bad))
        for size in (128, 256, 512):
            self.assertIs(nodes.Dart3sProgressMesh.validate_inputs(size), True)
        for size in (129, 384, True, "256"):
            self.assertIsNot(nodes.Dart3sProgressMesh.validate_inputs(size), True)

    def test_v3_schemas_and_passthrough_identity(self):
        mesh = triangle()
        voxel = Types.VOXEL(torch.ones((1, 4, 4, 4)))
        texture = torch.ones((1, 16, 16, 3))
        with patch.object(nodes, "submit_preview"):
            for cls, args, source in ((nodes.Dart3sProgressVoxel, (voxel, "", 256), voxel),
                                      (nodes.Dart3sProgressMesh, (mesh, "geometry", "", 256), mesh),
                                      (nodes.Dart3sProgressTexture, (texture, mesh, "", 256), texture)):
                self.assertEqual(cls.define_schema().node_id, cls.__name__)
                self.assertIs(cls.execute(*args).result[0], source)

    def test_node_preview_exception_is_nonfatal(self):
        mesh = triangle()
        with patch.object(nodes, "submit_preview", side_effect=OSError("preview disk failure")), self.assertLogs(level="ERROR"):
            self.assertIs(nodes.Dart3sProgressMesh.execute(mesh, "geometry", str(self.directory), 256).result[0], mesh)

    def test_gray_nodes_wait_and_gate_preserves_calculated_latent(self):
        mesh, latent = triangle(), {"samples": torch.ones((1, 2, 3))}
        with patch.object(nodes, "submit_preview") as submit:
            nodes.Dart3sProgressMesh.execute(mesh, "geometry", str(self.directory), 256)
            self.assertTrue(submit.call_args.kwargs["wait_for_publication"])
        self.assertIs(nodes.Dart3sGeometryReady.execute(latent, mesh).result[0], latent)

    def test_gray_publication_waits_for_atomic_manifest_and_timeout_is_bounded(self):
        worker = p.PreviewWorker()
        entered, release = threading.Event(), threading.Event()
        def draw(work):
            entered.set()
            release.wait(5)
            return Image.new("RGB", (128, 128)), 1
        worker._draw = draw
        results = []
        with patch.object(p, "WORKER", worker):
            caller = threading.Thread(target=lambda: results.append(p.submit_preview(
                "geometry", triangle(), str(self.directory), 128, wait_for_publication=True)))
            caller.start()
            try:
                self.assertTrue(entered.wait(5))
                self.assertTrue(caller.is_alive())
                self.assertFalse((self.directory / "latest.json").exists())
                release.set()
                caller.join(5)
                self.assertEqual(results, [True])
                latest = json.loads((self.directory / "latest.json").read_text())
                self.assertTrue(Path(latest["image"]).is_file())
                self.assertEqual(latest["stage"], "geometry")
                self.assertTrue(worker.wait_idle())
                release.clear()
                started = time.monotonic()
                with self.assertLogs("dart3s.progress", level="WARNING"):
                    self.assertFalse(p.submit_preview("prepared", triangle(), str(self.directory), 128,
                                     wait_for_publication=True, publication_timeout=.05))
                self.assertLess(time.monotonic() - started, 1)
            finally:
                release.set()
                caller.join(5)
                self.assertTrue(worker.wait_idle())

    def test_texture_snapshot_is_independent_and_correct_uv(self):
        mesh = triangle()
        texture = torch.zeros((1, 2, 2, 3))
        texture[0, :, 0, 0] = 1
        vertices = mesh.vertices.clone()
        snapshot, count = p.mesh_snapshot(mesh, texture)
        self.assertEqual(count, 1)
        np.testing.assert_array_equal(snapshot["uv"], mesh.uvs[0].numpy())
        np.testing.assert_array_equal(snapshot["texture"][:, 0, 0], [1, 1])
        snapshot["vertices"][:] = 99
        snapshot["texture"][:] = 99
        self.assertTrue(torch.equal(mesh.vertices, vertices))
        self.assertEqual(float(texture.max()), 1)

    def test_textured_render_uses_actual_base_color(self):
        texture = torch.zeros((1, 8, 8, 3))
        texture[..., 0] = 1
        proxy, _ = p.mesh_snapshot(triangle(), texture)
        image, _ = p._renderer().render([proxy], size=128, supersample=1, yaw=28, elevation=15)
        rgb = np.asarray(image).astype(int)
        self.assertGreater(int(((rgb[..., 0] > rgb[..., 1]+50) & (rgb[..., 0] > rgb[..., 2]+50)).sum()), 1000)

    def test_large_source_display_mesh_is_bounded_without_mutation(self):
        # 1.1 million source triangles, only a few genuine corner cells.
        vertices = np.array([[0., 0., 0.], [1., 0., 0.], [0., 1., 0.], [0., 0., 1.]], np.float32)
        faces = np.tile([[0, 1, 2], [0, 1, 3]], (550000, 1)).astype(np.int32)
        source = SimpleNamespace(vertices=vertices[None], faces=faces[None])
        before = vertices.copy()
        snapshot, count = p.mesh_snapshot(source)
        self.assertEqual(count, 1100000)
        self.assertLessEqual(len(snapshot["faces"]), p.MAX_FACES)
        np.testing.assert_array_equal(vertices, before)
        np.testing.assert_array_equal(faces[0], [0, 1, 2])

    def test_latest_pending_and_stage_order_and_stale_publication(self):
        entered = threading.Event()
        release = threading.Event()
        rendered = []
        worker = p.PreviewWorker()
        def draw(work):
            rendered.append(work.stage)
            if work.stage == "structure":
                entered.set()
                release.wait(5)
            return Image.new("RGB", (work.size, work.size)), 1
        worker._draw = draw
        worker.submit(p.Work(self.directory, "structure", 256, None))
        self.assertTrue(entered.wait(5))
        worker.submit(p.Work(self.directory, "geometry", 256, None))
        worker.submit(p.Work(self.directory, "prepared", 256, None))
        worker.submit(p.Work(self.directory, "textured", 256, None))
        self.assertFalse(worker.submit(p.Work(self.directory, "structure", 256, None)))
        release.set()
        self.assertTrue(worker.wait_idle())
        self.assertEqual(rendered, ["structure", "textured"])
        latest = json.loads((self.directory / "latest.json").read_text())
        self.assertEqual(latest["stage"], "textured")
        with Image.open(latest["image"]) as image:
            self.assertEqual(image.size, (256, 256))
        self.assertFalse(list(self.directory.glob("structure-*.png")))

    def test_worker_error_recovers_for_next_run(self):
        worker = p.PreviewWorker()
        with self.assertLogs("dart3s.progress", level="ERROR"):
            worker.submit(p.Work(self.directory, "geometry", 256, object()))
            self.assertTrue(worker.wait_idle())
        worker.submit(p.Work(self.directory, "prepared", 128, triangle()))
        self.assertTrue(worker.wait_idle())
        latest = json.loads((self.directory / "latest.json").read_text())
        self.assertEqual(latest["size"], 128)
        self.assertEqual(latest["triangles"], 1)

    def test_stage_order_survives_worker_restart(self):
        saved = dict(version=1, revision=47, stage="textured")
        (self.directory / "latest.json").write_text(json.dumps(saved))
        worker = p.PreviewWorker()
        self.assertFalse(worker.submit(p.Work(self.directory, "structure", 256, object())))
        worker._draw = lambda work: (Image.new("RGB", (256, 256)), 10000)
        self.assertTrue(worker.submit(p.Work(self.directory, "textured", 256, object())))
        self.assertTrue(worker.wait_idle())
        updated = json.loads((self.directory / "latest.json").read_text())
        self.assertEqual(updated["stage"], "textured")
        self.assertGreater(updated["revision"], 47)

    def test_actual_structure_frame_and_size(self):
        voxel = Types.VOXEL(torch.zeros((1, 8, 8, 8)))
        voxel.data[0, 1:7, 1:3, 1:5] = 1
        original = voxel.data.clone()
        y_up = np.array(p.render_structure(voxel, 256, "y_up"))
        z_up = np.array(p.render_structure(voxel, 256, "z_up"))
        self.assertEqual(min(y_up.shape[:2]), 256)
        self.assertEqual(y_up.shape[-1], 3)
        self.assertFalse(np.array_equal(y_up, z_up))
        self.assertTrue(torch.equal(voxel.data, original))

    def test_wide_and_tall_geometry_use_selected_short_side_without_stretching(self):
        for width, height in ((356., 256.), (256., 356.)):
            with self.subTest(width=width):
                vertices = torch.tensor([[[0., 0., 0.], [width, 0., 0.],
                                          [width, height, 0.], [0., height, 0.]]])
                mesh = Types.MESH(vertices, torch.tensor([[[0, 1, 2], [0, 2, 3]]]))
                before = vertices.clone()
                primitive, _ = p.mesh_snapshot(mesh)
                for side in (128, 256, 512):
                    image, report = p._renderer().render([primitive], size=side, supersample=1, yaw=0, elevation=0)
                    self.assertEqual(min(image.size), side)
                    self.assertAlmostEqual(image.width/image.height, width/height, delta=1/side)
                    self.assertEqual(report['size'], list(image.size))
                    if side == 256:
                        self.assertEqual(image.size, (int(width), int(height)))
                self.assertTrue(torch.equal(vertices, before))


if __name__ == "__main__":
    unittest.main(verbosity=2)
