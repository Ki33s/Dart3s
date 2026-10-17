"""Two isolated same-image generations to verify production idle-model release.

Preparation performs only local file copies. A bad/missing cleanup observation
after the first valid GLB prevents submission of the second GPU job.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import benchmark_same_input as benchmark

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "logs/repeated-input-memory-cleanup"
OUTPUT = ROOT / "outputs/repeated-input-memory-cleanup"
_finish_reports = benchmark.finish_reports


def checked_reports(summary):
    _finish_reports(summary)
    for run in summary.get("runs", []):
        if not run.get("result"):
            continue
        metadata = json.loads(Path(run["result"]["metadata_path"]).read_text(encoding="utf-8"))
        cleanup = metadata.get("memory_cleanup", {"status": "missing"})
        run["memory_cleanup"] = cleanup
        run["cleanup_verified"] = cleanup.get("status") in {"release_observed", "already_released"}
    benchmark.write_json(REPORT / "summary.json", summary)
    annotate(summary)
    for run in summary.get("runs", []):
        if run.get("result") and not run.get("cleanup_verified"):
            warning = dict(status="cleanup_not_verified_stopping_before_next_submission",
                           run=run["index"], memory_cleanup=run["memory_cleanup"],
                           result=run["result"], server_seconds=run["timings"]["server_seconds"])
            print(json.dumps(warning, ensure_ascii=False), flush=True)
            raise RuntimeError("Cleanup release was not observed; no second generation will be submitted without root review")


def configure():
    benchmark.REPORT = REPORT
    benchmark.OUTPUT = OUTPUT
    benchmark.INPUTS = [REPORT / "inputs/axe_cleanup_run1.png", REPORT / "inputs/axe_cleanup_run2.png"]
    benchmark.finish_reports = checked_reports


def annotate(summary):
    restart = json.loads((REPORT / "restart.json").read_text(encoding="utf-8-sig"))
    summary["server_restart"] = restart
    summary["cache_mode"] = restart.get("cache_mode")
    runs = [r for r in summary.get("runs", []) if r.get("timings")]
    if len(runs) == 2:
        totals = [r["timings"]["server_seconds"] for r in runs]
        samplers = [{n["node_id"]: n["seconds"] for n in r["timings"]["nodes"]
                     if n["node_id"] in benchmark.SAMPLERS} for r in runs]
        summary["comparison"] = dict(server_seconds=totals, total_ratio=totals[1]/totals[0],
                                     sampler_seconds=samplers,
                                     sampler3_ratio=samplers[1]["3"]/samplers[0]["3"])
    benchmark.write_json(REPORT / "summary.json", summary)
    report = REPORT / "report.md"
    text = report.read_text(encoding="utf-8")
    text = ("Проверка исправления освобождения моделей после получения GLB. "
            "Сервер между двумя запусками не перезапускается; политика кеша — исходная, "
            "без `--cache-classic`.\n\n" + text)
    for run in runs:
        cleanup = run.get("memory_cleanup", {})
        text += (f"\n\nОсвобождение памяти после запуска {run['index']}: "
                 f"`{cleanup.get('status', 'missing')}`. "
                 "Полные before/after находятся в summary.json и исходном generation.json.\n")
    if len(runs) == 2:
        comparison = summary["comparison"]
        text += ("\n\nСерверное время первого и второго запуска: "
                 f"{comparison['server_seconds'][0]:.3f} и {comparison['server_seconds'][1]:.3f} с; "
                 f"отношение второго к первому {comparison['total_ratio']:.3f}. "
                 f"Отношение sampler структуры: {comparison['sampler3_ratio']:.3f}. "
                 "Эта пара проверяет повтор с данным изображением и настройками; "
                 "сама по себе не гарантирует результат для любых сцен.\n")
    report.write_text(text, encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument("--prepare", action="store_true")
    choice.add_argument("--run", action="store_true")
    choice.add_argument("--report-only", action="store_true")
    args = parser.parse_args()
    configure()
    if args.prepare:
        manifest = benchmark.prepare()
        manifest["probe"] = "release_idle_models_after_saved_glb"
        manifest["second_run_requires_cleanup"] = ["release_observed", "already_released"]
        benchmark.write_json(REPORT / "inputs.json", manifest)
        print(json.dumps(manifest, ensure_ascii=False))
        return 0
    restart = json.loads((REPORT / "restart.json").read_text(encoding="utf-8-sig"))
    if (restart.get("ready") is not True or restart.get("cache_mode") not in {"default", "default_ram_pressure"}
            or "--cache-classic" in restart.get("argv", [])):
        raise RuntimeError("Wait for root GO: restart record must identify a ready default-cache server")
    if args.report_only:
        checked_reports(json.loads((REPORT / "summary.json").read_text(encoding="utf-8")))
        return 0
    return benchmark.run_benchmark()


if __name__ == "__main__":
    raise SystemExit(main())
