"""Observe a user-requested real boat run through the normal native app."""
from __future__ import annotations
import json
import os
from pathlib import Path
import subprocess
import threading
import time

from .gui_jobs import write_json


def configure(app, *, prompt_id=None, metadata_path=None):
    from . import gui
    project = app.project
    report_path = project / 'logs/boat-full-app-verification.json'
    source = project / '.state/gui/inputs/1350e4828fa84ef98e29287b9d395e33.png'
    if not source.is_file() or not app.image or Path(app.image['path']).resolve() != source.resolve():
        raise RuntimeError('The selected image is not the requested boat; no generation was started')
    settings = dict(quality='Быстро · 512', model='Trellis2', polygons='1000', remesh_resolution='128', texture_size='128',
                    structure_seed='56', shape_seed='42', texture_seed='43')
    for key, value in settings.items():
        app.variables[key].set(value)
    app.preview_size.set('256')
    app.save_settings()
    report = dict(status='starting', input=str(source), settings=settings, preview_short_side=256,
                  genuine_generation=True, frames=[], completion_sound_calls=0, gui_pid=os.getpid(),
                  attached_prompt=prompt_id)
    if prompt_id:
        original = Path(metadata_path).resolve()
        metadata = json.loads(original.read_text(encoding='utf-8'))
        if not original.is_relative_to(project/'outputs') or metadata.get('prompt_id') != prompt_id:
            raise RuntimeError('Cannot attach to an unverified job')
        write_json(app.jobs.pending_file, dict(image=str(source), parameters=app.parameters().__dict__,
            prompt_id=prompt_id, metadata_path=str(original), preview_size=256,
            preview_directory=metadata['preview_directory']))
    lock = threading.Lock()
    def save():
        write_json(report_path, report)
    save()
    original_sound = gui.completion_sound
    def sound():
        with lock:
            report['completion_sound_calls'] += 1
            save()
        original_sound()
    gui.completion_sound = sound
    original_handler = app.handle_event

    def verify(result):
        try:
            glb = Path(result['glb_path'])
            process = subprocess.run([str(project/'ComfyUI_windows_portable/python_embeded/python.exe'),
                str(project/'scripts/verify_glb.py'), str(glb), '--polygons','1000','--texture-size','128'],
                capture_output=True, encoding='utf-8', timeout=30,
                creationflags=subprocess.CREATE_NO_WINDOW)
            validation = json.loads(process.stdout)
            with lock:
                report['glb_validation'] = validation
                report['status'] = 'completed' if process.returncode==0 and validation.get('ok') else 'validation_failed'
                save()
        except Exception as exc:
            with lock:
                report.update(status='validation_failed', error=str(exc))
                save()

    def handler(event):
        original_handler(event)
        kind = event['kind']
        with lock:
            if kind=='progress':
                value=event['event']
                if value.get('prompt_id'):
                    report['prompt_id']=value['prompt_id']
                if value.get('metadata_path'):
                    report['metadata_path']=value['metadata_path']
                report['current_status']=value.get('status')
                if value.get('event'):
                    report['last_event']=value['event']
            elif kind=='intermediate_preview' and app.intermediate_shown and not app.final_ready:
                snapshot=event['snapshot']
                photo=app.canvas_images[app.result_canvas][0]
                report['frames'].append({**snapshot,'shown_width':photo.width(),'shown_height':photo.height(),
                                        'caption':app.result_label.cget('text')})
            elif kind=='result':
                report.update(status='validating', result=event['result'])
                threading.Thread(target=verify,args=(event['result'],),daemon=True).start()
            elif kind=='preview' and app.final_ready:
                photo=app.canvas_images[app.result_canvas][0]
                report['final_preview']=dict(image=event['preview'],width=photo.width(),height=photo.height())
            elif kind=='error':
                report.update(status='cancelled' if event.get('cancelled') else 'failed',error=event)
            report['ui'] = dict(status=app.status.get(), steps=app.step_text.get(),
                                progress=float(app.bar['value']), model_caption=app.result_label.cget('text'))
            save()
    app.handle_event=handler
    def heartbeat():
        with lock:
            report['ui_heartbeat_at'] = time.time()
            report['ui'] = dict(status=app.status.get(), steps=app.step_text.get(),
                                progress=float(app.bar['value']), model_caption=app.result_label.cget('text'))
            save()
        if app.jobs.busy or report['status']=='starting':
            app.root.after(2000, heartbeat)
    app.root.after(2000,heartbeat)
    app.root.after(500,app.resume if prompt_id else app.generate)
