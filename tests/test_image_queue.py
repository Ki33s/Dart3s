import tempfile
from pathlib import Path
import unittest

from dart3s.image_queue import ImageQueue


class ImageQueueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'queue.json'
        self.queue = ImageQueue(self.path)

    def test_order_remove_and_completed_result_survive_restart(self):
        first = self.queue.add({'path': 'one.png'})
        second = self.queue.add({'path': 'two.png'})
        self.queue.move(second['id'], -1)
        self.assertEqual(self.queue.next()['id'], second['id'])
        self.queue.mark(second['id'], 'completed', result={'glb_path': 'two.glb'})
        reloaded = ImageQueue(self.path)
        self.assertEqual(reloaded.next()['id'], first['id'])
        self.assertEqual(reloaded.get(second['id'])['result']['glb_path'], 'two.glb')
        reloaded.remove(first['id'])
        self.assertIsNone(reloaded.next())

    def test_active_job_is_never_automatically_requeued_after_restart(self):
        first = self.queue.add({'path': 'one.png'})
        self.queue.mark(first['id'], 'running')
        self.queue.remove(first['id'])
        self.assertIsNotNone(self.queue.get(first['id']))
        reloaded = ImageQueue(self.path)
        self.assertEqual(reloaded.get(first['id'])['status'], 'paused')
        self.assertIsNone(reloaded.next())

    def test_obsolete_low_risk_is_hidden_until_recheck(self):
        from dart3s.risk_version import RISK_VERSION
        item = self.queue.add({'path': 'tree.png'})
        self.queue.mark(item['id'], 'completed', risk={'version': 1, 'level': 'low'})
        updated = ImageQueue(self.path)
        self.assertNotIn('risk', updated.get(item['id']))
        updated.mark(item['id'], 'completed', risk={'version': RISK_VERSION, 'level': 'high'})
        self.assertEqual(ImageQueue(self.path).get(item['id'])['risk']['level'], 'high')


if __name__ == '__main__':
    unittest.main()
