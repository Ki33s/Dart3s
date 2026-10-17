"""One bounded retry before committing to expensive shape inference."""
import json
from pathlib import Path
import time
import math
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, build_opener

from .comfy3d import ComfyError, Parameters, PROJECT


def failure_details(exc):
    details = exc.details if isinstance(exc.details, dict) else {}
    for _ in range(4):
        if 'exception_type' in details or not isinstance(details.get('details'), dict):
            break
        details = details['details']
    return details


class EarlyBudget:
    def __init__(self, seconds, clock=time.monotonic):
        self.seconds, self.clock, self.started = seconds, clock, None
        self.completed = None

    def elapsed(self):
        return 0 if self.started is None else (self.completed if self.completed is not None else self.clock()) - self.started


class EarlyWatch:
    def __init__(self, budget, structure_seconds=75, shape_seconds=45):
        self.budget = budget
        self.structure_seconds, self.shape_seconds = structure_seconds, shape_seconds
        self.node = None
        self.stage_started = None
        self.passed = False
        self.timing_dir = None
        self.seen_starts = set()
        self.last_refresh = float('-inf')

    def refresh(self, force=False):
        now = self.budget.clock()
        if self.timing_dir is None or self.passed or (not force and now-self.last_refresh < .25):
            return
        self.last_refresh = now
        for phase in ('3','18'):
            if phase in self.seen_starts:
                continue
            try:
                data = json.loads((self.timing_dir/f'early-stage-{phase}.json').read_text(encoding='utf-8'))
                started = data['started']
                if data['node']!=phase or type(started) not in (int,float) or not math.isfinite(started) or not 0<=started<=now:
                    continue
            except (OSError,ValueError,KeyError,TypeError):
                continue
            self.seen_starts.add(phase)
            self.budget.started = started if self.budget.started is None else min(started,self.budget.started)
            if phase=='18' or self.node in (None,'3'):
                if self.node!=phase or self.stage_started is None:
                    self.node,self.stage_started=phase,started
                else:
                    self.stage_started=min(started,self.stage_started)

    def event(self, event):
        self.refresh(force=True)
        node = str(event.get('node', ''))
        completed = event.get('completed')
        # CLI may miss the sampler's very short start transition, or attach to
        # a cached upstream graph. Its real prerequisite completion is reliable
        # and must start the timer before the first slow iteration completes.
        if event.get('type') == 'progress' and isinstance(completed, (int, float)) and completed >= 1:
            if node in ('314', '315') and self.node is None:
                node = '3'
                event = {'type': 'executing'}
            elif node == '91':
                node = '18'
                event = {'type': 'executing'}
        if node not in ('3', '18') or event.get('type') not in ('executing', 'progress'):
            return
        if node == '3' and self.node == '18':
            return
        now = self.budget.clock()
        if self.budget.started is None:
            self.budget.started = now
        if node != self.node:
            self.node, self.stage_started = node, now
        if node == '18' and event.get('type') == 'progress' and isinstance(completed, (int, float)) and completed >= 2:
            self.check()
            self.passed = True
            self.budget.completed = now

    def check(self):
        self.refresh()
        if self.passed:
            return
        elapsed = 0 if self.stage_started is None else self.budget.clock() - self.stage_started
        reason = None
        if self.budget.elapsed() >= self.budget.seconds:
            reason = f'Исчерпан бюджет ранней проверки {self.budget.seconds} с.'
        elif self.node == '3' and elapsed >= self.structure_seconds:
            reason = f'Расчёт структуры превысил {self.structure_seconds} с.'
        elif self.node == '18' and elapsed >= self.shape_seconds:
            reason = f'Первые два шага формы не завершились за {self.shape_seconds} с.'
        if reason:
            raise ComfyError('early_risk', reason, details={'node': self.node, 'stage_seconds': elapsed})


def reset_owned_server(project, local_url, prompt_id, cancel_event=None):
    """Do not interrupt another client or pending submissions."""
    base = local_url.rstrip('/')
    try:
        with build_opener(ProxyHandler({})).open(base + '/queue', timeout=5) as response:
            queue = json.load(response)
    except Exception as exc:
        raise ComfyError('risk_cleanup_failed', f'Не удалось проверить очередь перед очисткой: {exc}') from exc
    if (not isinstance(queue.get('queue_running'), list) or not isinstance(queue.get('queue_pending'), list)
            or queue['queue_pending'] or any(not isinstance(row, list) or len(row) < 2 or row[1] != prompt_id
                                             for row in queue['queue_running'])):
        raise ComfyError('risk_cleanup_failed', 'Сервер занят другим заданием; автоматический перезапуск отменён.')
    address = urlsplit(local_url)
    if address.hostname not in ('127.0.0.1', 'localhost', '::1'):
        raise ComfyError('risk_cleanup_failed', 'Автоматический перезапуск разрешён только для локального ComfyUI.')
    argv = ['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
            str(project / 'scripts/Stop-ComfyUI.ps1'), '-Port', str(address.port or 8188)]
    from .cli_stream import run_cancellable
    import os
    def check():
        if cancel_event is not None and cancel_event.is_set():
            raise ComfyError('cancelled', 'Генерация отменена пользователем.')
    result = run_cancellable(argv, cwd=project, env=os.environ.copy(), timeout=20,
                             input_text=None, check_cancel=check)
    if result.returncode:
        raise ComfyError('risk_cleanup_failed', result.stderr.strip() or 'ComfyUI не завершился.')
    return json.loads(result.stdout)


