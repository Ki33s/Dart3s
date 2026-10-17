"""Bounded CPU preview of a huge untextured draft; original GLB stays intact."""
import json
from pathlib import Path
import time
import sys
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parent))
from render_glb_preview import read_glb, load_scene, render

ROOT = Path(__file__).resolve().parents[1]
path = ROOT / 'outputs/pyramid-draft/pyramid-base-512.glb'
output = path.with_suffix('.preview.png')
started = time.monotonic()
document, binary, digest = read_glb(path)
primitives, images = load_scene(document, binary)
assert not images, 'This lightweight path is for untextured draft geometry only'
original_triangles = sum(len(p['faces']) for p in primitives)
for primitive in primitives:
    vertices = primitive['vertices']
    low = vertices.min(0)
    width = max(float(np.max(vertices.max(0)-low)), 1e-9)
    cells = np.clip(np.floor((vertices-low)*(63/width)).astype(np.int32), 0, 63)
    keys = cells[:, 0] + 64*cells[:, 1] + 4096*cells[:, 2]
    _, inverse = np.unique(keys, return_inverse=True)
    counts = np.bincount(inverse)
    def average(values):
        return np.column_stack([np.bincount(inverse, weights=values[:, i])/counts
                                for i in range(values.shape[1])])
    primitive['vertices'] = average(vertices)
    faces = inverse[primitive['faces']]
    valid = (faces[:, 0] != faces[:, 1]) & (faces[:, 0] != faces[:, 2]) & (faces[:, 1] != faces[:, 2])
    faces = faces[valid]
    _, first = np.unique(np.sort(faces, axis=1), axis=0, return_index=True)
    primitive['faces'] = faces[np.sort(first)]
    if primitive['normals'] is not None:
        normals = average(primitive['normals'])
        primitive['normals'] = normals / np.maximum(np.linalg.norm(normals, axis=1, keepdims=True), 1e-12)
    if primitive['colors'] is not None:
        primitive['colors'] = average(primitive['colors'])
    primitive['uv'] = None
image, report = render(primitives, size=600, supersample=1)
image.save(output)
report.update(source=str(path), source_sha256=digest, original_triangles=original_triangles,
              preview_only_reduction='CPU vertex clustering, 64 cells per longest axis; original GLB unchanged',
              output=str(output), seconds=round(time.monotonic()-started, 2))
output.with_suffix('.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
print(json.dumps(report, indent=2))
