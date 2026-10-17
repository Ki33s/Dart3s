"""Inspect one image on CPU. No ComfyUI, network, inference or image output."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dart3s.image_risk import analyze_image

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True)
    parser.add_argument('--history-index')
    parser.add_argument('--parameters')
    args = parser.parse_args()
    try:
        index = json.loads(Path(args.history_index).read_text(encoding='utf-8')) if args.history_index else None
        parameters = json.loads(args.parameters) if args.parameters else None
        print(json.dumps({'ok': True, 'data': analyze_image(args.source, history_index=index, parameters=parameters)}, ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({'ok': False, 'error': str(exc)}, ensure_ascii=False))
        raise SystemExit(1)
