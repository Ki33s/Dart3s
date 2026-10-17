"""Job ownership, recoverability and history boundaries for the native app."""
import json
import importlib
from pathlib import Path
import queue
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from dart3s.comfy3d import ComfyError, Parameters
from dart3s.gui_jobs import Jobs, read_json, recent_runs, write_json


class GuiJobsTests(unittest.TestCase):
    def test_shutdown_cancels_owned_worker_and_clears_pending_after_server_stop(self):
        self.jobs._worker_done.set()
        write_json(self.jobs.pending_file, {'prompt_id': 'old'})
        previous = self.jobs.run_token
        with patch.object(self.jobs, 'stop_server', return_value={'ok': True, 'stopped_pids': [123]}) as stop:
            thread = self.jobs.shutdown()
            self.assertFalse(thread.daemon)
            thread.join(2)
            self.assertFalse(thread.is_alive())
            stop.assert_called_once()
        self.assertTrue(self.jobs._cancel_event.is_set())
        self.assertNotEqual(self.jobs.run_token, previous)
        self.assertEqual(read_json(self.jobs.pending_file), {})
        self.assertTrue(read_json(self.project / 'logs/gui-shutdown.json')['ok'])

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.project = Path(temp.name)
        self.image = self.project / 'input.png'
        self.image.touch()
        self.jobs = Jobs(self.project)

    def events_until(self, kind):
        events = []
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                event = self.jobs.events.get(timeout=.1)
            except queue.Empty:
                continue
            events.append(event)
            if event['kind'] == kind:
                return events
        self.fail(f'No {kind} event: {events}')

    def fail_after_submission(self, code):
        def fail(image, **kwargs):
            kwargs['progress']({'status': 'submitted', 'prompt_id': 'owned-job',
                                'metadata_path': str(self.project / 'generation.json')})
            raise ComfyError(code, 'Failure message', 'Actionable CLI hint')
        with patch('dart3s.gui_jobs.generate_3d', side_effect=fail):
            self.jobs.start(str(self.image), Parameters())
            return self.events_until('idle')

    def test_connection_failure_retains_id_and_hint_without_result(self):
        events = self.fail_after_submission('watch_timeout')
        self.assertEqual(read_json(self.jobs.pending_file)['prompt_id'], 'owned-job')
        failure = next(e for e in events if e['kind'] == 'error')
        self.assertTrue(failure['resumable'])
        self.assertIn('Actionable CLI hint', failure['message'])
        self.assertNotIn('result', [e['kind'] for e in events])

    def test_terminal_failure_clears_pending_id(self):
        events = self.fail_after_submission('execution_error')
        self.assertEqual(read_json(self.jobs.pending_file), {})
        self.assertFalse(next(e for e in events if e['kind'] == 'error')['resumable'])

    def test_cancel_scopes_both_requests_to_owned_prompt(self):
        self.jobs.busy = True
        self.jobs.prompt_id = 'owned-job'
        opener = Mock()
        opener.open.return_value.__enter__ = Mock(return_value=Mock())
        opener.open.return_value.__exit__ = Mock(return_value=False)
        with patch('dart3s.gui_jobs.build_opener', return_value=opener):
            self.jobs.cancel()
            self.events_until('cancel_requested')
        requests = [c.args[0] for c in opener.open.call_args_list]
        self.assertEqual([r.full_url for r in requests],
                         ['http://127.0.0.1:8188/queue', 'http://127.0.0.1:8188/interrupt'])
        self.assertEqual([json.loads(r.data) for r in requests],
                         [{'delete': ['owned-job']}, {'prompt_id': 'owned-job'}])

    def test_test_fixtures_do_not_appear_in_history(self):
        write_json(self.project / 'outputs/gui-integration-test/run/generation.json',
                   {'status': 'submitted', 'prompt_id': 'test-job'})
        write_json(self.project / 'outputs/real/run/generation.json',
                   {'status': 'submitted', 'prompt_id': 'real-job'})
        self.assertEqual([r['prompt_id'] for r in recent_runs(self.project)], ['real-job'])

    def test_apps_disable_intermediate_rendering_and_keep_final_preview(self):
        for module_name in ('dart3s.gui_jobs', 'Details3D.jobs'):
            with self.subTest(module=module_name):
                module = importlib.import_module(module_name)
                self.jobs = module.Jobs(self.project)
                result = Mock(glb_path=self.project / 'finished.glb')
                result.to_dict.return_value = {'glb_path': str(result.glb_path)}
                with patch.object(module, 'generate_3d', return_value=result) as generate, \
                     patch.object(self.jobs, 'render', return_value='finished.preview.png') as render:
                    self.jobs.start(str(self.image), Parameters())
                    events = self.events_until('idle')
                self.assertIs(generate.call_args.kwargs['visual_progress'], False)
                self.assertEqual(generate.call_args.kwargs['polygons'], Parameters().polygons)
                if module_name == 'dart3s.gui_jobs':
                    render.assert_called_once_with(result.glb_path, cancel_event=self.jobs._cancel_event)
                else:
                    render.assert_called_once_with(result.glb_path)
                self.assertIn('result', [e['kind'] for e in events])
                self.assertIn('preview', [e['kind'] for e in events])
                self.assertNotIn('intermediate_preview', [e['kind'] for e in events])

    def test_force_cancel_unlocks_and_late_old_worker_cannot_change_next_job(self):
        entered, release = threading.Event(), threading.Event()
        second = self.project / 'second.png'
        second.touch()
        old_result = Mock(glb_path=self.project / 'old.glb')
        old_result.to_dict.return_value = {'glb_path': 'old.glb'}
        new_result = Mock(glb_path=self.project / 'new.glb')
        new_result.to_dict.return_value = {'glb_path': 'new.glb'}
        def generate(image, **kwargs):
            if Path(image) == self.image:
                kwargs['progress']({'status': 'submitted', 'prompt_id': 'old-job'})
                entered.set()
                release.wait(10)  # Simulate an old client that is slow to exit.
                kwargs['progress']({'status': 'submitted', 'prompt_id': 'late-old-job', 'metadata_path': 'old.json'})
                return old_result
            kwargs['progress']({'status': 'submitted', 'prompt_id': 'new-job', 'metadata_path': 'new.json'})
            return new_result
        with patch('dart3s.gui_jobs.generate_3d', side_effect=generate), \
             patch.object(self.jobs, 'render', return_value='preview.png'), \
             patch.object(self.jobs, 'stop_server', return_value={'ok': True}) as stop:
            try:
                self.jobs.start(str(self.image), Parameters())
                self.assertTrue(entered.wait(2))
                old_done, old_token = self.jobs._worker_done, self.jobs.run_token
                self.jobs.cancel_requested = True  # Force remains available after graceful cancel.
                self.jobs.force_cancel()
                events = self.events_until('force_cancelled')
                self.assertFalse(self.jobs.busy)
                self.assertNotEqual(self.jobs.run_token, old_token)
                self.assertEqual(read_json(self.jobs.pending_file), {})
                stop.assert_called_once()
                self.jobs.start(str(second), Parameters())
                new_events = self.events_until('idle')
                self.assertEqual(next(e for e in new_events if e['kind'] == 'result')['result']['glb_path'], 'new.glb')
                release.set()
                self.assertTrue(old_done.wait(2))
                self.assertEqual(read_json(self.jobs.pending_file), {})
                self.assertEqual(self.jobs.prompt_id, 'new-job')
                self.assertEqual(self.jobs.metadata_path, 'new.json')
                self.assertFalse(self.jobs.busy)
                leftovers = []
                while not self.jobs.events.empty():
                    leftovers.append(self.jobs.events.get_nowait())
                self.assertFalse(any(e.get('job_token') == old_token for e in leftovers))
            finally:
                release.set()


if __name__ == '__main__':
    unittest.main()
