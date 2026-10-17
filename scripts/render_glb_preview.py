"""Deterministic CPU preview of GLB 2.0 triangle meshes; no GPU or new packages.

Loads actual scene transforms and embedded base-color textures. This is a
preview renderer, not a full glTF PBR implementation (see the saved report).
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import math
from pathlib import Path
import struct
import time

import numpy as np
from PIL import Image


COMPONENTS = {5120: np.int8, 5121: np.uint8, 5122: np.int16,
              5123: np.uint16, 5125: np.uint32, 5126: np.float32}
WIDTHS = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4,
          "MAT2": 4, "MAT3": 9, "MAT4": 16}


def read_glb(path: Path):
    data = path.read_bytes()
    if len(data) < 12:
        raise ValueError("Truncated GLB")
    magic, version, size = struct.unpack_from("<4sII", data)
    if magic != b"glTF" or version != 2 or size != len(data):
        raise ValueError("Expected a valid GLB 2.0 header")
    document, binary = None, None
    offset = 12
    while offset < size:
        count, kind = struct.unpack_from("<II", data, offset)
        payload = data[offset + 8:offset + 8 + count]
        if len(payload) != count:
            raise ValueError("Truncated GLB chunk")
        if kind == 0x4E4F534A:
            document = json.loads(payload)
        elif kind == 0x004E4942:
            binary = payload
        offset += count + 8
    if document is None or binary is None or offset != size:
        raise ValueError("GLB must contain JSON and binary chunks")
    return document, binary, hashlib.sha256(data).hexdigest()


def accessor(document, binary, index):
    spec = document["accessors"][index]
    dtype = np.dtype(COMPONENTS[spec["componentType"]]).newbyteorder("<")
    width = WIDTHS[spec["type"]]
    result = np.zeros((spec["count"], width), dtype=dtype)
    if "bufferView" in spec:
        view = document["bufferViews"][spec["bufferView"]]
        if view.get("buffer", 0) != 0:
            raise ValueError("External GLB buffers are unsupported")
        offset = view.get("byteOffset", 0) + spec.get("byteOffset", 0)
        stride = view.get("byteStride", width * dtype.itemsize)
        result = np.ndarray(result.shape, dtype=dtype, buffer=binary,
                            offset=offset, strides=(stride, dtype.itemsize)).copy()
    if "sparse" in spec:
        sparse = spec["sparse"]
        indices = sparse["indices"]
        iv = document["bufferViews"][indices["bufferView"]]
        ids = np.frombuffer(binary, dtype=COMPONENTS[indices["componentType"]],
                            count=sparse["count"],
                            offset=iv.get("byteOffset", 0) + indices.get("byteOffset", 0))
        values = sparse["values"]
        vv = document["bufferViews"][values["bufferView"]]
        vals = np.frombuffer(binary, dtype=dtype, count=sparse["count"] * width,
                            offset=vv.get("byteOffset", 0) + values.get("byteOffset", 0))
        result[ids] = vals.reshape(-1, width)
    if spec.get("normalized", False) and np.issubdtype(dtype, np.integer):
        info = np.iinfo(dtype)
        result = np.maximum(result.astype(np.float64) / info.max, -1.0)
    return result


def node_transform(node):
    if "matrix" in node:
        return np.asarray(node["matrix"], dtype=np.float64).reshape(4, 4).T
    x, y, z, w = node.get("rotation", [0, 0, 0, 1])
    rotation = np.array([
        [1 - 2*y*y - 2*z*z, 2*x*y - 2*z*w, 2*x*z + 2*y*w],
        [2*x*y + 2*z*w, 1 - 2*x*x - 2*z*z, 2*y*z - 2*x*w],
        [2*x*z - 2*y*w, 2*y*z + 2*x*w, 1 - 2*x*x - 2*y*y]])
    matrix = np.eye(4)
    matrix[:3, :3] = rotation @ np.diag(node.get("scale", [1, 1, 1]))
    matrix[:3, 3] = node.get("translation", [0, 0, 0])
    return matrix


def load_scene(document, binary):
    images = []
    for spec in document.get("images", []):
        if "bufferView" in spec:
            view = document["bufferViews"][spec["bufferView"]]
            start = view.get("byteOffset", 0)
            payload = binary[start:start + view["byteLength"]]
        elif spec.get("uri", "").startswith("data:"):
            payload = base64.b64decode(spec["uri"].split(",", 1)[1])
        else:
            raise ValueError("Only embedded GLB images are supported")
        with Image.open(io.BytesIO(payload)) as image:
            images.append(np.asarray(image.convert("RGBA"), dtype=np.float32) / 255)
    primitives = []
    nodes = document.get("nodes", [])
    roots = document.get("scenes", [{}])[document.get("scene", 0)].get("nodes")
    if roots is None:
        children = {child for node in nodes for child in node.get("children", [])}
        roots = [i for i in range(len(nodes)) if i not in children]

    def visit(index, parent, ancestry):
        if index in ancestry:
            raise ValueError("Cyclic GLB node hierarchy")
        node = nodes[index]
        matrix = parent @ node_transform(node)
        if "mesh" in node:
            for primitive in document["meshes"][node["mesh"]]["primitives"]:
                if primitive.get("mode", 4) != 4:
                    raise ValueError("Only triangle-list primitives are supported")
                attrs = primitive["attributes"]
                vertices = accessor(document, binary, attrs["POSITION"])
                vertices = vertices @ matrix[:3, :3].T + matrix[:3, 3]
                indices = (accessor(document, binary, primitive["indices"]).ravel()
                           if "indices" in primitive else np.arange(len(vertices)))
                if len(indices) % 3:
                    raise ValueError("Triangle-list indices are not divisible by three")
                material = document.get("materials", [])[primitive["material"]] if "material" in primitive else {}
                pbr = material.get("pbrMetallicRoughness", {})
                texspec = pbr.get("baseColorTexture")
                texture, uv, sampler = None, None, {}
                if texspec:
                    texture_spec = document["textures"][texspec["index"]]
                    texture = images[texture_spec["source"]]
                    sampler = document.get("samplers", [])[texture_spec["sampler"]] if "sampler" in texture_spec else {}
                    transform = texspec.get("extensions", {}).get("KHR_texture_transform", {})
                    uvkey = "TEXCOORD_" + str(transform.get("texCoord", texspec.get("texCoord", 0)))
                    if uvkey not in attrs:
                        raise ValueError("Base-color texture has no matching UV accessor")
                    uv = accessor(document, binary, attrs[uvkey]).astype(np.float64)
                    uv *= transform.get("scale", [1, 1])
                    angle = transform.get("rotation", 0)
                    uv = uv @ np.array([[math.cos(angle), -math.sin(angle)],
                                        [math.sin(angle), math.cos(angle)]]).T
                    uv += transform.get("offset", [0, 0])
                normals = None
                if "NORMAL" in attrs:
                    normals = accessor(document, binary, attrs["NORMAL"]) @ np.linalg.inv(matrix[:3, :3])
                    normals /= np.maximum(np.linalg.norm(normals, axis=1, keepdims=True), 1e-12)
                colors = accessor(document, binary, attrs["COLOR_0"]) if "COLOR_0" in attrs else None
                primitives.append(dict(vertices=vertices, faces=indices.reshape(-1, 3).astype(np.int64),
                                       normals=normals, colors=colors, uv=uv, texture=texture,
                                       factor=np.asarray(pbr.get("baseColorFactor", [1, 1, 1, 1]), dtype=np.float64),
                                       sampler=sampler, alpha_mode=material.get("alphaMode", "OPAQUE"),
                                       alpha_cutoff=material.get("alphaCutoff", .5),
                                       double_sided=material.get("doubleSided", False)))
        for child in node.get("children", []):
            visit(child, matrix, ancestry | {index})
    for root in roots:
        visit(root, np.eye(4), set())
    if not primitives or not any(len(p["faces"]) for p in primitives):
        raise ValueError("The scene contains no triangles")
    return primitives, images


def wrap(values, mode):
    if mode == 33071:
        return np.clip(values, 0, 1)
    if mode == 33648:
        return 1 - np.abs(np.mod(values, 2) - 1)
    return np.mod(values, 1)


def sample_texture(texture, uv, sampler):
    height, width = texture.shape[:2]
    u = wrap(uv[:, 0], sampler.get("wrapS", 10497))
    v = wrap(uv[:, 1], sampler.get("wrapT", 10497))
    # glTF uses the upper-left image corner for UV (0, 0).
    x, y = u * (width - 1), v * (height - 1)
    x0, y0 = np.floor(x).astype(int), np.floor(y).astype(int)
    x1, y1 = np.minimum(x0 + 1, width - 1), np.minimum(y0 + 1, height - 1)
    wx, wy = (x - x0)[:, None], (y - y0)[:, None]
    return ((texture[y0, x0] * (1-wx) + texture[y0, x1] * wx) * (1-wy)
            + (texture[y1, x0] * (1-wx) + texture[y1, x1] * wx) * wy)


def srgb_to_linear(rgb):
    return np.where(rgb <= .04045, rgb / 12.92, ((rgb + .055) / 1.055) ** 2.4)


def linear_to_srgb(rgb):
    return np.where(rgb <= .0031308, rgb * 12.92, 1.055 * np.maximum(rgb, 0) ** (1/2.4) - .055)


def frame_dimensions(extent, short_side):
    """Use one scale for both axes; the selected size is the shorter side."""
    width, height = map(float, extent)
    if not math.isfinite(width+height) or min(width, height) <= 1e-9:
        raise ValueError("Projected geometry has no two-dimensional area")
    if width >= height:
        return int(math.ceil(short_side*width/height)), int(short_side)
    return int(short_side), int(math.ceil(short_side*height/width))


def pixel_faces(screen, faces, width, height):
    """Cull subpixel triangles without changing geometry or sampled pixels."""
    kept = []
    for offset in range(0, len(faces), 100000):
        batch = faces[offset:offset+100000]
        xy = screen[batch]
        lo = np.maximum(np.ceil(xy.min(1)-.5-.001), 0)
        hi = np.minimum(np.floor(xy.max(1)-.5+.001), [width-1,height-1])
        valid = (lo<=hi).all(1)
        tiny = valid & ((hi-lo)<=1).all(1)
        ids = np.flatnonzero(tiny)
        covered = np.zeros(len(ids), dtype=bool)
        q = xy[ids]
        x0,y0 = q[:,0].T; x1,y1 = q[:,1].T; x2,y2 = q[:,2].T
        denominator = (y1-y2)*(x0-x2)+(x2-x1)*(y0-y2)
        safe = np.where(abs(denominator)>1e-12, denominator, 1)
        for dx,dy in ((0,0),(1,0),(0,1),(1,1)):
            xx,yy = lo[ids,0]+dx+.5,lo[ids,1]+dy+.5
            a = ((y1-y2)*(xx-x2)+(x2-x1)*(yy-y2))/safe
            b = ((y2-y0)*(xx-x2)+(x0-x2)*(yy-y2))/safe
            covered |= ((a>=-1e-7)&(b>=-1e-7)&(1-a-b>=-1e-7)&
                        (xx<=hi[ids,0]+.5)&(yy<=hi[ids,1]+.5)&(abs(denominator)>1e-12))
        valid[ids] = covered
        kept.append(batch[valid])
    return np.concatenate(kept) if kept else faces


def render(primitives, size=900, supersample=2, yaw=28, elevation=15, up="y", zoom=1.0, cull_subpixel=True):
    if not math.isfinite(zoom) or not .25 <= zoom <= 5:
        raise ValueError("Preview zoom must be between 0.25 and 5")
    all_vertices = np.vstack([p["vertices"] for p in primitives])
    low, high = all_vertices.min(0), all_vertices.max(0)
    center = (low + high) / 2
    yaw, elevation = np.deg2rad([yaw, elevation])
    camera = np.array([np.sin(yaw)*np.cos(elevation), np.sin(elevation),
                       np.cos(yaw)*np.cos(elevation)])
    world_up = np.array([0., 1., 0.])
    if up == "z":
        camera = camera[[0, 2, 1]]
        world_up = np.array([0., 0., 1.])
    right = np.cross(world_up, camera)
    right /= np.linalg.norm(right)
    upward = np.cross(camera, right)
    basis = np.column_stack([right, upward, camera])
    projected = (all_vertices - center) @ basis
    view_low, view_high = projected[:, :2].min(0), projected[:, :2].max(0)
    view_center = (view_low + view_high) / 2
    extent = view_high-view_low
    width, height = frame_dimensions(extent, size)
    pixel_width, pixel_height = width*supersample, height*supersample
    scale = min(pixel_width/extent[0], pixel_height/extent[1]) * .82 * zoom
    zbuffer = np.full((pixel_height, pixel_width), -np.inf, dtype=np.float32)
    background = np.asarray([.89, .90, .92], dtype=np.float32)
    framebuffer = np.broadcast_to(background, (pixel_height, pixel_width, 3)).copy()
    light = camera * .7 - right * .35 + upward * .65
    light /= np.linalg.norm(light)
    rasterized, covered = 0, 0
    for primitive in primitives:
        verts = (primitive["vertices"] - center) @ basis
        screen = verts[:, :2] - view_center
        screen[:, 0] = screen[:, 0] * scale + pixel_width / 2
        screen[:, 1] = pixel_height / 2 - screen[:, 1] * scale
        faces = pixel_faces(screen, primitive['faces'], pixel_width, pixel_height) if cull_subpixel else primitive['faces']
        for face in faces:
            points = screen[face]
            min_x, min_y = np.maximum(np.floor(points.min(0)).astype(int), 0)
            max_x, max_y = np.minimum(np.ceil(points.max(0)).astype(int), [pixel_width-1, pixel_height-1])
            if min_x > max_x or min_y > max_y:
                continue
            x0, y0 = points[0]; x1, y1 = points[1]; x2, y2 = points[2]
            denominator = (y1-y2)*(x0-x2) + (x2-x1)*(y0-y2)
            if abs(denominator) < 1e-12:
                continue
            yy, xx = np.mgrid[min_y:max_y+1, min_x:max_x+1]
            xx, yy = xx + .5, yy + .5
            a = ((y1-y2)*(xx-x2) + (x2-x1)*(yy-y2)) / denominator
            b = ((y2-y0)*(xx-x2) + (x0-x2)*(yy-y2)) / denominator
            c = 1 - a - b
            depth = a*verts[face[0], 2] + b*verts[face[1], 2] + c*verts[face[2], 2]
            region_z = zbuffer[min_y:max_y+1, min_x:max_x+1]
            selected = (a >= -1e-7) & (b >= -1e-7) & (c >= -1e-7) & (depth > region_z)
            if not selected.any():
                continue
            bary = np.column_stack([a[selected], b[selected], c[selected]])
            color = np.broadcast_to(primitive["factor"], (len(bary), 4)).copy()
            if primitive["texture"] is not None:
                texel = sample_texture(primitive["texture"], bary @ primitive["uv"][face], primitive["sampler"])
                color[:, :3] *= srgb_to_linear(texel[:, :3])
                color[:, 3] *= texel[:, 3]
            if primitive["colors"] is not None:
                vertex_colors = bary @ primitive["colors"][face]
                color[:, :3] *= vertex_colors[:, :3]
                if vertex_colors.shape[1] == 4:
                    color[:, 3] *= vertex_colors[:, 3]
            if primitive["normals"] is not None:
                normal = bary @ primitive["normals"][face]
            else:
                vertex = primitive["vertices"][face]
                normal = np.broadcast_to(np.cross(vertex[1]-vertex[0], vertex[2]-vertex[0]), (len(bary), 3)).copy()
            normal /= np.maximum(np.linalg.norm(normal, axis=1, keepdims=True), 1e-12)
            if primitive["double_sided"]:
                normal *= np.where(normal @ camera < 0, -1., 1.)[:, None]
            shade = .5 + .5*np.maximum(normal @ light, 0)
            rgb = np.clip(linear_to_srgb(color[:, :3] * shade[:, None]), 0, 1)
            alpha = np.ones(len(bary))
            if primitive["alpha_mode"] == "MASK":
                alpha = (color[:, 3] >= primitive["alpha_cutoff"]).astype(float)
            elif primitive["alpha_mode"] == "BLEND":
                alpha = np.clip(color[:, 3], 0, 1)
            final_mask = selected.copy()
            final_mask[selected] = alpha > 0
            keep = alpha > 0
            region = framebuffer[min_y:max_y+1, min_x:max_x+1]
            region[final_mask] = rgb[keep]*alpha[keep, None] + region[final_mask]*(1-alpha[keep, None])
            region_z[final_mask] = depth[final_mask]
            rasterized += 1
            covered += int(final_mask.sum())
    visible = int(np.isfinite(zbuffer).sum())
    if visible == 0:
        raise ValueError("No geometry is visible at this camera angle")
    image = Image.fromarray(np.rint(np.clip(framebuffer, 0, 1)*255).astype(np.uint8))
    if supersample != 1:
        image = image.resize((width, height), Image.Resampling.LANCZOS)
    return image, {"world_bounds": [low.tolist(), high.tolist()],
                   "triangles": sum(len(p["faces"]) for p in primitives),
                   "rasterized_triangles": rasterized, "visible_sample_pixels": visible,
                   "camera": {"yaw_degrees": float(np.rad2deg(yaw)),
                              "elevation_degrees": float(np.rad2deg(elevation)), "up": up, "zoom": zoom},
                   "projection": "orthographic", "size": [width, height], "short_side": size,
                   "supersample": supersample}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--size", type=int, default=900)
    parser.add_argument("--supersample", type=int, default=2)
    parser.add_argument("--yaw", type=float, default=28)
    parser.add_argument("--elevation", type=float, default=15)
    parser.add_argument("--up", choices=["y", "z"], default="y")
    args = parser.parse_args()
    if not 64 <= args.size <= 2048 or not 1 <= args.supersample <= 3:
        parser.error("size must be 64..2048 and supersample 1..3")
    start = time.monotonic()
    document, binary, digest = read_glb(args.file)
    primitives, images = load_scene(document, binary)
    image, report = render(primitives, args.size, args.supersample, args.yaw, args.elevation, args.up)
    output = (args.out or args.file.with_suffix(".preview.png")).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    image.save(output)
    report.update({"source": str(args.file.resolve()), "source_sha256": digest,
                   "output": str(output), "renderer": "numpy/Pillow CPU rasterizer",
                   "embedded_images": [{"size": [i.shape[1], i.shape[0]]} for i in images],
                   "textured_primitives": sum(p["texture"] is not None for p in primitives),
                   "seconds": round(time.monotonic()-start, 3),
                   "limitations": ["Simple diffuse lighting; metallic/roughness, normal, AO and emissive maps are not rendered.",
                                   "No animation or skinning; triangle lists only.",
                                   "Transparency is approximate; no shadows or perspective lens."]})
    report_path = (args.report or output.with_suffix(".json")).resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
