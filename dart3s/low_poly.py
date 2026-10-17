"""Optional CPU vertex clustering. Never modifies the source GLB."""
import hashlib
import json
from pathlib import Path
import struct

import numpy as np


def cluster(vertices, faces, colors, grid):
    low, high = vertices.min(0), vertices.max(0)
    cell_size = float((high-low).max())/grid
    cells = np.floor((vertices-low)/max(cell_size,1e-12)).astype(np.int64)
    width = grid+2
    keys = cells[:,0]+width*cells[:,1]+width*width*cells[:,2]
    _, inverse = np.unique(keys, return_inverse=True)
    count = np.bincount(inverse)
    v = np.column_stack([np.bincount(inverse,weights=vertices[:,i])/count for i in range(3)])
    c = np.column_stack([np.bincount(inverse,weights=colors[:,i])/count for i in range(4)])
    f = inverse[faces]
    valid = (f[:,0]!=f[:,1])&(f[:,1]!=f[:,2])&(f[:,2]!=f[:,0])
    f = f[valid]
    if len(f):
        # Keep winding while removing duplicate surviving triangles.
        _, ids = np.unique(np.sort(f,axis=1),axis=0,return_index=True)
        f = f[np.sort(ids)]
    return v, f, c, inverse, cell_size


def surface_area(v, f):
    total = 0.
    for start in range(0,len(f),100000):
        tri = v[f[start:start+100000]]
        total += np.linalg.norm(np.cross(tri[:,1]-tri[:,0],tri[:,2]-tri[:,0]),axis=1).sum()*.5
    return float(total)


def simplify(vertices, faces, colors, target):
    if target<4:
        raise ValueError('Полигонов должно быть не меньше 4')
    if len(faces)<=target:
        raise ValueError('В модели уже не больше заданного числа полигонов')
    left, right = 1, 8
    cache = {}
    def candidate(grid):
        if grid not in cache:
            cache[grid] = cluster(vertices,faces,colors,grid)
        return cache[grid]
    while len(candidate(right)[1])<target and right<512:
        left,right = right,right*2
    while right-left>1:
        middle = (left+right)//2
        if len(candidate(middle)[1])<=target:
            left=middle
        else:
            right=middle
    v,f,c,inverse,size = candidate(left)
    if len(f)<4:
        raise ValueError('Слишком мало полигонов для сохранения формы; оригинал оставлен')
    diagonal = np.linalg.norm(np.ptp(vertices,axis=0))
    displacement = float(np.linalg.norm(vertices-v[inverse],axis=1).max())
    area_before,area_after = surface_area(vertices,faces),surface_area(v,f)
    old_extent,new_extent = np.ptp(vertices,axis=0),np.ptp(v,axis=0)
    relevant = old_extent>diagonal*1e-4
    extent_ratio = new_extent[relevant]/old_extent[relevant]
    stats = dict(source_triangles=len(faces), triangles=len(f), grid=left,
                 max_vertex_displacement=displacement, diagonal=float(diagonal),
                 area_ratio=area_after/max(area_before,1e-12), extent_ratios=extent_ratio.tolist())
    if displacement>diagonal*.05 or np.any(extent_ratio<.9) or np.any(extent_ratio>1.1) or not .65<=stats['area_ratio']<=1.35:
        raise ValueError('Копия слишком меняет форму и не сохранена. Увеличьте число полигонов. Оригинал не изменён.')
    ids, remapped = np.unique(f,return_inverse=True)
    return v[ids],remapped.reshape(-1,3),c[ids],stats


def write_glb(path, vertices, faces, colors):
    """Minimal standard GLB with linear COLOR_0, without changing positions."""
    arrays = [np.asarray(vertices,dtype='<f4'), np.asarray(faces,dtype='<u4'), np.asarray(colors,dtype='<f4')]
    views, payload = [], bytearray()
    for index,array in enumerate(arrays):
        while len(payload)%4:payload.append(0)
        views.append(dict(buffer=0,byteOffset=len(payload),byteLength=array.nbytes,target=34963 if index==1 else 34962))
        payload.extend(array.tobytes())
    doc = dict(asset={'version':'2.0','generator':'Dart3s optional CPU low poly'},
               buffers=[{'byteLength':len(payload)}],bufferViews=views,
               accessors=[dict(bufferView=0,componentType=5126,count=len(vertices),type='VEC3',
                               min=arrays[0].min(0).tolist(),max=arrays[0].max(0).tolist()),
                          dict(bufferView=1,componentType=5125,count=faces.size,type='SCALAR'),
                          dict(bufferView=2,componentType=5126,count=len(vertices),type='VEC4')],
               materials=[dict(pbrMetallicRoughness={'baseColorFactor':[1,1,1,1],'metallicFactor':0,'roughnessFactor':1},doubleSided=True)],
               meshes=[{'primitives':[{'attributes':{'POSITION':0,'COLOR_0':2},'indices':1,'material':0}]}],
               nodes=[{'mesh':0}],scenes=[{'nodes':[0]}],scene=0)
    encoded=json.dumps(doc,separators=(',',':')).encode()
    encoded+=b' '*((-len(encoded))%4)
    payload+=b'\0'*((-len(payload))%4)
    data=struct.pack('<III',0x46546c67,2,28+len(encoded)+len(payload))
    data+=struct.pack('<II',len(encoded),0x4e4f534a)+encoded+struct.pack('<II',len(payload),0x004e4942)+payload
    path.write_bytes(data)


def make_copy(source, output, target):
    from scripts.render_glb_preview import read_glb,load_scene,sample_texture
    source,output=Path(source).resolve(),Path(output).resolve()
    if source==output or output.exists():
        raise ValueError('Нельзя перезаписывать исходную модель или существующую копию')
    doc,binary,digest=read_glb(source)
    primitives,_=load_scene(doc,binary)
    if len(primitives)!=1:
        raise ValueError('Для этой копии нужна модель с одной сеткой')
    p=primitives[0]
    v,f=p['vertices'],p['faces']
    colors=np.broadcast_to(p['factor'],(len(v),4)).copy()
    if p['colors'] is not None:
        colors[:,:p['colors'].shape[1]]*=p['colors']
    if p['texture'] is not None:
        colors*=sample_texture(p['texture'],p['uv'],p['sampler'])
    v,f,c,stats=simplify(v,f,colors,int(target))
    output.parent.mkdir(parents=True,exist_ok=True)
    temporary=output.with_suffix('.glb.tmp')
    try:
        write_glb(temporary,v,f,c)
        # Verify valid GLB and unchanged source before publishing the copy.
        read_glb(temporary)
        if hashlib.sha256(source.read_bytes()).hexdigest()!=digest:
            raise ValueError('Исходная модель изменилась во время обработки')
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    report=dict(source=str(source),source_sha256=digest,glb_path=str(output),**stats)
    output.with_suffix('.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    return report
