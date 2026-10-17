"""Isolated two-run cache-classic probe; preparation never touches the GPU.

Run --run only after root's GO, using require_escalated so Comfy CLI can
maintain its existing job lock files. This wrapper never restarts the server.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import benchmark_same_input as benchmark

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "logs/repeated-input-cache-classic"
OUTPUT = ROOT / "outputs/repeated-input-cache-classic"


def configure():
    benchmark.REPORT = REPORT
    benchmark.OUTPUT = OUTPUT
    benchmark.INPUTS = [REPORT / "inputs/axe_classic_run1.png", REPORT / "inputs/axe_classic_run2.png"]


def annotate():
    """Keep the cache-policy provenance beside the independent timing report."""
    restart = json.loads((REPORT / "restart.json").read_text(encoding="utf-8"))
    summary_path = REPORT / "summary.json"
    comparison = None
    if summary_path.exists():
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        summary["cache_mode"] = "classic"
        summary["server_restart"] = restart
        runs = summary.get("runs", [])
        if len(runs) == 2 and all(r.get("timings") for r in runs):
            totals = [r["timings"]["server_seconds"] for r in runs]
            structure = [next(n["seconds"] for n in r["timings"]["nodes"] if n["node_id"] == "3") for r in runs]
            comparison = dict(first_server_seconds=totals[0], second_server_seconds=totals[1],
                              total_ratio=totals[1]/totals[0],
                              sampler3_seconds=structure, sampler3_ratio=structure[1]/structure[0],
                              classic_removed_repeat_slowdown=False if totals[1] > totals[0] else None)
            summary["comparison"] = comparison
        benchmark.write_json(summary_path, summary)
    report_path = REPORT / "report.md"
    if report_path.exists():
        content = report_path.read_text(encoding="utf-8")
        prefix = ("Проверка политики кеша **classic** (`--cache-classic`). "
                  "Политика задана при запуске отдельного серверного процесса; "
                  "параметры основной программы не изменены.\n\n")
        if not content.startswith(prefix):
            content = prefix + content
        if comparison and comparison["classic_removed_repeat_slowdown"] is False:
            conclusion = ("\n\nПереход на `--cache-classic` **не устранил замедление повторной генерации**: "
                          f"общее серверное время {comparison['first_server_seconds']:.3f} → "
                          f"{comparison['second_server_seconds']:.3f} с ({comparison['total_ratio']:.2f}×), "
                          f"sampler структуры {comparison['sampler3_seconds'][0]:.3f} → "
                          f"{comparison['sampler3_seconds'][1]:.3f} с ({comparison['sampler3_ratio']:.2f}×). "
                          "Причина этим экспериментом не установлена; дальнейшие генерации не запускались.\n")
            if conclusion not in content:
                content += conclusion
        report_path.write_text(content, encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument("--prepare", action="store_true")
    choice.add_argument("--run", action="store_true")
    choice.add_argument("--report-only", action="store_true")
    choice.add_argument("--resume-first", metavar="PROMPT_ID")
    args = parser.parse_args()
    configure()
    if args.prepare:
        manifest = benchmark.prepare()
        manifest["planned_cache_mode"] = "classic"
        benchmark.write_json(REPORT / "inputs.json", manifest)
        print(json.dumps(manifest, ensure_ascii=False))
        return 0
    restart = json.loads((REPORT / "restart.json").read_text(encoding="utf-8"))
    if restart.get("cache_mode") != "classic" or restart.get("ready") is not True:
        raise RuntimeError("Root must supply a ready=true restart record with cache_mode=classic before GO")
    if args.report_only:
        benchmark.finish_reports(json.loads((REPORT / "summary.json").read_text(encoding="utf-8")))
        code = 0
    else:
        code = benchmark.run_benchmark(args.resume_first)
    annotate()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
