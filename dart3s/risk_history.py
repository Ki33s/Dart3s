"""Index existing completed executions on disk. No server or generation calls."""
import hashlib
import json
from pathlib import Path

from PIL import Image, ImageOps

PARAMETERS = ('model', 'quality', 'polygons', 'remesh_resolution', 'texture_size',
              'structure_seed', 'shape_seed', 'texture_seed', 'max_structure_elements', 'mesh_mode')


def fingerprint(path):
    with Image.open(path) as source:
        rgba = ImageOps.exif_transpose(source).convert('RGBA')
        background = Image.new('RGBA', rgba.size, 'white')
        background.alpha_composite(rgba)
        image = background.convert('RGB')
        image.thumbnail((128, 128), Image.Resampling.LANCZOS)
    return hashlib.sha256(str(image.size).encode() + image.tobytes()).hexdigest()


def build_index(project, output):
    project, output = Path(project), Path(output)
    try:
        previous = json.loads(output.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        previous = {}
    cache = previous.get('fingerprints', {})
    rows = []
    paths = sorted((project / 'outputs').rglob('generation.json'), key=lambda p: p.stat().st_mtime, reverse=True)[:300]
    for path in paths:
        if any(part in {'gui-integration-test','adaptive-verification','structure-budget-verification'} for part in path.parts):
            continue
        try:
            metadata = json.loads(path.read_text(encoding='utf-8'))
            if metadata.get('status') != 'completed':
                continue
            source = Path(metadata['image'])
            stat = source.stat()
            signature = f'{source}|{stat.st_size}|{stat.st_mtime_ns}'
            digest = cache.get(signature)
            if digest is None:
                digest = fingerprint(source)
                cache[signature] = digest
            histories = json.loads((path.parent / 'history.json').read_text(encoding='utf-8'))
            history = histories[metadata['prompt_id']]
            messages = history['status']['messages']
            starts = [event['timestamp'] for name, event in messages if name == 'execution_start']
            ends = [event['timestamp'] for name, event in messages if name == 'execution_success']
            cached = {str(node) for name, event in messages if name == 'execution_cached' for node in event.get('nodes', [])}
            # Cached neural stages do not measure a complete generation.
            if not starts or not ends or cached & {'3', '18', '23', '12'}:
                continue
            seconds = (ends[-1] - starts[0]) / 1000
            if seconds <= 0:
                continue
            rows.append(dict(fingerprint=digest, seconds=seconds, parameters=metadata['parameters'],
                             metadata_path=str(path), prompt_id=metadata['prompt_id']))
        except (OSError, ValueError, TypeError, KeyError):
            continue
    record = dict(version=1, rows=rows, fingerprints=cache)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix('.tmp')
    temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(output)
    return dict(index=str(output), executions=len(rows))


def matching_runs(path, index, parameters):
    if not index or parameters is None:
        return []
    digest = fingerprint(path)
    return [row for row in index.get('rows', []) if row.get('fingerprint') == digest
            and all(row.get('parameters', {}).get(key, {'max_structure_elements': 0, 'structure_retries': 2, 'mesh_mode':'processed'}.get(key))
                    == parameters.get(key, {'max_structure_elements': 0, 'structure_retries': 2, 'mesh_mode':'processed'}.get(key)) for key in PARAMETERS)]
