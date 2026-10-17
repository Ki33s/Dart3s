"""Optional server-side timestamps for a bounded two-job benchmark."""
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import threading
import time


def install():
    target = os.environ.get('DART3S_TIMINGS_FILE')
    if not target:
        return
    project = Path(__file__).resolve().parents[1]
    path = Path(target).resolve()
    if not path.is_relative_to(project/'logs'):
        raise ValueError('Benchmark timestamps must stay inside project logs')
    from server import PromptServer
    original = PromptServer.send_sync
    if getattr(original,'_dart3s_timed',False):
        return
    path.parent.mkdir(parents=True,exist_ok=True)
    lock = threading.Lock()
    completed = set()
    watched = {'execution_start','executing','execution_cached','execution_success',
               'execution_error','execution_interrupted'}
    terminal = {'execution_success','execution_error','execution_interrupted'}

    def timed(self,event,data,sid=None):
        if event in watched and isinstance(data,dict):
            record = dict(event=event,monotonic=time.perf_counter(),
                          utc=datetime.now(timezone.utc).isoformat(),
                          prompt_id=data.get('prompt_id'),node=data.get('node'))
            if event=='execution_cached':
                record['nodes']=data.get('nodes',[])
            try:
                with lock:
                    with path.open('a',encoding='utf-8') as log:
                        log.write(json.dumps(record,separators=(',',':'))+'\n')
                    if event in terminal:
                        completed.add(data.get('prompt_id'))
                        if len(completed)>=2:
                            PromptServer.send_sync=original
            except OSError:
                logging.exception('Nonfatal benchmark timestamp recording error')
        return original(self,event,data,sid)
    timed._dart3s_timed=True
    PromptServer.send_sync=timed
