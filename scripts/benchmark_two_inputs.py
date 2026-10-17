"""Two sequential real CLI generations, with server-side node timestamps.

This script never starts/stops the server, changes GUI settings, or retries jobs.
An existing checkpoint prevents accidental duplicate submissions.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
from urllib.request import ProxyHandler, build_opener

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dart3s.comfy3d import generate_3d

REPORT = ROOT / "logs/two-input-benchmark"
OUTPUT = ROOT / "outputs/two-input-benchmark"
URL = "http://127.0.0.1:8188"
INPUTS = [
    ("boat", ROOT / ".state/gui/inputs/1350e4828fa84ef98e29287b9d395e33.png"),
    ("axe", ROOT / "assets/viking_wolf_rune_axe.png"),
]
PARAMETERS = dict(model="trellis2", quality="fast", polygons=1000,
                  remesh_resolution=128, texture_size=128, structure_seed=56,
                  shape_seed=42, texture_seed=42, visual_progress=True, preview_size=256)
GROUPS = {
    "model_load": "Загрузка моделей и настройка",
    "image": "Подготовка изображения и conditioning",
    "structure": "Генерация и декодирование структуры",
    "shape": "Генерация формы 512",
    "shape_decode": "Декодирование формы в сетку",
    "remesh": "Remesh 128",
    "decimate": "Уменьшение до 1000 полигонов",
    "mesh_uv": "Нормали и UV-развёртка",
    "texture": "Генерация текстур",
    "texture_decode": "Декодирование текстур",
    "bake": "Запекание и применение текстур",
    "export": "Сохранение GLB",
    "preview": "Передача данных промежуточного предпросмотра",
    "other": "Прочие служебные узлы",
    "overhead": "Подготовка выполнения и интервалы вне узлов",
}


def utc():
    return datetime.now(timezone.utc).isoformat()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def api(route):
    with build_opener(ProxyHandler({})).open(URL + route, timeout=10) as response:
        return json.load(response)


def require_idle():
    queue = api("/queue")
    if queue.get("queue_running") or queue.get("queue_pending"):
        raise RuntimeError("ComfyUI queue is occupied; benchmark did not submit another job")


def node_group(node_id, node):
    kind = node.get("class_type", node.get("type", "unknown"))
    if kind.startswith("Dart3sProgress"):
        return "preview"
    if kind == "KSampler":
        # These identities belong to the retained project recipe; titles alone
        # don't distinguish its unrenamed shape sampler. We only read them.
        return {"3": "structure", "18": "shape", "12": "texture"}.get(str(node_id), "other")
    if kind in {"VaeDecodeStructureTrellis2", "EmptyTrellis2LatentStructure"}:
        return "structure"
    if kind == "Trellis2ShapeStage":
        return "shape"
    if kind == "VaeDecodeShapeTrellis":
        return "shape_decode"
    if kind == "RemeshMesh":
        return "remesh"
    if kind == "DecimateMesh":
        return "decimate"
    if kind in {"MeshSmoothNormals", "UnwrapMesh"}:
        return "mesh_uv"
    if kind == "Trellis2TextureStage":
        return "texture"
    if kind == "VaeDecodeTextureTrellis":
        return "texture_decode"
    if kind in {"BakeTextureFromVoxel", "ApplyTextureToMesh"}:
        return "bake"
    if kind in {"MeshToFile3D", "Save3DAdvanced"}:
        return "export"
    if kind in {"UNETLoader", "VAELoader", "CLIPVisionLoader", "LoadMoGeModel",
                "LoadBackgroundRemovalModel", "CFGOverride", "RescaleCFG", "ModelSamplingSD3"}:
        return "model_load"
    if kind in {"LoadImage", "RemoveBackground", "ImageCropToMask", "MoGeInference",
                "MoGeGeometryToFOV", "Trellis2Conditioning", "Pixal3DConditioning"}:
        return "image"
    return "other"


def calculate_timings(events, graph, prompt_id):
    """Partition the server execution timeline without double counting.

    Lazy-switch nodes can execute repeatedly. Each boundary closes the previous
    chunk; cache hits are reported separately and contribute no execution time.
    """
    selected = [e for e in events if e.get("prompt_id") == prompt_id]
    start = next((e for e in selected if e["event"] == "execution_start"), None)
    terminal = next((e for e in selected if e["event"] in
                     {"execution_success", "execution_error", "execution_interrupted"}), None)
    if not start or not terminal or terminal["event"] != "execution_success":
        raise RuntimeError(f"Missing successful server timing envelope for {prompt_id}")
    chunks = []
    cached = set()
    current = None
    boundary = start
    for event in selected:
        when = event["monotonic"]
        if when < start["monotonic"] or when > terminal["monotonic"]:
            continue
        if event["event"] == "execution_cached":
            cached.update(str(n) for n in event.get("nodes", []))
        elif event["event"] == "executing":
            chunks.append(dict(node_id=current, seconds=when-boundary["monotonic"],
                               started_utc=boundary["utc"], finished_utc=event["utc"]))
            current = str(event["node"]) if event.get("node") is not None else None
            boundary = event
        elif event is terminal:
            chunks.append(dict(node_id=current, seconds=when-boundary["monotonic"],
                               started_utc=boundary["utc"], finished_utc=event["utc"]))
            break
    totals = defaultdict(float)
    rows = []
    aggregate = defaultdict(lambda: {"seconds": 0.0, "chunks": 0})
    for chunk in chunks:
        node_id = chunk["node_id"]
        node = graph.get(node_id, {}) if node_id is not None else {}
        group = node_group(node_id, node) if node_id is not None else "overhead"
        kind = node.get("class_type", "") if node_id is not None else "server_overhead"
        title = node.get("_meta", {}).get("title", kind)
        rows.append(dict(**chunk, class_type=kind, title=title, group=group))
        totals[group] += chunk["seconds"]
        if node_id is not None:
            aggregate[node_id]["seconds"] += chunk["seconds"]
            aggregate[node_id]["chunks"] += 1
    node_rows = []
    for node_id in sorted(set(aggregate) | cached, key=lambda n: int(n) if n.isdigit() else n):
        node = graph.get(node_id, {})
        entry = aggregate[node_id]
        node_rows.append(dict(node_id=node_id, class_type=node.get("class_type", "unknown"),
                              title=node.get("_meta", {}).get("title", node.get("class_type", "unknown")),
                              group=node_group(node_id, node), seconds=entry["seconds"],
                              execution_chunks=entry["chunks"], cached=node_id in cached))
    elapsed = terminal["monotonic"] - start["monotonic"]
    if abs(sum(totals.values()) - elapsed) > 1e-6:
        raise RuntimeError("Server timing partition does not reconcile")
    return dict(server_seconds=elapsed, started_utc=start["utc"], finished_utc=terminal["utc"],
                groups={g: totals[g] for g in GROUPS}, nodes=node_rows, chunks=rows,
                cached_node_ids=sorted(cached))


def read_server_events():
    path = REPORT / "server-events.ndjson"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def run_portable(script, *args):
    python = ROOT / "ComfyUI_windows_portable/python_embeded/python.exe"
    proc = subprocess.run([str(python), str(ROOT / "scripts" / script), *map(str, args)],
                          cwd=ROOT, capture_output=True, encoding="utf-8", timeout=120,
                          creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
    try:
        result = json.loads(proc.stdout)
    except ValueError:
        raise RuntimeError(f"{script}: {proc.stdout}\n{proc.stderr}")
    if proc.returncode or result.get("ok") is False:
        raise RuntimeError(f"{script}: {result}\n{proc.stderr}")
    return result


def history_total(record):
    stamps = {name: info.get("timestamp") for name, info in record.get("status", {}).get("messages", [])}
    if "execution_start" in stamps and "execution_success" in stamps:
        return (stamps["execution_success"] - stamps["execution_start"]) / 1000
    return None


def finish_reports(summary):
    results = summary.get("runs", [])
    all_nodes, all_chunks = [], []
    for run in results:
        if "result" not in run:
            continue
        result = run["result"]
        directory = Path(result["metadata_path"]).parent
        record = json.loads((directory / "history.json").read_text(encoding="utf-8"))[result["prompt_id"]]
        graph = record["prompt"][2]
        shape_seed_binding = graph["18"]["inputs"]["seed"]
        shape_seed = (graph[str(shape_seed_binding[0])]["inputs"]["value"]
                      if isinstance(shape_seed_binding, list) else shape_seed_binding)
        run["resolved_settings"] = dict(
            sampler_steps=[graph[n]["inputs"]["steps"] for n in ("3", "18", "12")],
            sampler_seeds=[graph["3"]["inputs"]["seed"], shape_seed, graph["12"]["inputs"]["seed"]],
            remesh_resolution=graph["241"]["inputs"]["resolution"],
            target_face_count=graph["186"]["inputs"]["target_face_count"],
            texture_size=graph["288"]["inputs"]["value"],
            detail_1024_present="23" in graph or "94" in graph)
        if run["resolved_settings"] != dict(sampler_steps=[6, 8, 6], sampler_seeds=[56, 42, 42],
                                            remesh_resolution=128, target_face_count=1000,
                                            texture_size=128, detail_1024_present=False):
            raise RuntimeError("Executed graph does not match the requested fast settings")
        timings = calculate_timings(read_server_events(), graph, result["prompt_id"])
        timings["history_seconds"] = history_total(record)
        if timings["history_seconds"] is not None:
            timings["history_difference_seconds"] = timings["server_seconds"] - timings["history_seconds"]
        run["timings"] = timings
        all_nodes.extend(dict(run=run["index"], input=run["name"], **row) for row in timings["nodes"])
        all_chunks.extend(dict(run=run["index"], input=run["name"], **row) for row in timings["chunks"])
    for filename, rows in [("timings.csv", all_nodes), ("timeline.csv", all_chunks)]:
        if rows:
            with (REPORT / filename).open("w", encoding="utf-8-sig", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
    restart = REPORT / "restart.json"
    if restart.exists():
        summary["restart"] = json.loads(restart.read_text(encoding="utf-8"))
    write_json(REPORT / "summary.json", summary)
    complete = [r for r in results if "timings" in r]
    lines = ["# Два последовательных запуска Trellis2 в быстром режиме", "",
             "Форма 512; 1000 полигонов; Remesh 128; текстуры 128×128; шаги 6/8/6. "
             "Seed структуры 56, формы 42, текстур 42. После одного перезапуска сначала лодка, затем топор.", "",
             "Время узлов измерено на сервере ComfyUI по границам execution_start/executing/execution_success. "
             "Повторные исполнения ленивых переключателей суммированы. Служебные интервалы выделены отдельно. "
             "Проверка истории сервера независима от часов клиента.", "",
             "| Этап | " + " | ".join(f"{r['index']}. {r['name']}, с" for r in complete) + " |",
             "| --- | " + " | ".join("---:" for _ in complete) + " |"]
    for group, label in GROUPS.items():
        lines.append("| " + label + " | " + " | ".join(f"{r['timings']['groups'][group]:.3f}" for r in complete) + " |")
    lines.append("| **Всего на сервере** | " + " | ".join(f"**{r['timings']['server_seconds']:.3f}**" for r in complete) + " |")
    lines.append("| Программа: загрузка картинки → скачанный GLB | " + " | ".join(f"{r['app_generation_seconds']:.3f}" for r in complete) + " |")
    lines += ["", "Первый запуск начинается с пустого кеша процесса. Второй может использовать загруженные модели и кеш "
              "постоянных узлов, но картинки различаются: всю разницу времени нельзя приписать прогреву. "
              "Асинхронный CPU-рендер предпросмотров может выполняться одновременно с генерацией; длительность узла "
              "наблюдения измеряет передачу данных, а не полный фоновый рендер.", ""]
    for run in complete:
        lines += [f"## {run['index']}. {run['name']}", "",
                  f"Prompt: `{run['result']['prompt_id']}`", "",
                  f"GLB: [{Path(run['result']['glb_path']).name}]({Path(run['result']['glb_path']).as_posix()})", "",
                  f"![Предпросмотр]({Path(run['preview_path']).as_posix()})", "",
                  f"Треугольников: {run['validation']['polygons']}; карты текстур: "
                  f"{', '.join(str(t['size']) for t in run['validation']['textures'])}. "
                  f"Исходная картинка не изменена: {run['input_unchanged']}.", "",
                  "Время этапов приложения (с): " + ", ".join(f"{p['status']} {p['seconds']:.3f}" for p in run["app_phases"]) + ".", ""]
    lines += ["Полный список узлов: [timings.csv](timings.csv). Все серверные интервалы: [timeline.csv](timeline.csv).", ""]
    (REPORT / "report.md").write_text("\n".join(lines), encoding="utf-8")


def run_benchmark():
    REPORT.mkdir(parents=True, exist_ok=True)
    checkpoint = REPORT / "summary.json"
    if checkpoint.exists():
        raise RuntimeError("Benchmark checkpoint already exists; refusing duplicate generations. Use --report-only to rebuild reports.")
    if not (REPORT / "restart.json").exists():
        # The optional server hook creates its event file at execution_start.
        # Root owns the verified restart; no timestamps are fabricated here.
        raise RuntimeError("Root restart record is missing; wait for restart/GO")
    fingerprints = [sha(path) for _, path in INPUTS]
    if len(set(fingerprints)) != 2:
        raise RuntimeError("Benchmark requires two distinct input images")
    require_idle()
    summary = dict(schema="dart3s-two-input-benchmark/1", status="running", started_at=utc(),
                   parameters=PARAMETERS, runs=[])
    write_json(checkpoint, summary)
    try:
        for index, ((name, image), fingerprint) in enumerate(zip(INPUTS, fingerprints), 1):
            require_idle()
            run = dict(index=index, name=name, input=str(image), input_sha256=fingerprint,
                       status="starting", started_at=utc(), events=[], app_phases=[])
            summary["runs"].append(run)
            write_json(checkpoint, summary)
            began = time.perf_counter()
            phase, phase_start = "starting", began

            def progress(event):
                nonlocal phase, phase_start
                now = time.perf_counter()
                status = event.get("status", "unknown")
                if status != phase:
                    run["app_phases"].append(dict(status=phase, seconds=now-phase_start))
                    phase, phase_start = status, now
                run.update(status=status, last_event_utc=utc())
                for key in ("metadata_path", "prompt_id", "preview_directory"):
                    if key in event:
                        run[key] = event[key]
                nested = event.get("event", {})
                # Keep a bounded readable checkpoint; raw client events are in
                # the adapter's own watch.stdout.log for later inspection.
                if status != "generating" or nested.get("type") in {"executing", "execution_start", "execution_success"}:
                    run["events"].append(dict(utc=utc(), status=status, event=event))
                write_json(checkpoint, summary)
                print(json.dumps(dict(run=index, input=name, **event), ensure_ascii=False), flush=True)

            result = generate_3d(image, **PARAMETERS, output_dir=OUTPUT / name,
                                 auto_start=False, wait_timeout=1200,
                                 object_info=ROOT / "logs/object_info-progress.json", progress=progress)
            run["app_generation_seconds"] = time.perf_counter()-began
            run["app_phases"].append(dict(status=phase, seconds=time.perf_counter()-phase_start))
            run["result"] = result.to_dict()
            run["validation"] = run_portable("verify_glb.py", result.glb_path, "--polygons", 1000, "--texture-size", 128)
            preview = result.glb_path.with_suffix(".preview.png")
            run["preview"] = run_portable("gui_preview_bridge.py", result.glb_path, "--out", preview, "--size", 256)
            run["preview_path"] = str(preview)
            run["input_unchanged"] = sha(image) == fingerprint
            if not run["input_unchanged"]:
                raise RuntimeError("Input image unexpectedly changed")
            run.update(status="completed", finished_at=utc())
            finish_reports(summary)
            print(json.dumps(dict(run=index, status="verified", result=result.to_dict(),
                                  preview_path=str(preview), server_seconds=run["timings"]["server_seconds"],
                                  validation=run["validation"]), ensure_ascii=False), flush=True)
        summary.update(status="completed", finished_at=utc())
        finish_reports(summary)
        return 0
    except Exception as exc:
        summary.update(status="failed", error=str(exc), stopped_at=utc())
        write_json(checkpoint, summary)
        print(json.dumps(dict(status="failed", error=str(exc)), ensure_ascii=False), flush=True)
        return 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="Submit exactly two real sequential generations")
    parser.add_argument("--report-only", action="store_true", help="Rebuild reports without submitting jobs")
    args = parser.parse_args()
    if args.run == args.report_only:
        parser.error("Choose --run or --report-only")
    if args.report_only:
        finish_reports(json.loads((REPORT / "summary.json").read_text(encoding="utf-8")))
        return 0
    return run_benchmark()


if __name__ == "__main__":
    raise SystemExit(main())
