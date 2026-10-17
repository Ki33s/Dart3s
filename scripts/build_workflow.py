"""Build a canvas-native ComfyUI workflow using validated CLI recipe operations.

The downloaded upstream template is immutable. All executable graph changes use
`comfy workflow apply`; only frontend favorites metadata is serialized here.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "downloads/template.original.json"
CLI = ROOT / ".venv-cli/Scripts/comfy.exe"


def dump(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def ancestors(workflow: dict, terminal: int) -> set[int]:
    parents: dict[int, list[int]] = {}
    for link in workflow["links"]:
        parents.setdefault(link[3], []).append(link[1])
    visited: set[int] = set()
    pending = [terminal]
    while pending:
        node = pending.pop()
        if node not in visited:
            visited.add(node)
            pending.extend(parents.get(node, []))
    return visited


def make_recipe(workflow: dict) -> dict:
    nodes = {n["id"]: n for n in workflow["nodes"]}
    needed = ancestors(workflow, 322)
    ops = []

    def widget(node, name, value):
        ops.append({"op": "set_widget", "node": str(node), "widget": name, "value": value})

    def title(node, value):
        ops.append({"op": "set_node_field", "node": str(node), "field": "title", "value": value})

    # Remove preview side effects, including previews embedded in the generation
    # path. Reconnect each downstream consumer to the real producer, then mute
    # the preview. GetMeshInfo's mesh passthrough is treated identically.
    passthrough = {"PreviewImage", "MaskPreview", "GetMeshInfo"}
    for node in workflow["nodes"]:
        nid = node["id"]
        if nid in needed and node["type"] in passthrough:
            incoming = next(l for l in workflow["links"] if l[3] == nid)
            producer = nodes[incoming[1]]
            source_slot = producer["outputs"][incoming[2]]["name"]
            for outgoing in workflow["links"]:
                if outgoing[1] != nid:
                    continue
                consumer = nodes[outgoing[3]]
                target_slot = consumer["inputs"][outgoing[4]]["name"]
                ops.append({"op": "connect", "from": f"{producer['id']}.{source_slot}",
                            "to": f"{consumer['id']}.{target_slot}"})
        if nid not in needed or node["type"] in passthrough:
            ops.append({"op": "set_node_field", "node": str(nid), "field": "mode", "value": 2})

    # A real backend primitive, shared by both geometry samplers, ensures that
    # changing the favorited seed can never make their seeds diverge.
    ops.append({"op": "add_node", "class_type": "PrimitiveInt", "as": "shape_seed", "at": [-2400, -500]})
    widget("shape_seed", "value", "${shape_seed}")
    title("shape_seed", "Shared geometry seed (both shape samplers)")
    for nid in (18, 23):
        widget(nid, "seed", "${shape_seed}")
        ops.append({"op": "connect", "from": "shape_seed.INT", "to": f"{nid}.seed"})

    widget(3, "seed", "${structure_seed}")
    title(3, "Structure seed")
    widget(12, "seed", "${texture_seed}")
    title(12, "Texture generation seed")
    widget(186, "target_face_count", "${polygons}")
    title(186, "Polygon count")
    widget(241, "resolution", "${remesh_resolution}")
    title(241, "Remesh resolution")
    widget(94, "target_resolution", 1024)
    widget(316, "value", "${use_trellis2}")
    title(316, "Model: false = Pixal3D, true = Trellis.2")
    widget(288, "value", "${texture_size}")
    title(288, "Texture size (square, all PBR maps)")
    for nid, name in ((147, "texture_size"), (196, "resolution"), (224, "resolution"), (233, "resolution")):
        widget(nid, name, "${texture_size}")
        ops.append({"op": "connect", "from": "288.INT", "to": f"{nid}.{name}"})
    widget(122, "image", "${image}")
    title(122, "Source image")
    defaults = {"image": ("string", "viking_wolf_rune_axe.png"), "polygons": ("int", 10000),
                "remesh_resolution": ("int", 256), "texture_size": ("int", 256),
                "structure_seed": ("int", 56), "shape_seed": ("int", 42),
                "texture_seed": ("int", 43), "use_trellis2": ("bool", False)}
    return {"recipe": "dart3s-image-to-3d", "params": {k: {"type": t, "default": v} for k, (t, v) in defaults.items()},
            "source": "https://github.com/Comfy-Org/workflow_templates/blob/main/templates/3d_pixal3d_trellis2_image_to_model.json",
            "ops": ops}


def preview_operations(directory: Path, size: int, model: str, quality: str = "standard") -> list[dict]:
    """Four observers reuse calculated objects; none creates geometry or textures."""
    ops = []
    for alias, cls, stage, source, target in (
        ("progress_structure", "Dart3sProgressVoxel", None, "119.voxel", "91.voxel"),
        ("progress_geometry", "Dart3sProgressMesh", "geometry", "92.mesh", "241.mesh"),
        ("progress_prepared", "Dart3sProgressMesh", "prepared", "186.mesh", "238.mesh"),
        ("progress_texture", "Dart3sProgressTexture", None, "147.base_color", "210.base_color"),
    ):
        slot = "voxel" if cls == "Dart3sProgressVoxel" else "base_color" if cls == "Dart3sProgressTexture" else "mesh"
        ops.extend([
            {"op": "add_node", "class_type": cls, "as": alias},
            {"op": "set_widget", "node": alias, "widget": "preview_dir", "value": str(directory)},
            {"op": "set_widget", "node": alias, "widget": "preview_size", "value": size},
            {"op": "connect", "from": source, "to": f"{alias}.{slot}"},
            {"op": "connect", "from": f"{alias}.{slot}", "to": target},
        ])
        if stage is not None:
            ops.append({"op": "set_widget", "node": alias, "widget": "stage", "value": stage})
        if cls == "Dart3sProgressVoxel":
            ops.append({"op": "set_widget", "node": alias, "widget": "model_frame",
                        "value": "z_up" if model == "trellis2" else "y_up"})
        if cls == "Dart3sProgressTexture":
            ops.append({"op": "connect", "from": "196.mesh", "to": f"{alias}.mesh"})
    # Merely observing 92.mesh on the Remesh branch does not order that branch
    # before texture sampling. Wait only for the decoded gray mesh: running GPU
    # Remesh here would evict inference weights immediately before textures.
    ops.extend([
        {"op": "add_node", "class_type": "Dart3sGeometryReady", "as": "geometry_ready"},
        {"op": "connect", "from": "18.LATENT" if quality == "fast" else "23.LATENT",
         "to": "geometry_ready.samples"},
        {"op": "connect", "from": "progress_geometry.mesh", "to": "geometry_ready.mesh"},
        {"op": "connect", "from": "geometry_ready.LATENT", "to": "98.shape_latent"},
    ])
    return ops


def fast_operations() -> list[dict]:
    """Reuse the 512 latent for both shape decoding and texture conditioning."""
    return [
        {"op": "connect", "from": "18.LATENT", "to": "92.samples"},
        {"op": "connect", "from": "18.LATENT", "to": "98.shape_latent"},
        {"op": "connect", "from": "91.positive", "to": "98.positive"},
        {"op": "connect", "from": "91.negative", "to": "98.negative"},
        *[{"op": "set_widget", "node": str(nid), "widget": "steps", "value": steps}
          for nid, steps in ((3, 6), (18, 8), (12, 6))],
        {"op": "set_widget", "node": "241", "widget": "smooth_iters", "value": 1},
        # Removing these optional bakers also disconnects the corresponding
        # material slots. The base color and metallic/roughness remain intact.
        {"op": "delete_node", "node": "224"},
        {"op": "delete_node", "node": "233"},
    ]


def original_mesh_operations() -> list[dict]:
    """Color the decoder's exact mesh; no remesh, decimation or UV processing."""
    return [
        {'op': 'add_node', 'class_type': 'PaintMesh', 'as': 'original_color'},
        {'op': 'connect', 'from': '92.mesh', 'to': 'original_color.mesh'},
        {'op': 'connect', 'from': '93.voxel_colors', 'to': 'original_color.voxel_colors'},
        {'op': 'connect', 'from': 'original_color.mesh', 'to': '285.mesh'},
    ]


