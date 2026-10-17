"""Submit ONE guarded cache-only mesh export, then download three saved GLBs.

Only newly added exporters and trivial switches/primitives may execute. Any
original expensive node execution requests scoped interruption immediately.
Never retries a submission or starts/restarts ComfyUI.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
from urllib.request import ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dart3s.cli_stream import run_stream
from dart3s.comfy3d import glb_download_urls, local_environment, parse_envelope

DIRECTORY = ROOT / "outputs/stone-simplification-probe"
REPORT = ROOT / "logs/stone-simplification-probe"
URL = "http://127.0.0.1:8188"
opener = build_opener(ProxyHandler({}))


def api(route, body=None):
    request = Request(URL + route, data=json.dumps(body).encode() if body is not None else None,
                      headers={"Content-Type": "application/json"})
    with opener.open(request, timeout=10) as response:
        return json.loads(response.read() or "{}")


def utc():
    return datetime.now(timezone.utc).isoformat()


def main():
    checkpoint = REPORT / "extraction.json"
    if checkpoint.exists():
        raise RuntimeError("Extraction checkpoint exists; refusing duplicate submission")
    manifest = json.loads((REPORT / "preparation.json").read_text(encoding="utf-8"))
    compiled = json.loads(Path(manifest["compiled"]).read_text(encoding="utf-8"))
    queue = api("/queue")
    if queue.get("queue_running") or queue.get("queue_pending"):
        raise RuntimeError("Server queue occupied; preserving its work")
    source_history = api("/history/" + manifest["source_prompt_id"])
    if not source_history.get(manifest["source_prompt_id"], {}).get("status", {}).get("completed"):
        raise RuntimeError("Source stone prompt has no completed history")
    original = source_history[manifest["source_prompt_id"]]["prompt"][2]
    retained = set(manifest["retained_original_node_ids"])
    if not all(compiled[n] == original[n] for n in retained):
        raise RuntimeError("Prepared ancestors differ from source's actually executed graph")
    trivial = {"ComfySwitchNode", "PrimitiveInt", "PrimitiveBoolean"}
    forbidden = {n for n in retained if compiled[n]["class_type"] not in trivial}
    report = dict(status="submitting_once", started_at=utc(), source_prompt_id=manifest["source_prompt_id"],
                  cached_nodes=[], executing_nodes=[], events=[], forbidden_execution_ids=sorted(forbidden),
                  neural_sampler_ids=["3", "18", "23", "12"])

    def save():
        tmp = checkpoint.with_suffix(".tmp")
        tmp.write_text(json.dumps(report, indent=2), encoding="utf-8")
        tmp.replace(checkpoint)

    save()  # Before the single submission, so errors cannot provoke duplicates.

    def event(value):
        report["events"].append(dict(utc=utc(), event=value))
        prompt_id = value.get("prompt_id")
        if prompt_id:
            previous = report.get("prompt_id")
            if previous and previous != prompt_id:
                raise RuntimeError("Unexpected prompt identity in scoped export stream")
            report["prompt_id"] = prompt_id
        node = str(value.get("node", ""))
        if value.get("type") == "execution_cached":
            cached = value.get("nodes") or ([node] if node else [])
            report["cached_nodes"] = sorted(set(report["cached_nodes"]) | {str(n) for n in cached})
        if value.get("type") == "executing":
            report["executing_nodes"].append(node)
            print(json.dumps(dict(utc=utc(), executing=node, prompt_id=report.get("prompt_id"))), flush=True)
            if node in forbidden or node in {"3", "18", "23", "12"}:
                report["cache_missing"] = True
                report["unexpected_node"] = node
                if not report.get("interrupt_requested"):
                    report["interrupt_requested"] = True
                    save()
                    if not report.get("prompt_id"):
                        raise RuntimeError("Cannot safely interrupt an unidentified prompt")
                    api("/interrupt", {"prompt_id": report["prompt_id"]})
                    print("CACHE MISS: scoped interruption requested; no regeneration authorized", flush=True)
        save()

    try:
        command = [str(ROOT / ".venv-cli/Scripts/comfy.exe"), "--json-stream", "run",
                   "--workflow", manifest["compiled"], "--where", "local", "--wait",
                   "--timeout", "1800", "--no-notify"]
        result = run_stream(command, cwd=ROOT, env=local_environment(ROOT, URL), timeout=1900,
                            stdout_path=REPORT / "export.stdout.log", stderr_path=REPORT / "export.stderr.log",
                            on_event=event)
        envelope = parse_envelope(result.stdout)
        report["envelope"] = envelope
        if report.get("cache_missing") or result.returncode or not envelope.get("ok"):
            raise RuntimeError(envelope.get("error") or "Cache-only extraction interrupted")
        prompt_id = report.get("prompt_id") or envelope["data"]["prompt_id"]
        report["prompt_id"] = prompt_id
        history = api("/history/" + prompt_id)
        (REPORT / "history.json").write_text(json.dumps(history, indent=2), encoding="utf-8")
        record = history[prompt_id]
        if not record.get("status", {}).get("completed") or record["status"].get("status_str") != "success":
            raise RuntimeError("Cache export did not terminate successfully")
        cached_history = {str(n) for name, data in record.get("status", {}).get("messages", [])
                          if name == "execution_cached" for n in data.get("nodes", [])}
        report["cached_nodes_history"] = sorted(cached_history)
        if not set(manifest["stage_nodes"].values()).issubset(cached_history):
            raise RuntimeError("History did not verify cached raw/remesh/decimate stages")
        if any(n in forbidden for n in report["executing_nodes"]):
            raise RuntimeError("Original compute node executed; cache-only proof failed")
        download_dir = DIRECTORY / "download"
        download_dir.mkdir(exist_ok=True)
        refs = glb_download_urls(history, prompt_id, URL)
        if len(refs) != 3:
            raise RuntimeError(f"Expected three stage GLBs; got {len(refs)}")
        payload = json.dumps(dict(ok=True, command="mesh stage cache probe", where="local",
                                  data=dict(prompt_id=prompt_id, outputs=refs), error=None))
        downloaded = subprocess.run([str(ROOT / ".venv-cli/Scripts/comfy.exe"), "--json", "download",
                                     "--where", "local", "--out-dir", str(download_dir)],
                                    input=payload, cwd=ROOT, env=local_environment(ROOT, URL),
                                    capture_output=True, encoding="utf-8", timeout=180,
                                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        (REPORT / "download.stdout.json").write_text(downloaded.stdout, encoding="utf-8")
        (REPORT / "download.stderr.log").write_text(downloaded.stderr, encoding="utf-8")
        parsed = parse_envelope(downloaded.stdout)
        if downloaded.returncode or not parsed.get("ok"):
            raise RuntimeError(parsed.get("error") or "CLI artifact download failed")
        report["download"] = parsed
        report["stage_outputs"] = {stage: record["outputs"][str(save_id)].get("3d", [])
                                   for stage, save_id in manifest["save_nodes"].items()}
        report.update(status="completed", finished_at=utc(),
                      cache_only_verified=True, neural_sampler_executed=False)
        save()
        print(json.dumps(report, indent=2))
        return 0
    except Exception as exc:
        report.update(status="failed", error=str(exc), finished_at=utc(),
                      no_retry=True)
        save()
        raise


if __name__ == "__main__":
    raise SystemExit(main())
