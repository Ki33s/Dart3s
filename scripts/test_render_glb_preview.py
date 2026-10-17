"""Synthetic fixture validation only; none of these files are generated models."""
import importlib.util
import io
import json
from pathlib import Path
import struct
import subprocess
import sys

import numpy as np
from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
LOGS = ROOT / "logs" / "renderer-synthetic-test"
LOGS.mkdir(parents=True, exist_ok=True)
module_spec = importlib.util.spec_from_file_location("renderer", ROOT / "scripts" / "render_glb_preview.py")
renderer = importlib.util.module_from_spec(module_spec)
module_spec.loader.exec_module(renderer)

binary = bytearray()
views, accessors = [], []


def add_view(payload):
    while len(binary) % 4:
        binary.append(0)
    views.append({"buffer": 0, "byteOffset": len(binary), "byteLength": len(payload)})
    binary.extend(payload)
    return len(views)-1


def add_accessor(values, kind, component=5126):
    dtype = "<f4" if component == 5126 else "<u2"
    array = np.asarray(values, dtype=dtype)
    accessors.append({"bufferView": add_view(array.tobytes()), "componentType": component,
                      "count": len(array), "type": kind})
    return len(accessors)-1


positions = add_accessor([[-.5, -.5, 0], [.5, -.5, 0], [.5, .5, 0], [-.5, .5, 0]], "VEC3")
normals = add_accessor([[0, 0, 1]]*4, "VEC3")
uvs = add_accessor([[0, 1], [1, 1], [1, 0], [0, 0]], "VEC2")
indices = add_accessor([0, 1, 2, 0, 2, 3], "SCALAR", 5123)
front = add_accessor([[-.16, -.16, .25], [.16, -.16, .25], [0, .16, .25]], "VEC3")
texture = np.empty((16, 16, 4), dtype=np.uint8)
texture[:8, :8] = [255, 0, 0, 255]
texture[:8, 8:] = [0, 255, 0, 255]
texture[8:, :8] = [0, 0, 255, 255]
texture[8:, 8:] = [255, 255, 0, 255]
png = io.BytesIO()
Image.fromarray(texture).save(png, format="PNG")
image_view = add_view(png.getvalue())
document = {
    "asset": {"version": "2.0", "generator": "SYNTHETIC RENDERER TEST ONLY"},
    "scene": 0, "scenes": [{"nodes": [0]}],
    "nodes": [{"translation": [2, 3, 4], "scale": [2, 2, 2], "children": [1]}, {"mesh": 0}],
    "buffers": [{"byteLength": len(binary)}], "bufferViews": views, "accessors": accessors,
    "images": [{"bufferView": image_view, "mimeType": "image/png"}],
    "textures": [{"source": 0, "sampler": 0}], "samplers": [{"wrapS": 33071, "wrapT": 33071}],
    "materials": [{"pbrMetallicRoughness": {"baseColorTexture": {"index": 0}}},
                  {"pbrMetallicRoughness": {"baseColorFactor": [1, 0, 1, 1]}}],
    "meshes": [{"primitives": [
        {"attributes": {"POSITION": positions, "NORMAL": normals, "TEXCOORD_0": uvs},
         "indices": indices, "material": 0},
        {"attributes": {"POSITION": front}, "material": 1}]}]}
payload = json.dumps(document).encode()
payload += b" " * (-len(payload) % 4)
binary.extend(b"\x00" * (-len(binary) % 4))
glb = struct.pack("<4sII", b"glTF", 2, 12 + 8+len(payload)+8+len(binary))
glb += struct.pack("<II", len(payload), 0x4E4F534A)+payload
glb += struct.pack("<II", len(binary), 0x004E4942)+binary
fixture = LOGS / "TEST-synthetic-textured-transform.glb"
fixture.write_bytes(glb)
loaded, loaded_binary, _ = renderer.read_glb(fixture)
primitives, images = renderer.load_scene(loaded, loaded_binary)
preview, report = renderer.render(primitives, size=256, supersample=1, yaw=0, elevation=0)
pixels = np.asarray(preview)
assert np.allclose(report["world_bounds"], [[1, 2, 4], [3, 4, 4.5]]), report
assert report["triangles"] == 3 and len(images) == 1
quarter_turn = renderer.node_transform({"rotation": [0, 0, 2**-.5, 2**-.5]})
assert np.allclose(quarter_turn @ [1, 0, 0, 1], [0, 1, 0, 1])
matrix_node = np.eye(4)
matrix_node[:3, 3] = [5, 6, 7]
assert np.allclose(renderer.node_transform({"matrix": matrix_node.T.ravel().tolist()}), matrix_node)
red, green, blue, yellow = pixels[60, 60], pixels[60, 195], pixels[195, 60], pixels[195, 195]
assert red[0] > 150 and red[1] < 20 and red[2] < 20, red
assert green[1] > 150 and green[0] < 20 and green[2] < 20, green
assert blue[2] > 150 and blue[0] < 20 and blue[1] < 20, blue
assert yellow[0] > 150 and yellow[1] > 150 and yellow[2] < 20, yellow
purple = pixels[130, 128]
assert purple[0] > 150 and purple[2] > 150 and purple[1] < 20, purple
process = subprocess.run([sys.executable, str(ROOT / "scripts" / "render_glb_preview.py"),
                          str(fixture), "--out", str(LOGS / "TEST-synthetic.preview.png"),
                          "--size", "256", "--yaw", "0", "--elevation", "0"],
                         capture_output=True, text=True, check=True)
cli_report = json.loads(process.stdout)
assert Path(cli_report["output"]).is_file() and cli_report["textured_primitives"] == 1
result = {"ok": True, "fixture": "SYNTHETIC TEST ONLY; not a generated model",
          "checks": ["embedded GLB texture", "scene parent transform", "quaternion/matrix transform", "UV orientation",
                     "four texture quadrants", "depth occlusion", "CLI PNG/report output"],
          "report": cli_report}
(LOGS / "validation.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
print(json.dumps(result, indent=2))