def generate_adaptive(image, *, seconds, generate_once, retry_allowed=lambda: True, limits=None, **kwargs):
    if type(seconds) is not int or not 60 <= seconds <= 600:
        raise ValueError('Бюджет ранней проверки должен быть от 60 до 600 секунд.')
    Parameters(**{**{k:v for k,v in kwargs.items() if k in Parameters.__dataclass_fields__},
                  'early_risk_seconds':seconds}).validate()
    if kwargs.get('prepare_only'):
        return generate_once(image, early_risk_seconds=seconds, **kwargs)
    if not kwargs.get('auto_start', True):
        raise ValueError('Автоматический повтор требует разрешённого запуска локального сервера.')
    project = Path(kwargs.get('project', PROJECT)).resolve()
    config_path = project / '.state/gui/early-risk-policy.json'
    config = json.loads(config_path.read_text(encoding='utf-8')) if config_path.is_file() else {}
    if limits is not None:
        config = {**config, **limits}
    structure_seconds = config.get('structure_seconds', 75)
    shape_seconds = config.get('shape_two_steps_seconds', 45)
    for limit in (structure_seconds, shape_seconds):
        if type(limit) not in (int, float) or not 10 <= limit <= 600:
            raise ValueError('Порог раннего этапа должен быть числом от 10 до 600 секунд.')
    if kwargs.get('quality', 'standard') == 'standard':
        structure_seconds *= 2  # standard uses 12 structure steps instead of fast's 6
    local_url = kwargs.get('local_url', 'http://127.0.0.1:8188')
    progress = kwargs.get('progress')
    base_seed = kwargs.get('structure_seed', 56)
    attempts, budget = [], EarlyBudget(seconds)
    count = 1 + min(1, kwargs.get('structure_retries', 1))
    metadata_path = None
    def save(status):
        if metadata_path:
            path = Path(metadata_path).with_name('early-risk-attempts.json')
            path.write_text(json.dumps(dict(status=status, budget_seconds=seconds,
                elapsed_early_seconds=budget.elapsed(), base_structure_seed=base_seed, max_retries=count-1,
                structure_seconds=structure_seconds, shape_two_steps_seconds=shape_seconds,
                attempts=attempts), ensure_ascii=False, indent=2), encoding='utf-8')
    for index in range(count):
        watch = EarlyWatch(budget, structure_seconds, shape_seconds)
        seed = (base_seed + index) % 2**64
        def relay(event):
            nonlocal metadata_path
            metadata_path = event.get('metadata_path', metadata_path)
            if progress:
                progress(event)
        try:
            result = generate_once(image, **{**kwargs, 'structure_seed': seed, 'structure_retries': 0,
                'early_risk_seconds': seconds, '_early_guard': watch, 'progress': relay})
            attempts.append(dict(seed=seed, status='accepted', metadata_path=str(result.metadata_path)))
            metadata_path = result.metadata_path
            save('accepted')
            return result
        except ComfyError as exc:
            metadata_path = exc.metadata_path or metadata_path
            details = failure_details(exc)
            rejected = exc.code == 'early_risk' or (exc.code == 'execution_error' and
                str(details.get('exception_type', '')).endswith('StructureBudgetExceeded'))
            if not rejected:
                attempts.append(dict(seed=seed, status='error', reason=exc.message,
                                     metadata_path=str(metadata_path) if metadata_path else None))
                save('cancelled' if exc.code=='cancelled' else 'error')
                raise
            reason = details.get('exception_message', exc.message)
            attempts.append(dict(seed=seed, status='rejected', reason=reason,
                                 metadata_path=str(metadata_path) if metadata_path else None))
            prompt_id = None
            if metadata_path:
                prompt_id = json.loads(Path(metadata_path).read_text(encoding='utf-8')).get('prompt_id')
            if progress:
                progress({'status': 'risk_cleanup', 'metadata_path': str(metadata_path)})
            attempts[-1]['cleanup'] = reset_owned_server(project, local_url, prompt_id, kwargs.get('cancel_event'))
            if not retry_allowed():
                save('cancelled')
                raise ComfyError('cancelled', 'Повтор отменён пользователем.')
            save('retrying' if index + 1 < count and budget.elapsed() < seconds else 'skipped')
            if index + 1 >= count or budget.elapsed() >= seconds:
                break
            if progress:
                progress({'status': 'retrying_structure', 'seed': (base_seed + index + 1) % 2**64,
                          'reason': reason, 'metadata_path': str(metadata_path)})
    error = ComfyError('high_risk', f'Высокий риск: проверено вариантов {len(attempts)}. Картинка пропущена. '
                       + (attempts[-1]['reason'] if attempts else ''),
                       details={'attempts': attempts, 'budget_seconds': seconds})
    error.metadata_path = Path(metadata_path) if metadata_path else None
    raise error