def budget_operations(original: dict, args, quality: str, output: Path) -> list[dict]:
    """Use the original sampler's exact wiring and settings for structure retries."""
    nodes = {n['id']: n for n in original['nodes']}
    ops = [{'op': 'add_node', 'class_type': 'Dart3sStructureBudget', 'as': 'structure_budget'},
           {'op': 'add_node', 'class_type': 'PrimitiveInt', 'as': 'structure_seed_control'},
           {'op': 'set_widget', 'node': 'structure_seed_control', 'widget': 'value', 'value': args.structure_seed},
           {'op': 'connect', 'from': 'structure_seed_control.INT', 'to': '3.seed'},
           {'op': 'connect', 'from': 'structure_seed_control.INT', 'to': 'structure_budget.seed'}]
    def connect(source, target):
        ops.append({'op': 'connect', 'from': source, 'to': 'structure_budget.' + target})
    for target, nid, slot in [('model', 3, 'model'), ('positive', 3, 'positive'),
                              ('negative', 3, 'negative'), ('latent', 3, 'latent_image'), ('vae', 119, 'vae')]:
        entry = next(i for i in nodes[nid]['inputs'] if i['name'] == slot)
        link = next(l for l in original['links'] if l[0] == entry['link'])
        connect(f"{link[1]}.{nodes[link[1]]['outputs'][link[2]]['name']}", target)
    connect('119.voxel', 'voxel')
    report = output.with_name('structure-selection.json')
    if not report.is_relative_to((ROOT / 'outputs').resolve()):
        report = ROOT / 'outputs/structure-reports' / (output.stem + '-selection.json')
    values = {**nodes[3]['widgets_values_named'],
              'steps': 6 if quality == 'fast' else nodes[3]['widgets_values_named']['steps'],
              'resolution': nodes[119]['widgets_values_named']['resolution'],
              'max_elements': args.max_structure_elements, 'retries': args.structure_retries,
              'report_path': str(report)}
    values.pop('seed', None)  # linked to the same primitive as the original sampler
    values.pop('control_after_generate', None)
    for key, value in values.items():
        ops.append({'op': 'set_widget', 'node': 'structure_budget', 'widget': key, 'value': value})
    ops.append({'op': 'connect', 'from': 'structure_budget.voxel', 'to': '91.voxel'})
    return ops


