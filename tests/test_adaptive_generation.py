import json
from pathlib import Path
import tempfile
from io import BytesIO
import unittest
from unittest.mock import Mock, patch

from dart3s.adaptive_generation import EarlyBudget, EarlyWatch, generate_adaptive, reset_owned_server
from dart3s.comfy3d import ComfyError, GenerationResult


class WatchTests(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.budget = EarlyBudget(180, clock=lambda: self.now)
        self.watch = EarlyWatch(self.budget)

    def test_silent_shape_stops_without_waiting_for_next_progress(self):
        self.watch.event({'type': 'executing', 'node': '18'})
        self.now = 46
        with self.assertRaises(ComfyError) as caught:
            self.watch.check()
        self.assertEqual(caught.exception.code, 'early_risk')

    def test_missing_sampler_start_uses_prerequisite_before_first_iteration(self):
        self.watch.event({'type':'progress', 'node':'91', 'completed':1, 'total':1})
        self.now = 46
        with self.assertRaises(ComfyError):
            self.watch.check()

    def test_missing_structure_start_uses_conditioning_ready(self):
        self.watch.event({'type':'progress', 'node':'315', 'completed':1, 'total':1})
        self.now = 76
        with self.assertRaises(ComfyError):
            self.watch.check()

    def test_late_second_step_does_not_hide_timeout(self):
        self.watch.event({'type':'progress', 'node':'91', 'completed':1, 'total':1})
        self.now = 46
        with self.assertRaises(ComfyError):
            self.watch.event({'type':'progress', 'node':'18', 'completed':2, 'total':8})

    def test_nullable_progress_is_safe(self):
        self.watch.event({'type':'progress', 'node':'18', 'completed':None, 'total':None})
        self.assertFalse(self.watch.passed)

    def test_server_clock_detects_slow_first_step_before_cli_events(self):
        with tempfile.TemporaryDirectory() as folder:
            self.watch.timing_dir = Path(folder)
            (Path(folder)/'early-stage-18.json').write_text(json.dumps({'node':'18','started':0}))
            self.now = 46
            with self.assertRaises(ComfyError):
                self.watch.check()

    def test_stale_cli_event_cannot_move_timer_back_to_structure(self):
        with tempfile.TemporaryDirectory() as folder:
            self.watch.timing_dir = Path(folder)
            (Path(folder)/'early-stage-18.json').write_text(json.dumps({'node':'18','started':0}))
            self.now = 20
            self.watch.event({'type':'progress','node':'3','completed':6,'total':6})
            self.assertEqual(self.watch.node,'18')
            self.assertEqual(self.watch.stage_started,0)

    def test_passed_two_steps_allows_long_textures(self):
        self.watch.event({'type': 'executing', 'node': '18'})
        self.now = 14
        self.watch.event({'type': 'progress', 'node': '18', 'completed': 2, 'total': 8})
        self.now = 600
        self.watch.check()
        self.assertEqual(self.budget.elapsed(), 14)

    def test_structure_deadline(self):
        self.watch.event({'type': 'executing', 'node': '3'})
        self.now = 76
        with self.assertRaises(ComfyError):
            self.watch.check()

    def test_shared_budget_covers_second_attempt(self):
        self.watch.event({'type': 'executing', 'node': '3'})
        self.now = 175
        second = EarlyWatch(self.budget)
        second.event({'type': 'executing', 'node': '3'})
        self.now = 181
        with self.assertRaises(ComfyError):
            second.check()


class RetryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.meta = self.root / 'generation.json'
        self.meta.write_text(json.dumps({'prompt_id': 'owned'}))
        self.result = GenerationResult(self.root/'model.glb', 'new', self.meta, ())

    def error(self, code='early_risk', details=None):
        error = ComfyError(code, 'slow', details=details)
        error.metadata_path = self.meta
        return error

    def test_retry_changes_only_structure_seed_and_resets_before_next(self):
        order = []
        def generate(image, **kwargs):
            order.append(('generate', kwargs['structure_seed']))
            self.assertEqual(kwargs['shape_seed'], 42)
            self.assertEqual(kwargs['texture_seed'], 43)
            self.assertEqual(kwargs['structure_retries'], 0)
            if len(order) == 1:
                raise self.error()
            return self.result
        def reset(*args):
            order.append(('reset',))
            return {'ok': True}
        with patch('dart3s.adaptive_generation.reset_owned_server', side_effect=reset):
            result = generate_adaptive('image.png', seconds=180, generate_once=generate,
                structure_seed=56, shape_seed=42, texture_seed=43, structure_retries=4)
        self.assertIs(result, self.result)
        self.assertEqual(order, [('generate', 56), ('reset',), ('generate', 57)])

    def test_two_rejections_become_high_risk_not_third_attempt(self):
        generate = Mock(side_effect=self.error())
        with patch('dart3s.adaptive_generation.reset_owned_server', return_value={'ok': True}) as reset:
            with self.assertRaises(ComfyError) as caught:
                generate_adaptive('image', seconds=180, generate_once=generate)
        self.assertEqual(caught.exception.code, 'high_risk')
        self.assertEqual(generate.call_count, 2)
        self.assertEqual(reset.call_count, 2)

    def test_count_limit_rejection_is_retryable(self):
        generate = Mock(side_effect=[self.error('execution_error', {'exception_type':
            'dart3s.structure_budget.StructureBudgetExceeded'}), self.result])
        with patch('dart3s.adaptive_generation.reset_owned_server', return_value={'ok': True}):
            self.assertIs(generate_adaptive('image', seconds=180, generate_once=generate), self.result)

    def test_real_cli_nested_execution_details_become_high_risk(self):
        generate = Mock(side_effect=self.error('execution_error', {'status':'error','details':{
            'node_type':'Dart3sStructureBudget','exception_type':'dart3s.structure_budget.StructureBudgetExceeded',
            'exception_message':'Слишком сложная структура: 4321'}}))
        with patch('dart3s.adaptive_generation.reset_owned_server', return_value={'ok':True}) as reset:
            with self.assertRaises(ComfyError) as caught:
                generate_adaptive('image', seconds=180, generate_once=generate)
        self.assertEqual(caught.exception.code,'high_risk')
        self.assertEqual(generate.call_count,2)
        self.assertEqual(reset.call_count,2)

    def test_other_errors_and_user_cancel_never_retry(self):
        for code in ['cancelled', 'execution_error', 'server_died']:
            generate = Mock(side_effect=self.error(code))
            with patch('dart3s.adaptive_generation.reset_owned_server') as reset:
                with self.assertRaises(ComfyError) as caught:
                    generate_adaptive('image', seconds=180, generate_once=generate)
            self.assertEqual(caught.exception.code, code)
            self.assertEqual(generate.call_count, 1)
            reset.assert_not_called()

    def test_cleanup_failure_stops_instead_of_submitting_again(self):
        generate = Mock(side_effect=self.error())
        with patch('dart3s.adaptive_generation.reset_owned_server', side_effect=ComfyError('risk_cleanup_failed', 'busy')):
            with self.assertRaises(ComfyError) as caught:
                generate_adaptive('image', seconds=180, generate_once=generate)
        self.assertEqual(caught.exception.code, 'risk_cleanup_failed')
        self.assertEqual(generate.call_count, 1)

    def test_graceful_cancel_disallows_next_seed(self):
        generate = Mock(side_effect=self.error())
        with patch('dart3s.adaptive_generation.reset_owned_server', return_value={'ok':True}):
            with self.assertRaises(ComfyError) as caught:
                generate_adaptive('image', seconds=180, generate_once=generate, retry_allowed=lambda: False)
        self.assertEqual(caught.exception.code, 'cancelled')
        self.assertEqual(generate.call_count, 1)

    def test_other_clients_jobs_are_not_terminated(self):
        for q in [{'queue_running': [[1,'someone-else']], 'queue_pending': []},
                  {'queue_running': [[1,'owned']], 'queue_pending': [[2,'next']]}]:
            opener = Mock()
            opener.open.return_value = BytesIO(json.dumps(q).encode())
            with patch('dart3s.adaptive_generation.build_opener', return_value=opener), \
                 patch('dart3s.cli_stream.run_cancellable') as process:
                with self.assertRaises(ComfyError) as caught:
                    reset_owned_server(self.root, 'http://127.0.0.1:8188', 'owned')
                self.assertEqual(caught.exception.code, 'risk_cleanup_failed')
                process.assert_not_called()


if __name__ == '__main__':
    unittest.main()
