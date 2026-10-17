"""Inspect the actual generated GLB geometry and embedded texture dimensions."""
import argparse
import io
import json
from pathlib import Path
import struct
from PIL import Image

parser = argparse.ArgumentParser()
parser.add_argument('file', type=Path)
parser.add_argument('--polygons', type=int, default=10000)
parser.add_argument('--texture-size', type=int, default=256)
args = parser.parse_args()
data = args.file.read_bytes()
magic, version, length = struct.unpack_from('<4sII', data)
if magic != b'glTF' or version != 2 or length != len(data):
    raise RuntimeError('Invalid GLB 2.0 header')
cursor = 12
document = None
binary = None
while cursor < length:
    size, kind = struct.unpack_from('<II', data, cursor)
    payload = data[cursor + 8:cursor + 8 + size]
    if kind == 0x4E4F534A:
        document = json.loads(payload)
    elif kind == 0x004E4942:
        binary = payload
    cursor += 8 + size
if document is None or binary is None or cursor != length:
    raise RuntimeError('Missing or malformed GLB chunks')
accessors = document['accessors']
primitives = [p for mesh in document.get('meshes', []) for p in mesh['primitives']]
triangles = 0
vertices = 0
for primitive in primitives:
    if primitive.get('mode', 4) != 4:
        raise RuntimeError('Expected triangle-list geometry')
    vertices += accessors[primitive['attributes']['POSITION']]['count']
    count = accessors[primitive['indices']]['count'] if 'indices' in primitive else accessors[primitive['attributes']['POSITION']]['count']
    if count % 3:
        raise RuntimeError('Non-triangular index count')
    triangles += count // 3
textures = []
for item in document.get('images', []):
    if 'bufferView' not in item:
        raise RuntimeError('Texture is not embedded in GLB')
    view = document['bufferViews'][item['bufferView']]
    offset = view.get('byteOffset', 0)
    payload = binary[offset:offset + view['byteLength']]
    with Image.open(io.BytesIO(payload)) as image:
        image.load()
        textures.append({'name': item.get('name'), 'size': list(image.size), 'format': image.format})
report = {'file': str(args.file.resolve()), 'bytes': len(data), 'vertices': vertices,
          'polygons': triangles, 'target_polygons': args.polygons, 'textures': textures,
          'materials': len(document.get('materials', []))}
report['ok'] = bool(triangles > 0 and triangles <= args.polygons and textures
                    and all(t['size'] == [args.texture_size, args.texture_size] for t in textures))
print(json.dumps(report, ensure_ascii=False, indent=2))
raise SystemExit(0 if report['ok'] else 1)
