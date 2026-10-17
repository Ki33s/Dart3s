"""Render original triangles, culling only boxes containing no pixel samples.

Unlike the application's bounded proxy, this does not cluster vertices or
simplify topology. All lighting, framing and rasterization use the established
CPU renderer. Intended only for this offline diagnostic.
"""
import argparse
import json
from pathlib import Path
import sys
import time

import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parent))
from render_glb_preview import read_glb, load_scene, render, frame_dimensions


def render_full(source, output, size=256):
    started=time.monotonic()
    document,binary,digest=read_glb(source)
    primitives,_=load_scene(document,binary)
    vertices=np.vstack([p['vertices'] for p in primitives])
    center=(vertices.min(0)+vertices.max(0))/2
    yaw,elevation=np.deg2rad([45,15])
    camera=np.array([np.sin(yaw)*np.cos(elevation),np.sin(elevation),np.cos(yaw)*np.cos(elevation)])
    right=np.cross([0.,1.,0.],camera);right/=np.linalg.norm(right)
    upward=np.cross(camera,right)
    basis=np.column_stack([right,upward,camera])
    projected=(vertices-center)@basis
    lo,hi=projected[:,:2].min(0),projected[:,:2].max(0)
    extent=hi-lo;view_center=(lo+hi)/2
    width,height=frame_dimensions(extent,size)
    scale=min(width/extent[0],height/extent[1])*.82
    full_count=sum(len(p['faces']) for p in primitives)
    filtered=[]
    for p in primitives:
        screen=((p['vertices']-center)@basis)[:,:2]-view_center
        screen[:,0]=screen[:,0]*scale+width/2
        screen[:,1]=height/2-screen[:,1]*scale
        masks=[]
        for offset in range(0,len(p['faces']),100000):
            xy=screen[p['faces'][offset:offset+100000]]
            a=np.ceil(xy.min(1)-.5-.001)
            b=np.floor(xy.max(1)-.5+.001)
            a=np.maximum(a,0);b=np.minimum(b,[width-1,height-1])
            masks.append((a<=b).all(1))
        mask=np.concatenate(masks)
        filtered.append({**p,'faces':p['faces'][mask]})
    im,report=render(filtered,size=size,supersample=1,yaw=45,elevation=15)
    im.save(output)
    report.update(source=str(source),sha256=digest,input_triangles=full_count,
                  topology_simplified=False,seconds=round(time.monotonic()-started,3))
    output.with_suffix('.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('source',type=Path)
    p.add_argument('--out',type=Path,required=True)
    a=p.parse_args()
    print(json.dumps(render_full(a.source,a.out)),flush=True)
