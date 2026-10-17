"""Prepare three mesh-stage exports with sanctioned CLI ops; never submits work."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from comfy_cli.workflow_to_api import convert_ui_to_api
from dart3s.comfy3d import local_environment

SOURCE = ROOT / "outputs/20261015T134344Z_3b4549b0/workflow.json"
SOURCE_PROMPT = "9304f9f0-c152-4aa7-bfac-f8c01dad3cd7"
DIRECTORY = ROOT / "outputs/stone-simplification-probe"
REPORT = ROOT / "logs/stone-simplification-probe"
CATALOG = ROOT / "logs/object_info-progress.json"
STAGES = {"raw": "92", "remesh": "241", "decimate": "186"}


def cli(arguments, log_name):
    result = subprocess.run([str(ROOT / ".venv-cli/Scripts/comfy.exe"), *arguments], cwd=ROOT,
                            env=local_environment(ROOT, "http://127.0.0.1:8188"),
                            capture_output=True, encoding="utf-8", timeout=90,
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    (REPORT / f"{log_name}.stdout.json").write_text(result.stdout, encoding="utf-8")
    (REPORT / f"{log_name}.stderr.log").write_text(result.stderr, encoding="utf-8")
    payload = json.loads(result.stdout)
    if result.returncode or not payload.get("ok"):
        raise RuntimeError(payload.get("error") or result.stderr)
    return payload


def main():
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    REPORT.mkdir(parents=True, exist_ok=True)
    if (REPORT / "extraction.json").exists():
        raise RuntimeError("Extraction checkpoint exists; refusing to overwrite a submitted probe")
    cli(["--json", "discover"], "discover")
    target = DIRECTORY / "workflow.json"
    shutil.copy2(SOURCE, target)
    ops = []
    for stage, node in STAGES.items():
        file_alias, save_alias = f"{stage}_file", f"{stage}_save"
        ops += [{"op": "add_node", "class_type": "MeshToFile3D", "as": file_alias},
                {"op": "add_node", "class_type": "Save3DAdvanced", "as": save_alias},
                {"op": "connect", "from": f"{node}.mesh", "to": f"{file_alias}.mesh"},
                {"op": "connect", "from": f"{file_alias}.model_3d", "to": f"{save_alias}.model_3d"},
                {"op": "set_widget", "node": save_alias, "widget": "filename_prefix",
                 "value": f"3d/stone-simplification-probe/{stage}"},
                {"op": "set_widget", "node": save_alias, "widget": "viewport_state", "value": ""},
                {"op": "set_widget", "node": save_alias, "widget": "width", "value": 256},
                {"op": "set_widget", "node": save_alias, "widget": "height", "value": 256}]
    recipe = DIRECTORY / "mesh-stage-exports.recipe.json"
    recipe.write_text(json.dumps({"recipe": "export-existing-stone-mesh-stages", "ops": ops}, indent=2), encoding="utf-8")
    applied = cli(["--json", "workflow", "apply", str(target), "--ops", str(recipe),
                   "--input", str(CATALOG), "--ack", "summary"], "apply")
    aliases = applied["data"]["aliases"]
    graph = json.loads(target.read_text(encoding="utf-8"))
    parents = {}
    for link in graph["links"]:
        parents.setdefault(link[3], []).append(link[1])
    saves = {stage: aliases[f"{stage}_save"] for stage in STAGES}
    needed, pending = set(), list(saves.values())
    while pending:
        node = pending.pop()
        if node not in needed:
            needed.add(node)
            pending.extend(parents.get(node, []))
    disable = [{"op": "set_node_field", "node": str(n["id"]), "field": "mode", "value": 2}
               for n in graph["nodes"] if n["id"] not in needed and n.get("mode", 0) != 2]
    disable_path = DIRECTORY / "disable-unused.ops.json"
    disable_path.write_text(json.dumps(disable, indent=2), encoding="utf-8")
    if disable:
        cli(["--json", "workflow", "apply", str(target), "--ops", str(disable_path),
             "--input", str(CATALOG), "--ack", "summary"], "disable")
    graph = json.loads(target.read_text(encoding="utf-8"))
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    compiled = convert_ui_to_api(graph, catalog)
    original = convert_ui_to_api(json.loads(SOURCE.read_text(encoding="utf-8")), catalog)
    new_ids = {str(i) for i in aliases.values()}
    retained = set(compiled) - new_ids
    if not all(compiled[n] == original[n] for n in retained):
        raise RuntimeError("An original ancestor changed; cache export is unsafe")
    if not set(STAGES.values()).issubset(retained):
        raise RuntimeError("Required mesh stages missing")
    if "12" in compiled or "23" in compiled or "94" in compiled:
        raise RuntimeError("Texture or high-resolution branches unexpectedly retained")
    compiled_path = DIRECTORY / "workflow.compiled.json"
    compiled_path.write_text(json.dumps(compiled, indent=2), encoding="utf-8")
    manifest = dict(status="prepared_not_submitted", source=str(SOURCE), source_prompt_id=SOURCE_PROMPT,
                    workflow=str(target), compiled=str(compiled_path), stage_nodes=STAGES,
                    save_nodes=saves, file_nodes={s: aliases[f"{s}_file"] for s in STAGES},
                    cached_ancestors_unchanged=True, retained_original_node_ids=sorted(retained),
                    neural_sampler_ids=[n for n in ("3", "18", "23", "12") if n in compiled],
                    node_count=len(compiled))
    (REPORT / "preparation.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
