"""Read-only geometric measurements, welding UV seam duplicates for edge counts."""
import argparse
import json
from pathlib import Path
import sys

import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parent))
from render_glb_preview import read_glb, load_scene


def inspect(path):
    document, binary, digest = read_glb(path)
    primitives, _ = load_scene(document, binary)
    result = {"file": str(path.resolve()), "sha256": digest, "primitives": []}
    for primitive in primitives:
        vertices = np.asarray(primitive["vertices"], dtype=np.float64)
        faces = np.asarray(primitive["faces"], dtype=np.int64)
        lo, hi = vertices.min(0), vertices.max(0)
        diagonal = float(np.linalg.norm(hi-lo))
        tolerance = max(diagonal * 1e-7, 1e-12)
        _, representative, inverse = np.unique(np.rint((vertices-lo)/tolerance).astype(np.int64),
            axis=0, return_index=True, return_inverse=True)
        welded = vertices[representative]
        f = inverse[faces]
        edges = np.concatenate([f[:, [0, 1]], f[:, [1, 2]], f[:, [2, 0]]])
        edges.sort(axis=1)
        unique, counts = np.unique(edges, axis=0, return_counts=True)
        boundary = unique[counts == 1]
        boundary_length = float(np.linalg.norm(welded[boundary[:, 0]]-welded[boundary[:, 1]], axis=1).sum())
        tri = vertices[faces]
        area = np.linalg.norm(np.cross(tri[:, 1]-tri[:, 0], tri[:, 2]-tri[:, 0]), axis=1)*0.5
        result["primitives"].append(dict(vertices=len(vertices), welded_vertices=len(welded),
            faces=len(faces), bounds=[lo.tolist(), hi.tolist()], extent=(hi-lo).tolist(),
            area=float(area.sum()), zero_area_faces=int((area < diagonal**2 * 1e-14).sum()),
            boundary_edges=len(boundary), boundary_length=boundary_length,
            nonmanifold_edges=int((counts > 2).sum()),
            signed_volume=float(np.einsum("ij,ij->i",tri[:,0],np.cross(tri[:,1],tri[:,2])).sum()/6),
            position_weld_tolerance=tolerance))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="+", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    output = [inspect(path) for path in args.files]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(json.dumps(output, indent=2))