def timing_operations(workflow: dict, output: Path) -> list[dict]:
    nodes = {n['id']:n for n in workflow['nodes']}
    directory = output.parent
    if not directory.is_relative_to((ROOT/'outputs').resolve()):
        directory = ROOT/'outputs/early-stage-reports'/output.stem
    ops = []
    for sampler in (3,18):
        alias = f'early_clock_{sampler}'
        ops += [{'op':'add_node','class_type':'Dart3sEarlyStage','as':alias},
                {'op':'set_widget','node':alias,'widget':'directory','value':str(directory)},
                {'op':'set_widget','node':alias,'widget':'phase','value':str(sampler)}]
        for target, slot in [('samples','latent_image'),('model','model'),('positive','positive'),('negative','negative')]:
            entry = next(i for i in nodes[sampler]['inputs'] if i['name']==slot)
            link = next(l for l in workflow['links'] if l[0]==entry['link'])
            source = f"{link[1]}.{nodes[link[1]]['outputs'][link[2]]['name']}"
            ops.append({'op':'connect','from':source,'to':f'{alias}.{target}'})
        ops.append({'op':'connect','from':f'{alias}.LATENT','to':f'{sampler}.latent_image'})
    return ops


def validate(workflow: dict, shared_seed_id: int, preview_enabled: bool = False,
             quality: str = "standard", structure_budget: bool = False, early_timing: bool = False,
             mesh_mode: str = 'processed') -> dict:
    active = ancestors(workflow, 322)
    nodes = {n["id"]: n for n in workflow["nodes"]}
    for nid in active:
        if nodes[nid].get("mode", 0) != 0:
            raise ValueError(f"Generation depends on disabled node {nid}")
        if nodes[nid]["type"].startswith("Preview") or nodes[nid]["type"] in {"MaskPreview", "GetMeshInfo"}:
            raise ValueError(f"Generation still depends on preview/info node {nid}")
    for nid in (18, 23):
        link_id = next(i["link"] for i in nodes[nid]["inputs"] if i["name"] == "seed")
        link = next(l for l in workflow["links"] if l[0] == link_id)
        if link[1] != shared_seed_id:
            raise ValueError("Geometry seeds are not fed by one shared primitive")
    for nid, input_name in ((147, "texture_size"), (196, "resolution"), (224, "resolution"), (233, "resolution")):
        if nid not in nodes:
            if quality != "fast":
                raise ValueError("Standard profile must preserve every PBR baker")
            continue
        link_id = next(i["link"] for i in nodes[nid]["inputs"] if i["name"] == input_name)
        if next(l for l in workflow["links"] if l[0] == link_id)[1] != 288:
            raise ValueError("PBR sizes are not controlled by the shared texture-size primitive")
    # The one active output is the actual GLB save. Mute every unused executable
    # node so later changes cannot accidentally submit preview work.
    unused_active = [n["id"] for n in workflow["nodes"] if n["id"] not in active and n.get("mode", 0) == 0]
    if unused_active:
        raise ValueError(f"Unused nodes remain enabled: {unused_active}")
    observers = {nid: n for nid, n in nodes.items() if n["type"].startswith("Dart3sProgress")}
    if len(observers) != (4 if preview_enabled else 0):
        raise ValueError("Expected exactly four progress observers when visual progress is enabled")
    gates = {nid: n for nid, n in nodes.items() if n["type"] == "Dart3sGeometryReady"}
    if len(gates) != (1 if preview_enabled else 0):
        raise ValueError("Expected one geometry-before-textures dependency when previews are enabled")
    clocks = {nid:n for nid,n in nodes.items() if n['type']=='Dart3sEarlyStage'}
    if len(clocks) != (2 if early_timing else 0):
        raise ValueError('Expected exactly two early stage clocks when early checking is enabled')
    if mesh_mode == 'processed' and quality == "standard" and len(active) != 48 + len(observers) + len(gates) + 2 * int(structure_budget) + len(clocks):
        raise ValueError("Visual progress must preserve the original 48 generation nodes")
    if quality == "fast":
        if {94, 23, 224, 233} & active:
            raise ValueError("Fast profile still calculates high-resolution refinement or optional maps")
        for nid, steps in ((3, 6), (18, 8), (12, 6)):
            if nodes[nid]["widgets_values_named"]["steps"] != steps:
                raise ValueError("Fast sampler budget was not applied")
    expected_paths = ((119, "voxel", 91, "voxel", "Dart3sProgressVoxel"),
                      (92, "mesh", 241, "mesh", "Dart3sProgressMesh"),
                      (186, "mesh", 238, "mesh", "Dart3sProgressMesh"),
                      (147, "base_color", 210, "base_color", "Dart3sProgressTexture"))

    def producer(nid: int, slot: str) -> tuple[int, str]:
        link_id = next(i["link"] for i in nodes[nid]["inputs"] if i["name"] == slot)
        link = next(l for l in workflow["links"] if l[0] == link_id)
        source = nodes[link[1]]
        return link[1], source["outputs"][link[2]]["name"]

    if mesh_mode == 'original':
        if {241, 186, 238, 196, 147, 210, 260, 224, 233} & active:
            raise ValueError('Original mesh must not run destructive postprocessing or UV bakers')
        color_id, slot = producer(285, 'mesh')
        if nodes[color_id]['type'] != 'PaintMesh' or slot != 'mesh':
            raise ValueError('Original export must use vertex colors')
        if producer(color_id, 'mesh') != (92, 'mesh') or producer(color_id, 'voxel_colors') != (93, 'voxel_colors'):
            raise ValueError('Original colors must reuse the exact decoded mesh and generated color field')

    if early_timing:
        for sampler, original in [(3,(87,'LATENT')),(18,(91,'LATENT'))]:
            clock_id, slot = producer(sampler,'latent_image')
            if clock_id not in clocks or slot!='LATENT' or producer(clock_id,'samples')!=original:
                raise ValueError('Clock must preserve the sampler latent')
            for name in ('model','positive','negative'):
                if producer(clock_id,name)!=producer(sampler,name):
                    raise ValueError('Clock must wait for the exact sampler inputs')

    if quality == "fast":
        for consumer, slot, source, output in ((92, "samples", 18, "LATENT"),
                *([] if preview_enabled else [(98, "shape_latent", 18, "LATENT")]), (98, "positive", 91, "positive"),
                (98, "negative", 91, "negative")):
            if producer(consumer, slot) != (source, output):
                raise ValueError("Fast texture and mesh must share the calculated 512 shape")

    if preview_enabled:
        gate_id = next(iter(gates))
        if producer(98, "shape_latent") != (gate_id, "LATENT"):
            raise ValueError("Textures must depend on the gray mesh publication")
        if producer(gate_id, "samples") != (18 if quality == "fast" else 23, "LATENT"):
            raise ValueError("Geometry dependency must preserve the original shape latent")
        geometry_id, output_name = producer(gate_id, "mesh")
        geometry = observers.get(geometry_id, {})
        geometry_stage = geometry.get("widgets_values_named", {}).get("stage")
        if geometry_stage is None:
            geometry_stage = next(iter(geometry.get("widgets_values", [])), None)
        if geometry.get("type") != "Dart3sProgressMesh" or geometry_stage != "geometry" or output_name != "mesh":
            raise ValueError("Texture dependency must wait for the decoded gray mesh")
        if 241 in ancestors(workflow, gate_id) or 186 in ancestors(workflow, gate_id):
            raise ValueError("Early gray publication must not move GPU postprocessing before textures")

    for source, slot, consumer, input_name, cls in expected_paths:
        if mesh_mode == 'original' and consumer != 91:
            continue
        incoming, output_name = producer(consumer, input_name)
        if structure_budget and consumer == 91 and input_name == 'voxel':
            if nodes[incoming]['type'] != 'Dart3sStructureBudget' or output_name != 'voxel':
                raise ValueError('Shape must depend on the structure budget')
            if producer(incoming, 'voxel') != (119, 'voxel'):
                raise ValueError('Budget must select from the original decoded structure')
            if producer(incoming, 'seed') != producer(3, 'seed'):
                raise ValueError('Initial structure and retries must share the same base seed')
            continue
        if preview_enabled:
            if incoming not in observers or observers[incoming]["type"] != cls or output_name != slot:
                raise ValueError(f"Incorrect progress observer before {consumer}.{input_name}")
            if producer(incoming, slot) != (source, slot):
                raise ValueError("Progress observer does not pass through the original calculated object")
            if cls == "Dart3sProgressTexture" and producer(incoming, "mesh") != (196, "mesh"):
                raise ValueError("Texture preview must reuse the unwrapped mesh")
        elif (incoming, output_name) != (source, slot):
            raise ValueError("Original generation wiring changed")
    return {"active_nodes": len(active), "disabled_nodes": len(nodes) - len(active),
            "terminal": 322, "geometry_seed_shared": True, "all_pbr_texture_sizes_shared": True,
            "progress_observers": len(observers), "geometry_before_textures": bool(gates), "quality": quality,
            "shape_resolution": 512 if quality == "fast" else 1024,
            "optional_pbr_bakers": quality == "standard", 'early_timing_nodes':len(clocks)}


