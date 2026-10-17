"""Small CPU milestone previews; no inference, mesh export or source mutation.

One worker owns one replaceable pending item. Images publish only while their
revision remains current. Every run has its own output directory.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
import importlib.util
import json
import logging
from pathlib import Path
import threading
import time
from types import SimpleNamespace

import numpy as np
from PIL import Image, ImageDraw

PROJECT = Path(__file__).resolve().parents[1]
STAGES = {"structure": 0, "geometry": 1, "prepared": 2, "textured": 3}
SIZES = (128, 256, 512)
MAX_FACES = 24000
CHUNK = 32768
LOG = logging.getLogger("dart3s.progress")


def preview_path(value: str, project: Path = PROJECT) -> Path | None:
    if not value:
        return None
    path = Path(value).resolve()
    root = (project / "outputs").resolve()
    if path == root or not path.is_relative_to(root):
        raise ValueError("Preview directory must be a run folder inside project outputs")
    return path


def array(value, *, copy=False):
    if hasattr(value, "detach"):
        value = value.detach().cpu().numpy()
    result = np.asarray(value)
    return result.copy() if copy else result


def first(value):
    return value[0] if getattr(value, "ndim", 0) >= 3 else value


def _renderer():
    spec = importlib.util.spec_from_file_location("dart3s_cpu_renderer", PROJECT / "scripts/render_glb_preview.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def mesh_snapshot(mesh, base_color=None):
    """Copies a bounded display mesh. Million-face sources are clustered in chunks.

    Vertex clustering changes only this display proxy; the original tensors stay
    intact. Faces never cross a chunk without carrying their own vertex coords.
    Small meshes retain exact triangles, UVs and texture coordinates.
    """
    vertices = first(mesh.vertices)
    faces = first(mesh.faces)
    count = int(mesh.face_counts[0]) if getattr(mesh, "face_counts", None) is not None else len(faces)
    vcount = int(mesh.vertex_counts[0]) if getattr(mesh, "vertex_counts", None) is not None else len(vertices)
    vertices, faces = vertices[:vcount], faces[:count]
    uv = normals = None
    if count <= MAX_FACES:
        f = array(faces, copy=True).astype(np.int64, copy=False)
        ids, f = np.unique(f, return_inverse=True)
        f = f.reshape(-1, 3)
        # Index first, transfer only vertices actually needed by the proxy.
        v = array(vertices[ids], copy=True).astype(np.float32, copy=False)
        if getattr(mesh, "uvs", None) is not None:
            uv = array(first(mesh.uvs)[ids], copy=True)
        if getattr(mesh, "normals", None) is not None:
            normals = array(first(mesh.normals)[ids], copy=True)
    else:
        low, high = np.full(3, np.inf), np.full(3, -np.inf)
        for offset in range(0, vcount, CHUNK):
            points = array(vertices[offset:offset + CHUNK])
            low = np.minimum(low, points.min(0))
            high = np.maximum(high, points.max(0))
        extent = max(float((high - low).max()), 1e-9)
        grid = 48
        while True:
            unique = set()
            overflow = False
            for offset in range(0, count, CHUNK):
                source_faces = array(faces[offset:offset + CHUNK]).astype(np.int64, copy=False)
                points = array(vertices[source_faces.reshape(-1)]).reshape(-1, 3, 3)
                cells = np.clip(np.rint((points-low) / extent * grid), 0, grid).astype(np.int64)
                width = grid + 1
                keys = cells[..., 0] + width*cells[..., 1] + width*width*cells[..., 2]
                keys = keys[(keys[:, 0] != keys[:, 1]) & (keys[:, 1] != keys[:, 2]) & (keys[:, 0] != keys[:, 2])]
                # Preserve winding; canonicalize by rotating the smallest index first.
                order = keys.argmin(1)
                keys = np.take_along_axis(keys, (np.arange(3)[None, :] + order[:, None]) % 3, axis=1)
                unique.update(map(tuple, np.unique(keys, axis=0).tolist()))
                if len(unique) > MAX_FACES:
                    overflow = True
                    break
            if not overflow:
                break
            grid //= 2
            if grid < 1:
                raise ValueError("Display proxy exceeds its face budget")
        if not unique:
            raise ValueError("Display proxy has no triangles")
        f = np.array(sorted(unique), dtype=np.int64)
        ids, flat = np.unique(f, return_inverse=True)
        f = flat.reshape(-1, 3)
        # Average original vertices per cell rather than snapping the surface to
        # cube centers. This keeps real planes/slopes on low-cost display meshes.
        sums = np.zeros((width**3, 3), dtype=np.float64)
        counts = np.zeros(width**3, dtype=np.int64)
        for offset in range(0, vcount, CHUNK):
            points = array(vertices[offset:offset + CHUNK])
            cells = np.clip(np.rint((points-low) / extent * grid), 0, grid).astype(np.int64)
            keys = cells[:, 0] + width*cells[:, 1] + width*width*cells[:, 2]
            np.add.at(sums, keys, points)
            np.add.at(counts, keys, 1)
        v = (sums[ids] / counts[ids, None]).astype(np.float32)
    texture = None
    if base_color is not None:
        if uv is None:
            raise ValueError("Textured milestone requires the same mesh's UVs")
        texture = array(first(base_color), copy=True).astype(np.float32)
        if texture.shape[-1] == 3:
            texture = np.concatenate([texture, np.ones((*texture.shape[:2], 1), dtype=np.float32)], axis=-1)
    primitive = dict(vertices=v, faces=f, uv=uv, normals=normals, texture=texture,
                     colors=None, factor=np.array([.65, .65, .65, 1.] if texture is None else [1., 1., 1., 1.]),
                     sampler={}, alpha_mode="OPAQUE", alpha_cutoff=.5, double_sided=True)
    return primitive, count


def render_structure(voxel, size, model_frame="y_up"):
    values = array(voxel.data)
    if values.ndim >= 4:
        values = values[0]
    if values.ndim == 3:
        points = np.argwhere(values > .5).astype(np.float32)
    elif values.ndim == 2 and values.shape[1] in (3, 4):
        points = values[values[:, 0] == 0, 1:] if values.shape[1] == 4 else values
    else:
        raise ValueError("Expected dense occupancy or sparse spatial coordinates")
    if not len(points):
        raise ValueError("Structure is empty")
    if model_frame == "z_up":
        points = np.column_stack([points[:, 0], points[:, 2], -points[:, 1]])
    # Same orthographic yaw/elevation as all mesh milestones.
    yaw, elevation = np.deg2rad([45, 15])
    camera = np.array([np.sin(yaw)*np.cos(elevation), np.sin(elevation), np.cos(yaw)*np.cos(elevation)])
    right = np.cross([0., 1., 0.], camera)
    right /= np.linalg.norm(right)
    up = np.cross(camera, right)
    projected = (points - (points.min(0)+points.max(0))/2) @ np.column_stack([right, up, camera])
    low, high = projected[:, :2].min(0), projected[:, :2].max(0)
    extent = high-low
    width, height = _renderer().frame_dimensions(extent, size)
    scale = min(width/extent[0], height/extent[1])*.82
    xy = (projected[:, :2] - (low+high)/2) * scale
    xy[:, 0] += width/2
    xy[:, 1] = height/2-xy[:, 1]
    image = Image.new("RGB", (width, height), (227, 230, 235))
    draw = ImageDraw.Draw(image)
    radius = max(1., min(3., scale*.55))
    depth = projected[:, 2]
    span = max(float(np.ptp(depth)), 1e-9)
    nearest = float(depth.min())
    # Upper bound matters if fed a later, dense voxel volume by mistake.
    order = np.argsort(depth)
    order = order[::max(1, int(np.ceil(len(order)/32768)))]
    for i in order:
        shade = int(95 + 65*(depth[i]-nearest)/span)
        x, y = xy[i]
        draw.ellipse((x-radius, y-radius, x+radius, y+radius), fill=(shade, shade+5, shade+8))
    return image


def render_glb_preview(sourcePath, outPath, size=256, yaw=45, elevation=15, zoom=1.0):
    """Render a saved result/history GLB with the same bounded CPU display path."""
    if type(size) is not int or size not in SIZES:
        raise ValueError("Preview size must be 128, 256 or 512")
    started = time.monotonic()
    renderer = _renderer()
    document, binary, digest = renderer.read_glb(Path(sourcePath))
    primitives, _ = renderer.load_scene(document, binary)
    total = sum(len(p['faces']) for p in primitives)
    image, report = renderer.render(primitives, size=size, supersample=1, yaw=yaw, elevation=elevation, zoom=zoom)
    output = Path(outPath).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    tmp = output.with_suffix(output.suffix + ".tmp")
    image.save(tmp, format="PNG")
    tmp.replace(output)
    report.update(source=str(Path(sourcePath).resolve()), source_sha256=digest, output=str(output),
                  sourceTriangles=total, displayTriangles=total, proxy=False,
                  seconds=round(time.monotonic()-started, 3))
    return report


@dataclass
class Work:
    directory: Path
    stage: str
    size: int
    source: object
    texture: object = None
    revision: int = 0
    model_frame: str = "y_up"
    done: threading.Event = field(default_factory=threading.Event, repr=False)
    published: bool = False


class PreviewWorker:
    def __init__(self, renderer=None):
        self.condition = threading.Condition()
        self.pending = None
        self.running = False
        self.thread = None
        self.sequence = 0
        self.latest = OrderedDict()
        self.renderer = renderer

    def submit(self, work):
        with self.condition:
            previous = self.latest.get(work.directory)
            if previous is None:
                # A resumed run can survive a server restart. Its saved newer
                # milestone must not be replaced by an earlier cached stage.
                try:
                    saved = json.loads((work.directory / "latest.json").read_text(encoding="utf-8"))
                    if saved.get("version") == 1 and saved.get("stage") in STAGES and type(saved.get("revision")) is int:
                        previous = (saved["revision"], STAGES[saved["stage"]])
                        self.sequence = max(self.sequence, saved["revision"])
                        self.latest[work.directory] = previous
                except (OSError, ValueError, TypeError):
                    pass
            if previous is not None and STAGES[work.stage] < previous[1]:
                return False
            self.sequence += 1
            work.revision = self.sequence
            self.latest[work.directory] = (work.revision, STAGES[work.stage])
            self.latest.move_to_end(work.directory)
            while len(self.latest) > 32:
                self.latest.popitem(last=False)
            if self.pending is not None:
                self.pending.done.set()
            self.pending = work
            if self.thread is None or not self.thread.is_alive():
                self.thread = threading.Thread(target=self._loop, name="Dart3s CPU preview", daemon=True)
                self.thread.start()
            self.condition.notify_all()
            return True

    def _current(self, work):
        return self.latest.get(work.directory, (None,))[0] == work.revision

    def _draw(self, work):
        if work.stage == "structure":
            return render_structure(work.source, work.size, work.model_frame), None
        primitive, count = mesh_snapshot(work.source, work.texture)
        if self.renderer is None:
            self.renderer = _renderer().render
        image, _ = self.renderer([primitive], size=work.size, supersample=1, yaw=45, elevation=15)
        return image, count

    def _loop(self):
        while True:
            with self.condition:
                if self.pending is None:
                    self.running = False
                    self.thread = None
                    self.condition.notify_all()
                    return
                work, self.pending = self.pending, None
                self.running = True
            try:
                image, triangles = self._draw(work)
                work.directory.mkdir(parents=True, exist_ok=True)
                # Revision-specific PNG keeps latest.json and image consistent even
                # while the client is reading the previous publication.
                output = work.directory / f"{work.stage}-{work.revision}.png"
                temporary = output.with_suffix(".tmp")
                image.save(temporary, format="PNG")
                with self.condition:
                    if not self._current(work):
                        temporary.unlink(missing_ok=True)
                        continue
                    temporary.replace(output)
                    metadata = dict(version=1, revision=work.revision, stage=work.stage,
                                    image=str(output), triangles=triangles, size=work.size,
                                    width=image.width, height=image.height,
                                    created_at=datetime.now(timezone.utc).isoformat())
                    json_tmp = work.directory / "latest.json.tmp"
                    json_tmp.write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")
                    json_tmp.replace(work.directory / "latest.json")
                    work.published = True
                    # Keep only the newest image for each milestone; at most four.
                    for older in work.directory.glob(f"{work.stage}-*.png"):
                        if older != output:
                            older.unlink(missing_ok=True)
            except Exception:
                LOG.exception("Nonfatal %s progress-preview failure", work.stage)
            finally:
                work.done.set()
                work = None

    def wait_idle(self, timeout=30):
        deadline = time.monotonic() + timeout
        with self.condition:
            while self.pending is not None or self.running:
                remaining = deadline-time.monotonic()
                if remaining <= 0:
                    return False
                self.condition.wait(remaining)
        return True


WORKER = PreviewWorker()


def submit_preview(stage, source, preview_dir, preview_size=256, *, texture=None, model_frame="y_up",
                   wait_for_publication=False, publication_timeout=10):
    """A preview failure must never fail the paid-for generation path."""
    try:
        directory = preview_path(preview_dir)
        if directory is None:
            return False
        if type(preview_size) is not int or preview_size not in SIZES:
            raise ValueError("Preview size must be 128, 256 or 512")
        work = Work(directory, stage, preview_size, source, texture, model_frame=model_frame)
        accepted = WORKER.submit(work)
        if accepted and wait_for_publication:
            work.done.wait(publication_timeout)
            if not work.published:
                LOG.warning("%s preview was not published within its bounded wait", stage)
            return work.published
        return accepted
    except Exception:
        LOG.exception("Nonfatal progress-preview submission failure")
        return False
