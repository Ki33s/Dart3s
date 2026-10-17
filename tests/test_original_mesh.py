"""Original graph excludes destructive stages; display and copy preserve source."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/'scripts'))


class OriginalGraphTests(unittest.TestCase):
    def test_original_graph_only_paints_decoded_mesh_for_both_profiles(self):
        from comfy_cli.workflow_to_api import convert_ui_to_api
        catalog=ROOT/'logs/object_info-structure-budget.json'
        schema=json.loads(catalog.read_text(encoding='utf-8'))
        with tempfile.TemporaryDirectory(dir=ROOT/'logs') as folder:
            for quality in ('fast','standard'):
                target=Path(folder)/f'{quality}.json'
                proc=subprocess.run([sys.executable,str(ROOT/'scripts/build_workflow.py'),
                    '--quality',quality,'--mesh-mode','original','--model','trellis2',
                    '--object-info',str(catalog),'--output',str(target)],
                    cwd=ROOT,capture_output=True,encoding='utf-8',timeout=90,
                    env={**os.environ,'PYTHONUTF8':'1'})
                self.assertEqual(proc.returncode,0,proc.stderr)
                ui=json.loads(target.read_text(encoding='utf-8'))
                graph=convert_ui_to_api(ui,schema)
                forbidden={'RemeshMesh','DecimateMesh','UnwrapMesh','BakeTextureFromVoxel',
                           'ApplyTextureToMesh','MeshSmoothNormals','BakeNormalMap','BakeAmbientOcclusion'}
                self.assertFalse({n['class_type'] for n in graph.values()}&forbidden)
                paints=[(k,n) for k,n in graph.items() if n['class_type']=='PaintMesh']
                self.assertEqual(len(paints),1)
                paint_id,paint=paints[0]
                self.assertEqual(paint['inputs']['mesh'],['92',0])
                self.assertEqual(paint['inputs']['voxel_colors'],['93',0])
                self.assertEqual(graph['285']['inputs']['mesh'],[paint_id,0])
                self.assertEqual(sum(n['class_type']=='KSampler' for n in graph.values()),3 if quality=='fast' else 4)
                favorites={f['nodeLocatorId'] for f in ui['extra']['favoritedWidgets']['favorites']}
                self.assertFalse({'186','241','288'}&favorites)


@unittest.skipUnless(importlib.util.find_spec('numpy'), 'CPU mesh tests run in portable Python (NumPy)')
class OriginalMeshCpuTests(unittest.TestCase):
    def test_pixel_culling_is_identical_to_full_rasterization(self):
        import numpy as np
        from render_glb_preview import render
        rng=np.random.default_rng(17)
        centers=rng.uniform(-1,1,(1500,1,3))
        tiny=centers+rng.uniform(-.003,.003,(1500,3,3))
        big=rng.uniform(-1,1,(20,3,3))
        vertices=np.concatenate([tiny,big]).reshape(-1,3)
        scene=[dict(vertices=vertices,faces=np.arange(len(vertices)).reshape(-1,3),
                    normals=None,uv=None,texture=None,colors=rng.uniform(0,1,(len(vertices),4)),
                    factor=np.ones(4),sampler={},alpha_mode='OPAQUE',alpha_cutoff=.5,double_sided=True)]
        for yaw in (45,-30):
            expected,_=render(scene,size=64,supersample=1,yaw=yaw,cull_subpixel=False)
            actual,_=render(scene,size=64,supersample=1,yaw=yaw,cull_subpixel=True)
            self.assertTrue(np.array_equal(np.asarray(actual),np.asarray(expected)))

    def test_low_poly_is_separate_and_cannot_overwrite_original(self):
        import numpy as np
        from dart3s.low_poly import write_glb,make_copy
        from render_glb_preview import read_glb,load_scene
        n=40
        x,y=np.meshgrid(np.linspace(0,1,n),np.linspace(0,1,n))
        v=np.column_stack([x.ravel(),y.ravel(),(.1*np.sin(x*3)*np.sin(y*3)).ravel()])
        a=np.arange((n-1)*n).reshape(n-1,n)[:,:-1].ravel()
        f=np.concatenate([np.column_stack([a,a+1,a+n]),np.column_stack([a+1,a+n+1,a+n])])
        colors=np.ones((len(v),4));colors[:,:3]=[.1,.4,.7]
        with tempfile.TemporaryDirectory() as folder:
            source,output=Path(folder)/'original.glb',Path(folder)/'copy.glb'
            write_glb(source,v,f,colors)
            digest=hashlib.sha256(source.read_bytes()).hexdigest()
            report=make_copy(source,output,300)
            self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(),digest)
            self.assertLessEqual(report['triangles'],300)
            d,b,_=read_glb(output);parts,_=load_scene(d,b)
            self.assertIsNotNone(parts[0]['colors'])
            self.assertTrue(np.isfinite(parts[0]['vertices']).all())
            with self.assertRaises(ValueError):make_copy(source,source,300)
            self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(),digest)


if __name__=='__main__':unittest.main()