def build(args: argparse.Namespace) -> dict:
    original = json.loads(SOURCE.read_text(encoding="utf-8"))
    quality = getattr(args, "quality", "standard")
    mesh_mode = getattr(args, 'mesh_mode', 'processed')
    if mesh_mode == 'original' and getattr(args, 'preview_directory', None):
        raise ValueError('Original mesh mode currently requires intermediate previews disabled')
    if quality not in ("standard", "fast"):
        raise ValueError("Quality must be standard or fast")
    args.max_structure_elements = getattr(args, 'max_structure_elements', 0)
    args.structure_retries = getattr(args, 'structure_retries', 2)
    if not 0 <= args.max_structure_elements <= 32768 or not 0 <= args.structure_retries <= 4:
        raise ValueError('Invalid structure budget or retry count')
    if args.max_structure_elements and getattr(args, 'preview_directory', None):
        raise ValueError('Structure budget currently requires intermediate previews disabled')
    recipe_path = ROOT / "workflows/image-to-3d.recipe.json"
    recipe = make_recipe(original)
    dump(recipe_path, recipe)
    preview_recipe_path = None
    preview_directory = getattr(args, "preview_directory", None)
    preview_size = getattr(args, "preview_size", 256)
    if preview_directory:
        directory = Path(preview_directory)
        if not directory.is_absolute():
            raise ValueError("Preview directory must be an absolute path")
        if preview_size not in (128, 256, 512):
            raise ValueError("Preview size must be 128, 256 or 512")
        directory = directory.resolve()
        directory.mkdir(parents=True, exist_ok=True)
        preview_recipe = {"recipe": "dart3s-progress-observers", "params": {},
                          "ops": preview_operations(directory, preview_size, args.model, quality)}
        recipe_output = Path(args.output).resolve()
        preview_recipe_path = recipe_output.with_name(recipe_output.stem + ".progress.recipe.json")
        dump(preview_recipe_path, preview_recipe)
    output = Path(args.output).resolve()
    if output == SOURCE.resolve():
        raise ValueError("Output cannot overwrite the upstream source template")
    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(SOURCE, output)
    params = {"image": args.image, "polygons": args.polygons, "remesh_resolution": args.remesh_resolution,
              "texture_size": args.texture_size, "structure_seed": args.structure_seed, "shape_seed": args.shape_seed,
              "texture_seed": args.texture_seed, "use_trellis2": args.model == "trellis2"}
    argv = [str(CLI), "--json", "workflow", "apply", str(output), "--ops", str(recipe_path)]
    for key, value in params.items():
        argv += ["--param", f"{key}={str(value).lower() if isinstance(value, bool) else value}"]
    if args.object_info:
        argv += ["--input", str(Path(args.object_info).resolve())]
    env = {**os.environ, "LOCALAPPDATA": str(ROOT / ".state"), "COMFY_NO_TELEMETRY": "1",
           "PYTHONUTF8": "1", "COMFY_WHERE": "local"}
    proc = subprocess.run(argv, cwd=ROOT, env=env, text=True, encoding="utf-8", capture_output=True,
                          creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    result = json.loads(proc.stdout)
    dump(output.with_suffix(".apply.json"), result)
    if not result.get("ok"):
        raise RuntimeError(json.dumps(result.get("error"), ensure_ascii=False))
    shared_seed_id = result["data"]["aliases"]["shape_seed"]
    def apply_batch(label: str, operations: list[dict]) -> dict:
        nonlocal result
        batch_path = output.with_name(output.stem + f".{label}.recipe.json")
        dump(batch_path, {"recipe": f"dart3s-{label}", "params": {}, "ops": operations})
        batch_argv = [str(CLI), "--json", "workflow", "apply", str(output), "--ops", str(batch_path),
                      "--base-version", str(result["data"]["version"])]
        if args.object_info:
            batch_argv += ["--input", str(Path(args.object_info).resolve())]
        process = subprocess.run(batch_argv, cwd=ROOT, env=env, text=True, encoding="utf-8", capture_output=True,
                                 creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        result = json.loads(process.stdout)
        dump(output.with_suffix(f".{label}.apply.json"), result)
        if not result.get("ok"):
            raise RuntimeError(json.dumps(result.get("error"), ensure_ascii=False))
        return result

    if quality == "fast":
        apply_batch("fast", fast_operations())
        projected = json.loads(output.read_text(encoding="utf-8"))
        required = ancestors(projected, 322)
        apply_batch("fast-mute", [{"op": "set_node_field", "node": str(n["id"]), "field": "mode", "value": 2}
                                  for n in projected["nodes"] if n["id"] not in required and n.get("mode", 0) == 0])
    if preview_recipe_path:
        # The CLI stamps each operation in a batch with one base_version. Its
        # LWW input registers can make two reconnects of the same slot compete
        # by random op id. Start a subsequent batch at the acknowledged version
        # so observers deterministically supersede the original preview bypass.
        progress_argv = [str(CLI), "--json", "workflow", "apply", str(output),
                         "--ops", str(preview_recipe_path), "--base-version", str(result["data"]["version"])]
        if args.object_info:
            progress_argv += ["--input", str(Path(args.object_info).resolve())]
        progress_proc = subprocess.run(progress_argv, cwd=ROOT, env=env, text=True, encoding="utf-8", capture_output=True,
                                       creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        progress_result = json.loads(progress_proc.stdout)
        dump(output.with_suffix(".progress.apply.json"), progress_result)
        if not progress_result.get("ok"):
            raise RuntimeError(json.dumps(progress_result.get("error"), ensure_ascii=False))
    if mesh_mode == 'original':
        apply_batch('original-mesh', original_mesh_operations())
        projected = json.loads(output.read_text(encoding='utf-8'))
        required = ancestors(projected, 322)
        apply_batch('original-mute', [
            {'op': 'set_node_field', 'node': str(n['id']), 'field': 'mode', 'value': 2}
            for n in projected['nodes'] if n['id'] not in required and n.get('mode', 0) == 0])
    budget_id = budget_seed_id = None
    if args.max_structure_elements:
        budget_aliases = apply_batch('structure-budget', budget_operations(original, args, quality, output))['data']['aliases']
        budget_id = budget_aliases['structure_budget']
        budget_seed_id = budget_aliases['structure_seed_control']
    early_timing = bool(getattr(args,'early_risk_seconds',0))
    if early_timing:
        apply_batch('early-timing', timing_operations(json.loads(output.read_text(encoding='utf-8')),output))
    workflow = json.loads(output.read_text(encoding="utf-8"))
    summary = validate(workflow, shared_seed_id, bool(preview_directory), quality, bool(args.max_structure_elements),early_timing,mesh_mode)
    summary.update(mesh_mode=mesh_mode, color_storage='vertex_colors' if mesh_mode=='original' else 'uv_textures',
                   optional_pbr_bakers=mesh_mode=='processed' and quality=='standard')
    summary.update(max_structure_elements=args.max_structure_elements, structure_retries=args.structure_retries,
                   structure_budget_node=budget_id)
    favorites = [(186, "target_face_count"), (3, "seed"), (shared_seed_id, "value"), (12, "seed"),
                 (241, "resolution"), (316, "value"), (288, "value"), (122, "image")]
    if mesh_mode == 'original':
        favorites = [item for item in favorites if item[0] not in {186,241,288}]
    if budget_id is not None:
        favorites = [(budget_seed_id, 'value') if item == (3, 'seed') else item for item in favorites]
        favorites.extend([(budget_id, 'max_elements'), (budget_id, 'retries')])
    # Frontend metadata only: exact official format from favoritedWidgetsStore.ts.
    workflow.setdefault("extra", {})["favoritedWidgets"] = {
        "favorites": [{"nodeLocatorId": str(nid), "widgetName": widget} for nid, widget in favorites]}
    dump(output, workflow)
    schema = {"parameters": {**params, "quality": quality, 'mesh_mode':mesh_mode}, "favorites": workflow["extra"]["favoritedWidgets"],
              "source_sha256": hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
              "shape_seed_node": shared_seed_id, "model_values": {"pixal3d": False, "trellis2": True},
              "favorites_source": "https://github.com/Comfy-Org/ComfyUI_frontend/blob/v1.49.6/src/stores/workspace/favoritedWidgetsStore.ts",
              "summary": summary}
    if preview_directory:
        schema.update(preview_directory=str(directory), preview_size=preview_size)
    dump(output.with_suffix(".parameters.json"), schema)
    built = {"workflow": str(output), "recipe": str(recipe_path), "favorites": len(favorites), "summary": summary}
    if preview_directory:
        built.update(preview_directory=str(directory), preview_size=preview_size,
                     preview_recipe=str(preview_recipe_path))
    return built


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--image", default="viking_wolf_rune_axe.png")
    p.add_argument("--model", choices=("pixal3d", "trellis2"), default="pixal3d")
    p.add_argument("--quality", choices=("standard", "fast"), default="standard")
    p.add_argument('--mesh-mode', choices=('processed','original'), default='processed')
    p.add_argument("--polygons", type=int, default=10000)
    p.add_argument("--remesh-resolution", type=int, default=256)
    p.add_argument("--texture-size", type=int, default=256)
    p.add_argument("--structure-seed", type=int, default=56)
    p.add_argument("--max-structure-elements", type=int, default=0)
    p.add_argument("--structure-retries", type=int, default=2)
    p.add_argument("--early-risk-seconds", type=int, default=0, help="Client-side adaptive budget; does not alter graph sampling")
    p.add_argument("--shape-seed", type=int, default=42)
    p.add_argument("--texture-seed", type=int, default=43)
    p.add_argument("--output", default=str(ROOT / "workflows/image-to-3d.json"))
    p.add_argument("--object-info", help="Optional real ComfyUI object_info dump for offline preparation")
    p.add_argument("--preview-directory", help="Absolute output directory for calculated-stage previews")
    p.add_argument("--preview-size", type=int, choices=(128, 256, 512), default=256)
    return p


if __name__ == "__main__":
    try:
        print(json.dumps(build(parser().parse_args()), ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)
