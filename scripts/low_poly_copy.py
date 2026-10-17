"""Create a separate guarded low poly GLB on CPU, no ComfyUI/model inference."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from dart3s.low_poly import make_copy
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('source',type=Path)
p.add_argument('--out',type=Path,required=True)
p.add_argument('--polygons',type=int,required=True)
a=p.parse_args()
try:
    print(json.dumps({'ok':True,'data':make_copy(a.source,a.out,a.polygons)},ensure_ascii=True))
except Exception as exc:
    print(json.dumps({'ok':False,'error':str(exc)},ensure_ascii=True))
    raise SystemExit(1)
