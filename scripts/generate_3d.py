"""Command line entrypoint for the reusable dart3s.generate_3d adapter."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dart3s.comfy3d import ComfyError, GenerationResult, generate_3d, resume_3d


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a local textured GLB through Comfy CLI.")
    parser.add_argument("--image", type=Path)
    parser.add_argument("--resume", metavar="PROMPT_ID", help="Watch/download an existing job, without resubmitting.")
    parser.add_argument("--model", choices=["pixal3d", "trellis2"], default="pixal3d")
    parser.add_argument("--quality", choices=["standard", "fast"], default="standard")
    parser.add_argument("--polygons", type=int, default=10000)
    parser.add_argument("--remesh-resolution", type=int, default=256)
    parser.add_argument("--texture-size", type=int, default=256)
    parser.add_argument("--structure-seed", type=int, default=56)
    parser.add_argument("--shape-seed", type=int, default=42, help="Shared shape seed for both geometry-dependent samplers.")
    parser.add_argument("--texture-seed", type=int, default=43)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--local-url", default="http://127.0.0.1:8188")
    parser.add_argument("--wait-timeout", type=float, default=7200)
    parser.add_argument("--no-start-server", dest="auto_start", action="store_false", help="Require an already running local ComfyUI server.")
    parser.add_argument("--startup-timeout", type=float, default=180)
    parser.add_argument("--prepare-only", action="store_true", help="Build only: no upload and no execution.")
    parser.add_argument("--object-info", type=Path, help="Saved node catalog for offline preparation.")
    options = vars(parser.parse_args())
    prompt_id = options.pop("resume")
    if prompt_id and (options["image"] or options["prepare_only"]):
        parser.error("--resume cannot be combined with --image or --prepare-only")
    if not prompt_id and not options["image"]:
        parser.error("--image or --resume is required")

    def progress(event: dict) -> None:
        print(json.dumps(event, ensure_ascii=False), file=sys.stderr, flush=True)

    try:
        if prompt_id:
            result = resume_3d(prompt_id, output_dir=options["output_dir"], local_url=options["local_url"],
                               wait_timeout=options["wait_timeout"], auto_start=options["auto_start"],
                               startup_timeout=options["startup_timeout"], progress=progress)
        else:
            result = generate_3d(**options, progress=progress)
        print(json.dumps({"ok": True, "data": result.to_dict() if isinstance(result, GenerationResult) else result,
                          "error": None}, ensure_ascii=False, indent=2))
        return 0
    except (ComfyError, ValueError, OSError) as exc:
        error = exc.to_dict() if isinstance(exc, ComfyError) else {"code": type(exc).__name__, "message": str(exc)}
        print(json.dumps({"ok": False, "data": None, "error": error}, ensure_ascii=False, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
