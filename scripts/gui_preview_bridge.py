"""Render a bounded CPU GLB preview with the portable runtime's libraries."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dart3s.progress_preview import render_glb_preview

parser = argparse.ArgumentParser()
parser.add_argument('file', type=Path)
parser.add_argument('--out', type=Path, required=True)
parser.add_argument('--size', type=int, choices=[128, 256, 512], default=256)
parser.add_argument('--yaw', type=float, default=45)
parser.add_argument('--elevation', type=float, default=15)
parser.add_argument('--zoom', type=float, default=1)
args = parser.parse_args()
try:
    report = render_glb_preview(args.file, args.out, size=args.size, yaw=args.yaw,
                                elevation=args.elevation, zoom=args.zoom)
    print(json.dumps({'ok': True, 'data': report}, ensure_ascii=False))
except Exception as exc:
    print(json.dumps({'ok': False, 'error': str(exc)}, ensure_ascii=False))
    raise SystemExit(1)
