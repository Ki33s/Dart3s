"""Local TEST fixture for installed CLI websocket events and GLB download.

No GPU work or model generation: serves a previously verified GLB as test data.
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path
from datetime import datetime, timezone
from aiohttp import web
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dart3s.progress_preview import render_glb_preview

parser = argparse.ArgumentParser()
parser.add_argument("--glb", type=Path, required=True)
parser.add_argument("--ready", type=Path, required=True)
parser.add_argument('--preview-directory', type=Path)
parser.add_argument('--preview-size', type=int, default=256)
args = parser.parse_args()
prompt_id = "dart3s-gui-test"
done = False
workflow = {"23": {"class_type": "KSampler", "inputs": {}}}
prompt = [0, prompt_id, workflow, {}, ["322"]]
app = web.Application()


async def stats(request):
    return web.json_response({"system": {"comfyui_version": "TEST"}, "devices": []})


async def queued(request):
    return web.json_response({"queue_running": [] if done else [prompt], "queue_pending": []})


async def history(request):
    return web.json_response({prompt_id: {"prompt": prompt, "outputs": {"322": {"3d": [
        {"filename": "TEST-model.glb", "subfolder": "", "type": "output"}]}},
        "status": {"completed": True, "status_str": "success", "messages": []}}} if done else {})


async def view(request):
    return web.FileResponse(args.glb)


async def websocket(request):
    global done
    socket = web.WebSocketResponse()
    await socket.prepare(request)
    await socket.send_json({"type": "executing", "data": {"prompt_id": prompt_id, "node": "23"}})
    if args.preview_directory:
        args.preview_directory.mkdir(parents=True, exist_ok=True)
        # Only UI delivery is under test: reuse the known generated model's view.
        rendered = args.preview_directory / 'test-frame.png'
        render_glb_preview(args.glb, rendered, size=args.preview_size)
        with Image.open(rendered) as source:
            thumbnail = source.convert('RGB')
        for revision, stage in enumerate(('structure', 'geometry', 'prepared', 'textured'), 1):
            path = args.preview_directory / (stage+'.png')
            thumbnail.save(path)
            temporary = args.preview_directory / 'latest.tmp'
            temporary.write_text(json.dumps(dict(version=1, revision=revision, stage=stage,
                image=str(path.resolve()), size=args.preview_size,
                width=thumbnail.width, height=thumbnail.height,
                triangles=None if stage=='structure' else 9982,
                created_at=datetime.now(timezone.utc).isoformat())), encoding='utf-8')
            temporary.replace(args.preview_directory / 'latest.json')
            await asyncio.sleep(1.4)
    for n in (1, 7, 12):
        await asyncio.sleep(1)
        await socket.send_json({"type": "progress_state", "data": {"prompt_id": prompt_id,
            "nodes": {"23": {"value": n, "max": 12, "state": "finished" if n == 12 else "running"}}}})
    done = True
    await socket.send_json({"type": "execution_success", "data": {"prompt_id": prompt_id}})
    await asyncio.sleep(2)
    await socket.close()
    return socket


app.router.add_get("/system_stats", stats)
app.router.add_get("/queue", queued)
app.router.add_get("/history", history)
app.router.add_get("/history/{prompt_id}", history)
app.router.add_get("/view", view)
app.router.add_get("/ws", websocket)


async def run():
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    args.ready.write_text(json.dumps({"url": f"http://127.0.0.1:{port}", "prompt_id": prompt_id}), encoding="utf-8")
    await asyncio.Event().wait()


asyncio.run(run())
