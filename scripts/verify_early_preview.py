"""One real fast axe generation to verify gray mesh previews precede texture.

--prepare only hashes the input. --run requires root GO and an instrumented
ready server; any existing run checkpoint prevents accidental resubmission.
Only this verifier's own logs and outputs are written.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dart3s.comfy3d import generate_3d
from dart3s.preview_watch import PreviewMonitor
from benchmark_two_inputs import api, run_portable, write_json
from benchmark_same_input import EXPECTED, resolve_settings

REPORT = ROOT / "logs/early-preview-verification"
OUTPUT = ROOT / "outputs/early-preview-verification"
SERVER_REPORT = REPORT
GEOMETRY_ONLY = False
SOURCE = ROOT / ".state/gui/inputs/861ee6ef6a804731adc3f0f38a6ff9f5.png"
PARAMETERS = dict(model="trellis2", quality="fast", polygons=10000,
                  remesh_resolution=256, texture_size=256, structure_seed=56,
                  shape_seed=42, texture_seed=42, visual_progress=True, preview_size=256)


def utc():
    return datetime.now(timezone.utc).isoformat()


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def prepare():
    REPORT.mkdir(parents=True, exist_ok=True)
    if (REPORT / "verification.json").exists():
        raise RuntimeError("Run checkpoint exists; refusing new generation")
    manifest = dict(status="prepared_waiting_root_GO", source=str(SOURCE), source_sha256=sha(SOURCE),
                    parameters=PARAMETERS, geometry_only=GEOMETRY_ONLY, prepared_at=utc())
    write_json(REPORT / "preparation.json", manifest)
    print(json.dumps(manifest), flush=True)


def timestamp(value):
    return datetime.fromisoformat(value).timestamp()


def verify_report(report):
    result = report["result"]
    directory = Path(result["metadata_path"]).parent
    history = json.loads((directory / "history.json").read_text(encoding="utf-8"))[result["prompt_id"]]
    graph = history["prompt"][2]
    report["resolved_settings"] = resolve_settings(graph)
    if report["resolved_settings"] != EXPECTED:
        raise RuntimeError("Executed settings differ from agreed fast axe parameters")
    events = [json.loads(line) for line in (SERVER_REPORT / "server-events.ndjson").read_text(encoding="utf-8").splitlines()
              if line.strip()]
    selected = [e for e in events if e.get("prompt_id") == result["prompt_id"]]
    begins = [e for e in selected if e["event"] == "execution_start"]
    ends = [e for e in selected if e["event"] == "execution_success"]
    if len(begins) != 1 or len(ends) != 1:
        raise RuntimeError("Expected exactly one successful instrumented execution")
    executed = [e for e in selected if e["event"] == "executing" and e.get("node") is not None]
    cached = {str(n) for e in selected if e["event"] == "execution_cached" for n in e.get("nodes", [])}
    geometry_only = report.get("geometry_only", False)
    if not geometry_only and cached & {"3", "18", "12"}:
        raise RuntimeError("A neural sampler was cached; this would not verify a real generation")
    report["sampler_execution_counts"] = {
        node: sum(str(event["node"]) == node for event in executed) for node in ("3", "18", "12")}
    required_real_samplers = ("18", "12") if geometry_only else ("3", "18", "12")
    if any(report["sampler_execution_counts"][node] != 1 for node in required_real_samplers):
        raise RuntimeError("Expected exactly one execution of each of the three samplers")
    for node in required_real_samplers:
        if not any(str(e["node"]) == node for e in executed):
            raise RuntimeError(f"Sampler {node} did not execute")
    report["cached_node_ids"] = sorted(cached)
    report["samplers_executed"] = [node for node, count in report["sampler_execution_counts"].items() if count > 0]
    report["samplers_cached"] = [node for node in ("3", "18", "12") if node in cached]
    report["complete_neural_generation"] = all(count == 1 for count in report["sampler_execution_counts"].values())
    observer_ids = {}
    for node, value in graph.items():
        if value["class_type"] == "Dart3sProgressMesh" and value["inputs"].get("stage") in {"geometry", "prepared"}:
            observer_ids[value["inputs"]["stage"]] = node
    gates = [n for n, value in graph.items() if value["class_type"] == "Dart3sGeometryReady"]
    if set(observer_ids) != {"geometry", "prepared"} or len(gates) != 1:
        raise RuntimeError("Expected geometry/prepared observers and one Dart3sGeometryReady gate")
    expected = (["92", observer_ids["geometry"], gates[0], "12"] if geometry_only else
                ["92", observer_ids["geometry"], "241", "186", observer_ids["prepared"], gates[0], "12"])
    positions = [next((i for i, e in enumerate(executed) if str(e["node"]) == node), -1) for node in expected]
    report["expected_execution_order"] = expected
    report["actual_execution_order"] = [str(e["node"]) for e in executed]
    report["execution_order_verified"] = all(i >= 0 for i in positions) and positions == sorted(positions)
    if not report["execution_order_verified"]:
        raise RuntimeError("Gray mesh stages/gate did not precede texture sampler in expected order")
    prepared_order = ["241", "186", observer_ids["prepared"], "147"]
    prepared_positions = [next((i for i, e in enumerate(executed) if str(e["node"]) == node), -1)
                          for node in prepared_order]
    report["prepared_before_bake_verified"] = all(i >= 0 for i in prepared_positions) and prepared_positions == sorted(prepared_positions)
    if not report["prepared_before_bake_verified"]:
        raise RuntimeError("Prepared mesh did not precede texture baking")
    if geometry_only and str(graph[gates[0]]["inputs"]["mesh"][0]) != observer_ids["geometry"]:
        raise RuntimeError("Geometry-only gate is unexpectedly bound to the prepared mesh")
    texture = next(e for e in executed if str(e["node"]) == "12")
    texture_epoch = timestamp(texture["utc"])
    bake = next(e for e in executed if str(e["node"]) == "147")
    bake_epoch = timestamp(bake["utc"])
    preview_directory = Path(report["preview_directory"])
    stage_publications = {}
    for stage in ("geometry", "prepared"):
        files = sorted(preview_directory.glob(f"{stage}-*.png"))
        if not files:
            raise RuntimeError(f"Missing {stage} PNG")
        image = files[-1]
        stage_publications[stage] = dict(image=str(image), png_mtime=image.stat().st_mtime,
                                          png_before_texture=image.stat().st_mtime < texture_epoch,
                                          png_before_bake=image.stat().st_mtime < bake_epoch)
    report["texture_started_utc"] = texture["utc"]
    report["stage_publications"] = stage_publications
    if not stage_publications["geometry"]["png_before_texture"]:
        raise RuntimeError("Raw gray preview was published after texture generation began")
    if geometry_only and not stage_publications["prepared"]["png_before_bake"]:
        raise RuntimeError("Prepared mesh PNG was published after texture baking began")
    if not geometry_only and not all(v["png_before_texture"] for v in stage_publications.values()):
        raise RuntimeError("A gray preview PNG was published after texture generation began")
    observations = report["gui_observations"]
    early = [r for r in observations if r.get("snapshot", {}).get("stage") in {"geometry", "prepared"}
             and timestamp(r["utc"]) < texture_epoch]
    report["gui_gray_observations_before_texture"] = early
    report["gui_early_gray_observed"] = bool(early)
    geometry_observations = [item for item in observations if item.get("snapshot", {}).get("stage") == "geometry"]
    geometry_delay = (min(timestamp(item["utc"]) for item in geometry_observations) - texture_epoch
                      if geometry_observations else None)
    report["gui_geometry_delay_from_texture_seconds"] = geometry_delay
    report["gui_geometry_observed_before_texture"] = geometry_delay is not None and geometry_delay < 0
    report["gui_geometry_observed_within_poll_window"] = geometry_delay is not None and geometry_delay <= 1.5
    if geometry_only and not report["gui_geometry_observed_within_poll_window"]:
        raise RuntimeError("Ordinary PreviewMonitor did not observe raw gray mesh within 1.5s of texture start")
    report["server_seconds"] = ends[0]["monotonic"] - begins[0]["monotonic"]
    start_epoch = timestamp(begins[0]["utc"])
    elapsed_milestones = {}
    for stage in ("structure", "geometry", "prepared"):
        observations = [item for item in report["publication_observations"]
                        if item.get("record", {}).get("stage") == stage]
        created = [item["record"].get("created_at") for item in observations
                   if item["record"].get("created_at")]
        if created:
            elapsed_milestones[stage] = min(timestamp(value) for value in created) - start_epoch
        else:
            images = list(preview_directory.glob(f"{stage}-*.png"))
            if images:
                elapsed_milestones[stage] = min(p.stat().st_mtime for p in images) - start_epoch
    elapsed_milestones["texture_started"] = texture["monotonic"] - begins[0]["monotonic"]
    elapsed_milestones["bake_started"] = bake["monotonic"] - begins[0]["monotonic"]
    elapsed_milestones["glb_saved_server"] = report["server_seconds"]
    report["elapsed_milestones_seconds"] = elapsed_milestones
    publication_durations = {}
    for stage, node in observer_ids.items():
        index = next(i for i, event in enumerate(executed) if str(event["node"]) == node)
        next_boundary = executed[index+1] if index+1 < len(executed) else ends[0]
        publication_durations[stage] = next_boundary["monotonic"] - executed[index]["monotonic"]
    report["cpu_publication_node_seconds"] = publication_durations
    sampler_durations = {}
    for node in ("3", "18", "12"):
        if node in cached:
            sampler_durations[node] = None
            continue
        index = next(i for i, event in enumerate(executed) if str(event["node"]) == node)
        sampler_durations[node] = executed[index+1]["monotonic"] - executed[index]["monotonic"]
    report["sampler_node_seconds"] = sampler_durations
    metadata = json.loads(Path(result["metadata_path"]).read_text(encoding="utf-8"))
    report["memory_cleanup"] = metadata.get("memory_cleanup")
    report["input_unchanged"] = sha(SOURCE) == report["source_sha256"]
    if not report["input_unchanged"]:
        raise RuntimeError("Source image changed")
    lines = ["# Реальная проверка ранних серых предпросмотров", "",
             f"Prompt: `{result['prompt_id']}`. Fast512, 10000, Remesh256, текстуры256, seeds56/42/42.", "",
             "Порядок выполнения: " + " → ".join(f"`{n}`" for n in expected) + ".", "",
             ("PNG сырой геометрии записана до sampler текстур; PNG подготовленной сетки — до запекания."
              if geometry_only else "PNG геометрии и подготовленной сетки записаны до sampler текстур."), "",
             f"Обычный GUI PreviewMonitor увидел серую сетку до текстур: {bool(early)}.", "",
             f"Сдвиг первого GUI-наблюдения сырой геометрии относительно начала текстур: {geometry_delay} с "
             "(отрицательный — раньше, положительный — после). "
             "Допуск штатного секундного опроса: не позднее 1,5 с после начала текстур.", "",
             f"Время сервера: {report['server_seconds']:.3f} с. "
             f"Реально выполненные sampler: {', '.join(report['samplers_executed'])}; "
             f"sampler из кеша: {', '.join(report['samplers_cached']) or 'нет'}. " +
             ("Общее время нельзя сравнивать с полным свежим расчётом: часть генерации взята из кеша."
              if not report["complete_neural_generation"] else "Полный свежий расчёт трёх sampler без кеша."), "",
             "| Событие | Секунд от начала выполнения |", "| --- | ---: |",
             *[f"| {stage} | {elapsed:.3f} |" for stage, elapsed in elapsed_milestones.items()], "",
             "Длительности узлов публикации с ожиданием CPU-рендера: " +
             ", ".join(f"{stage}: {seconds:.3f} с" for stage, seconds in publication_durations.items()) +
             ". Это время всего observer-узла, включая подготовку данных и ожидание публикации.", "",
             "Длительности sampler-узлов: " +
             ", ".join(f"{node}: {seconds:.3f} с" if seconds is not None else f"{node}: cached (null)"
                       for node, seconds in sampler_durations.items()) + ".", "",
             f"[GLB]({Path(result['glb_path']).as_posix()})", "",
             f"![Итог]({Path(report['final_preview_path']).as_posix()})", ""]
    (REPORT / "report.md").write_text("\n".join(lines), encoding="utf-8")


def run():
    checkpoint = REPORT / "verification.json"
    if checkpoint.exists():
        raise RuntimeError("Checkpoint exists: no automatic retry or second generation is allowed")
    prepared = json.loads((REPORT / "preparation.json").read_text(encoding="utf-8"))
    restart = json.loads((SERVER_REPORT / "restart.json").read_text(encoding="utf-8-sig"))
    if restart.get("ready") is not True:
        raise RuntimeError("Wait for root's instrumented server and GO")
    if sha(SOURCE) != prepared["source_sha256"]:
        raise RuntimeError("Source no longer matches preparation")
    queue = api("/queue")
    if queue.get("queue_running") or queue.get("queue_pending"):
        raise RuntimeError("Server queue is occupied; no submission")
    report = dict(status="starting", started_at=utc(), source=str(SOURCE),
                  source_sha256=prepared["source_sha256"], parameters=PARAMETERS,
                  geometry_only=GEOMETRY_ONLY,
                  gui_observations=[], progress_events=[], publication_observations=[])
    lock = threading.Lock()

    def save():
        with lock:
            write_json(checkpoint, report)

    def gui_emit(kind, **values):
        observation = dict(utc=utc(), kind=kind, **values)
        with lock:
            report["gui_observations"].append(observation)
            with (REPORT / "gui-observations.ndjson").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(observation) + "\n")

    monitor = PreviewMonitor(ROOT, gui_emit, "early-preview-verification")
    stopped = threading.Event()

    def publications():
        seen = set()
        while not stopped.wait(.1):
            directory = report.get("preview_directory")
            if not directory:
                continue
            for path in Path(directory).glob("*.json"):
                try:
                    record = json.loads(path.read_text(encoding="utf-8"))
                    if record.get("stage") not in {"structure", "geometry", "prepared", "textured"}:
                        continue
                    marker = (str(path), record.get("revision"), record.get("stage"))
                    if marker in seen:
                        continue
                    seen.add(marker)
                    item = dict(utc=utc(), manifest=str(path), manifest_mtime=path.stat().st_mtime, record=record)
                    with lock:
                        report["publication_observations"].append(item)
                        with (REPORT / "publication-observations.ndjson").open("a", encoding="utf-8") as stream:
                            stream.write(json.dumps(item) + "\n")
                except (OSError, ValueError, TypeError):
                    continue

    def progress(event):
        report.update(status=event.get("status", "unknown"), last_progress_at=utc())
        if event.get("preview_directory"):
            report["preview_directory"] = event["preview_directory"]
            monitor.configure(event["preview_directory"])
        for key in ("prompt_id", "metadata_path"):
            if event.get(key):
                report[key] = event[key]
        if event.get("event", {}).get("prompt_id"):
            report["prompt_id"] = event["event"]["prompt_id"]
        report["progress_events"].append(dict(utc=utc(), event=event))
        save()
        print(json.dumps(event, ensure_ascii=False), flush=True)

    save()  # Submission is guarded against accidental repeats from here on.
    monitor.start()
    collector = threading.Thread(target=publications, daemon=True)
    collector.start()
    try:
        result = generate_3d(SOURCE, **PARAMETERS, output_dir=OUTPUT, auto_start=False,
                             wait_timeout=1800, progress=progress)
        report["result"] = result.to_dict()
        save()
        report["glb_validation"] = run_portable("verify_glb.py", result.glb_path,
                                                 "--polygons", 10000, "--texture-size", 256)
        final_preview = result.glb_path.with_suffix(".preview.png")
        report["final_preview"] = run_portable("gui_preview_bridge.py", result.glb_path,
                                                 "--out", final_preview, "--size", 256)
        report["final_preview_path"] = str(final_preview)
        monitor.close()
        stopped.set()
        collector.join(timeout=2)
        verify_report(report)
        report.update(status="verified", finished_at=utc())
        save()
        print(json.dumps(report, ensure_ascii=False), flush=True)
        return 0
    except Exception as exc:
        report.update(status="failed", error=str(exc), finished_at=utc(),
                      no_retry=True, note="A submitted server job may still run; never resubmit automatically")
        save()
        raise
    finally:
        stopped.set()
        monitor.close()
        collector.join(timeout=2)


def main():
    global REPORT, OUTPUT, GEOMETRY_ONLY
    parser = argparse.ArgumentParser(description=__doc__)
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument("--prepare", action="store_true")
    choice.add_argument("--run", action="store_true")
    choice.add_argument("--report-only", action="store_true")
    parser.add_argument("--geometry-only", action="store_true",
                        help="Independent revised-wiring check, with shared server event log")
    args = parser.parse_args()
    if args.geometry_only:
        GEOMETRY_ONLY = True
        REPORT = ROOT / "logs/early-preview-geometry-only"
        OUTPUT = ROOT / "outputs/early-preview-geometry-only"
    if args.prepare:
        prepare()
        return 0
    if args.report_only:
        checkpoint = REPORT / "verification.json"
        report = json.loads(checkpoint.read_text(encoding="utf-8"))
        verify_report(report)
        write_json(checkpoint, report)
        print(json.dumps({"status": report["status"],
                          "elapsed_milestones_seconds": report["elapsed_milestones_seconds"],
                          "cpu_publication_node_seconds": report["cpu_publication_node_seconds"]}))
        return 0
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
