"""Two real, sequential generations of byte-identical PNGs; never retries jobs.

--prepare performs no server calls and no generation. Root must explicitly give
GO, start the instrumented server and save restart.json before --run is used.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from benchmark_two_inputs import (GROUPS, api, calculate_timings, history_total,
                                  require_idle, run_portable, sha, utc, write_json)
from dart3s.comfy3d import generate_3d, resume_3d

REPORT = ROOT / "logs/repeated-input-benchmark"
OUTPUT = ROOT / "outputs/repeated-input-benchmark"
SOURCE = ROOT / ".state/gui/inputs/861ee6ef6a804731adc3f0f38a6ff9f5.png"
INPUTS = [REPORT / "inputs/axe_repeat_run1.png", REPORT / "inputs/axe_repeat_run2.png"]
PARAMETERS = dict(model="trellis2", quality="fast", polygons=10000,
                  remesh_resolution=256, texture_size=256, structure_seed=56,
                  shape_seed=42, texture_seed=42, visual_progress=True, preview_size=256)
EXPECTED = dict(sampler_steps=[6, 8, 6], sampler_seeds=[56, 42, 42],
                remesh_resolution=256, target_face_count=10000,
                texture_size=256, detail_1024_present=False)
LABELS = dict(GROUPS, remesh="Remesh 256", decimate="Уменьшение до 10 000 треугольников")
SAMPLERS = {"3", "18", "12"}


def prepare():
    fingerprint = sha(SOURCE)
    if (REPORT / "summary.json").exists():
        raise RuntimeError("Existing run checkpoint; will not overwrite or resubmit")
    for image in INPUTS:
        image.parent.mkdir(parents=True, exist_ok=True)
        if image.exists() and sha(image) != fingerprint:
            raise RuntimeError(f"Existing input has different bytes: {image}")
        if not image.exists():
            shutil.copyfile(SOURCE, image)
    manifest = dict(schema="dart3s-repeat-inputs/1", prepared_at=utc(),
                    source=str(SOURCE), source_sha256=fingerprint, parameters=PARAMETERS,
                    inputs=[dict(path=str(p), sha256=sha(p)) for p in INPUTS],
                    status="prepared_waiting_for_root_GO")
    if len({p.name for p in INPUTS}) != 2 or any(sha(p) != fingerprint for p in INPUTS):
        raise RuntimeError("Need distinct basenames and exactly identical source bytes")
    write_json(REPORT / "inputs.json", manifest)
    (REPORT / "report.md").write_text(
        "# Два последовательных запуска одной картинки\n\n"
        "Подготовлен топор; обе PNG-копии побайтно совпадают, имена различаются. "
        "Режим 512; 10 000 треугольников; Remesh 256; текстуры 256; "
        "шаги 6/8/6; seeds 56/42/42. Генерация ещё не запускалась.\n", encoding="utf-8")
    return manifest


def server_events():
    path = REPORT / "server-events.ndjson"
    if not path.exists():
        raise RuntimeError("Instrumented server events are missing")
    return [json.loads(s) for s in path.read_text(encoding="utf-8").splitlines() if s]


def scalar(graph, binding):
    return graph[str(binding[0])]["inputs"]["value"] if isinstance(binding, list) else binding


def resolve_settings(graph):
    return dict(sampler_steps=[graph[n]["inputs"]["steps"] for n in ("3", "18", "12")],
                sampler_seeds=[scalar(graph, graph[n]["inputs"]["seed"]) for n in ("3", "18", "12")],
                remesh_resolution=graph["241"]["inputs"]["resolution"],
                target_face_count=graph["186"]["inputs"]["target_face_count"],
                texture_size=graph["288"]["inputs"]["value"],
                detail_1024_present="23" in graph or "94" in graph)


def finish_reports(summary):
    complete, nodes, chunks = [], [], []
    events = server_events()
    for run in summary["runs"]:
        if not run.get("result"):
            continue
        result = run["result"]
        history_path = Path(result["metadata_path"]).parent / "history.json"
        record = json.loads(history_path.read_text(encoding="utf-8"))[result["prompt_id"]]
        graph = record["prompt"][2]
        run["resolved_settings"] = resolve_settings(graph)
        if run["resolved_settings"] != EXPECTED:
            raise RuntimeError("Executed graph differs from agreed settings")
        timings = calculate_timings(events, graph, result["prompt_id"])
        executed = {r["node_id"] for r in timings["nodes"] if r["execution_chunks"] > 0}
        cached = set(timings["cached_node_ids"])
        if not SAMPLERS.issubset(executed) or SAMPLERS & cached:
            raise RuntimeError("Sampler was skipped/cached; not a valid second generation")
        run["samplers_actually_executed"] = sorted(SAMPLERS, key=int)
        run["samplers_cached"] = sorted(SAMPLERS & cached)
        timings["history_seconds"] = history_total(record)
        if timings["history_seconds"] is not None:
            timings["history_difference_seconds"] = timings["server_seconds"] - timings["history_seconds"]
        run["timings"] = timings
        nodes.extend(dict(run=run["index"], **row) for row in timings["nodes"])
        chunks.extend(dict(run=run["index"], **row) for row in timings["chunks"])
        complete.append(run)
    summary["source_unchanged"] = sha(SOURCE) == summary["source_sha256"]
    if not summary["source_unchanged"]:
        raise RuntimeError("Original source changed")
    write_json(REPORT / "summary.json", summary)
    for filename, rows in [("timings.csv", nodes), ("timeline.csv", chunks)]:
        if rows:
            with (REPORT / filename).open("w", encoding="utf-8-sig", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
    lines = ["# Два последовательных запуска одного топора", "",
             "Форма 512; 10 000 треугольников; Remesh 256; текстуры 256×256; шаги 6/8/6; seeds 56/42/42.", "",
             "PNG имеют разные имена и одинаковый SHA256. В обоих запусках проверено реальное исполнение "
             "sampler-узлов 3, 18, 12 без кеширования. Между запусками сервер не перезапускался.", "",
             "Время измерено на сервере по границам execution_start/executing/execution_success. "
             "Интервалы включают загрузку/перенос моделей внутри узла; это не чистое время GPU-ядер. "
             "История выполнения независимо сверена с серверными часами.", "",
             "| Этап | " + " | ".join(f"Запуск {r['index']}, с" for r in complete) +
             (" | Разница 2 − 1, с |" if len(complete) == 2 else " |"),
             "| --- | " + " | ".join("---:" for _ in complete) + (" | ---: |" if len(complete) == 2 else " |")]
    def row(label, values):
        suffix = f" | {values[1]-values[0]:+.3f}" if len(values) == 2 else ""
        return "| " + label + " | " + " | ".join(f"{v:.3f}" for v in values) + suffix + " |"
    for group, label in LABELS.items():
        lines.append(row(label, [r["timings"]["groups"][group] for r in complete]))
    lines.append(row("**Всего на сервере**", [r["timings"]["server_seconds"] for r in complete]))
    lines.append(row("Приложение: загрузка → скачанный GLB", [r["app_generation_seconds"] for r in complete]))
    if any(r.get("client_recovery") for r in complete):
        lines += ["", "Первый CLI-клиент после принятия задания сервером встретил запрет sandbox на запись "
                  "lock-файла. Наблюдение и скачивание продолжены по тому же prompt_id без повторной подачи. "
                  "Серверные времена непрерывны; клиентское время первого запуска включает паузу восстановления "
                  "и потому не подходит для чистого сравнения накладных расходов приложения."]
    for r in complete:
        lines += ["", f"## Запуск {r['index']}", "", f"Prompt: `{r['result']['prompt_id']}`", "",
                  f"[GLB]({Path(r['result']['glb_path']).as_posix()})", "",
                  f"![Предпросмотр]({Path(r['preview_path']).as_posix()})", "",
                  f"Треугольников: {r['validation']['polygons']}. Кешированные узлы: "
                  f"{', '.join(r['timings']['cached_node_ids']) or 'нет'}. Samplers 3/18/12 исполнились."]
    lines += ["", "Два запуска позволяют сравнить их этапы, но сами по себе не доказывают "
              "утечку памяти или конкретную причину замедления. Асинхронный рендер CPU предпросмотров "
              "не включён целиком во время observer-узлов.", ""]
    (REPORT / "report.md").write_text("\n".join(lines), encoding="utf-8")


def run_benchmark(resume_first=None):
    if (REPORT / "summary.json").exists() and not resume_first:
        raise RuntimeError("Existing checkpoint: refusing duplicate submission; use --report-only")
    if not (REPORT / "restart.json").is_file():
        raise RuntimeError("Root restart/GO record is missing")
    manifest = json.loads((REPORT / "inputs.json").read_text(encoding="utf-8"))
    fingerprint = manifest["source_sha256"]
    if any(sha(p) != fingerprint for p in [SOURCE, *INPUTS]):
        raise RuntimeError("Inputs no longer match the prepared source")
    checkpoint = REPORT / "summary.json"
    if resume_first:
        summary = json.loads(checkpoint.read_text(encoding="utf-8"))
        if len(summary["runs"]) != 1 or summary["runs"][0].get("result") or summary["parameters"] != PARAMETERS:
            raise RuntimeError("Resume is allowed only for first submitted job without a collected result")
        starts = [e for e in server_events() if e["event"] == "execution_start"]
        if len(starts) != 1 or starts[0]["prompt_id"] != resume_first:
            raise RuntimeError("Resume ID must match the sole recorded server execution")
        summary["runs"][0]["client_recovery"] = dict(prompt_id=resume_first, resumed_at=utc(),
            reason="Initial sandbox client could not write CLI lock after server accepted the job",
            generation_resubmitted=False, client_wall_time_includes_recovery=True)
        summary.update(status="running", resumed_at=utc())
    else:
        require_idle()
        summary = dict(schema="dart3s-repeated-input-benchmark/1", status="running", started_at=utc(),
                       source=str(SOURCE), source_sha256=fingerprint, parameters=PARAMETERS, runs=[])
    write_json(checkpoint, summary)
    try:
        for index, image in enumerate(INPUTS, 1):
            recovering = index == 1 and resume_first is not None
            if recovering:
                run = summary["runs"][0]
            else:
                require_idle()
                run = dict(index=index, input=str(image), input_sha256=sha(image), status="starting",
                           started_at=utc(), events=[], app_phases=[])
                summary["runs"].append(run)
            write_json(checkpoint, summary)  # BEFORE submission: retry cannot duplicate it.
            began = time.perf_counter()
            if recovering:
                began -= (datetime.now(timezone.utc) - datetime.fromisoformat(run["started_at"])).total_seconds()
            phase, phase_start = "starting", time.perf_counter()
            sampler_state = dict(node=None, changed=began, completed=None, warned=False)
            stopped = threading.Event()

            def watchdog():
                while not stopped.wait(2):
                    if sampler_state["node"] in SAMPLERS and not sampler_state["warned"]:
                        elapsed = time.perf_counter() - sampler_state["changed"]
                        if elapsed > 120:
                            sampler_state["warned"] = True
                            warning = dict(utc=utc(), run=index, status="sampler_stall_warning",
                                           node=sampler_state["node"], seconds_since_step=elapsed,
                                           action="notify_only_server_not_interrupted")
                            with (REPORT / "warnings.ndjson").open("a", encoding="utf-8") as f:
                                f.write(json.dumps(warning) + "\n")
                            print(json.dumps(warning), flush=True)

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
                node = str(nested.get("node"))
                if nested.get("type") == "executing":
                    sampler_state.update(node=node, changed=now, completed=None, warned=False)
                elif nested.get("type") == "progress" and node == sampler_state["node"]:
                    if nested.get("completed") != sampler_state["completed"]:
                        sampler_state.update(changed=now, completed=nested.get("completed"), warned=False)
                if nested.get("prompt_id"):
                    run["prompt_id"] = nested["prompt_id"]
                if status != "generating" or nested.get("type") in {"executing", "execution_cached"}:
                    run["events"].append(dict(utc=utc(), status=status, event=event))
                write_json(checkpoint, summary)
                print(json.dumps(dict(run=index, **event), ensure_ascii=False), flush=True)

            watcher = threading.Thread(target=watchdog, daemon=True)
            watcher.start()
            try:
                if recovering:
                    result = resume_3d(resume_first, output_dir=OUTPUT / "run1",
                                       auto_start=False, wait_timeout=1800, progress=progress)
                else:
                    result = generate_3d(image, **PARAMETERS, output_dir=OUTPUT / f"run{index}",
                                         auto_start=False, wait_timeout=1800,
                                         object_info=ROOT / "logs/object_info-progress.json", progress=progress)
            finally:
                stopped.set()
                watcher.join(timeout=3)
            run["app_generation_seconds"] = time.perf_counter()-began
            run["app_phases"].append(dict(status=phase, seconds=time.perf_counter()-phase_start))
            run["result"] = result.to_dict()
            write_json(checkpoint, summary)
            run["validation"] = run_portable("verify_glb.py", result.glb_path, "--polygons", 10000, "--texture-size", 256)
            preview = result.glb_path.with_suffix(".preview.png")
            run["preview"] = run_portable("gui_preview_bridge.py", result.glb_path, "--out", preview, "--size", 256)
            run["preview_path"] = str(preview)
            run["input_unchanged"] = sha(image) == fingerprint
            if not run["input_unchanged"]:
                raise RuntimeError("Input changed during generation")
            run.update(status="completed", finished_at=utc())
            finish_reports(summary)
            print(json.dumps(dict(run=index, status="verified", server_seconds=run["timings"]["server_seconds"],
                                  groups=run["timings"]["groups"], cached_node_ids=run["timings"]["cached_node_ids"],
                                  samplers_actually_executed=run["samplers_actually_executed"],
                                  result=result.to_dict(), preview_path=str(preview)), ensure_ascii=False), flush=True)
            if index == 1:
                print(json.dumps(dict(status="between_runs_idle", seconds=15,
                                      excluded_from_generation_time=True)), flush=True)
                time.sleep(15)
        summary.update(status="completed", finished_at=utc())
        finish_reports(summary)
        return 0
    except Exception as exc:
        summary.update(status="failed", error=str(exc), stopped_at=utc(),
                       note="No retry performed; a submitted server job may still run")
        write_json(checkpoint, summary)
        print(json.dumps(dict(status="failed", error=str(exc))), flush=True)
        return 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument("--prepare", action="store_true")
    choice.add_argument("--run", action="store_true")
    choice.add_argument("--report-only", action="store_true")
    choice.add_argument("--resume-first", metavar="PROMPT_ID",
                        help="Collect the sole already submitted first job; never resubmit it")
    args = parser.parse_args()
    if args.prepare:
        print(json.dumps(prepare(), ensure_ascii=False))
        return 0
    if args.report_only:
        finish_reports(json.loads((REPORT / "summary.json").read_text(encoding="utf-8")))
        return 0
    return run_benchmark(args.resume_first)


if __name__ == "__main__":
    raise SystemExit(main())
