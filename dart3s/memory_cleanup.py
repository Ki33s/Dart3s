"""Release idle ComfyUI model weights while retaining its process and outputs."""
from __future__ import annotations

import json
import time
from urllib.request import ProxyHandler, Request, build_opener


def release_idle_models(local_url: str, *, wait_seconds: float = 12) -> dict:
    opener = build_opener(ProxyHandler({}))
    base = local_url.rstrip("/")

    def request(path, body=None):
        data = None if body is None else json.dumps(body).encode("utf-8")
        req = Request(base + path, data=data, headers={"Content-Type": "application/json"})
        with opener.open(req, timeout=2) as response:
            payload = response.read()
        return json.loads(payload) if payload else None

    def memory(stats):
        return {str(d.get("index", i)): {
            "allocated": d["torch_vram_total"] - d["torch_vram_free"],
            "free": d["vram_free"],
            "total": d.get("vram_total", 0),
        } for i, d in enumerate(stats.get("devices", []))
            if d.get("type") != "cpu" and all(isinstance(d.get(k), (int, float))
                for k in ("torch_vram_total", "torch_vram_free", "vram_free"))}

    try:
        queue = request("/queue")
        if not isinstance(queue, dict) or any(not isinstance(queue.get(k), list)
                for k in ("queue_running", "queue_pending")):
            return {"status": "skipped", "reason": "unrecognized_queue"}
        if queue["queue_running"] or queue["queue_pending"]:
            return {"status": "skipped", "reason": "server_busy"}
        before = memory(request("/system_stats"))
        if before and all(item["total"] > 0 and
                item["allocated"] < 64 * 1024**2 and item["free"] > item["total"] * 0.9
                for item in before.values()):
            return {"status": "already_released", "before": before}
        # free_memory=True also resets the executor's result cache. Only unload
        # model weights; saved GLBs, history and intermediate outputs survive.
        request("/free", {"unload_models": True, "free_memory": False})
        report = {"status": "requested", "before": before}
        deadline = time.monotonic() + wait_seconds
        while before and time.monotonic() < deadline:
            time.sleep(0.25)
            after = memory(request("/system_stats"))
            report["after"] = after
            if any(key in after and (
                item["allocated"] - after[key]["allocated"] >= 32 * 1024**2 or
                after[key]["free"] - item["free"] >= 128 * 1024**2
            ) for key, item in before.items()):
                report["status"] = "release_observed"
                break
        return report
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        # An unavailable maintenance endpoint must not invalidate a saved GLB.
        return {"status": "unavailable", "error": str(exc)}
