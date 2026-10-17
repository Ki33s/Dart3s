"""Preview delivery validates paths and never repeats an already shown frame."""
import json
from pathlib import Path
import tempfile
import unittest
from dart3s.preview_watch import PreviewMonitor


class PreviewWatchTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.project = Path(temp.name)
        self.directory = self.project / 'outputs/job/previews'
        self.directory.mkdir(parents=True)
        self.image = self.directory / 'prepared.png'
        self.image.write_bytes(b'fixture; decoding belongs to Tk, not monitor')
        self.events = []
        self.monitor = PreviewMonitor(self.project, lambda kind, **fields: self.events.append((kind, fields)), 'owned-run')
        self.monitor.configure(self.directory)

    def write(self, **changes):
        record = dict(version=1, revision=1, stage='prepared', image=str(self.image), size=256, triangles=9982)
        record.update(changes)
        (self.directory / 'latest.json').write_text(json.dumps(record), encoding='utf-8')

    def test_revision_is_delivered_once_with_job_identity(self):
        self.write()
        self.monitor.poll()
        self.monitor.poll()
        self.assertEqual(len(self.events), 1)
        self.assertEqual(self.events[0][1]['token'], 'owned-run')
        self.assertEqual(self.events[0][1]['snapshot']['triangles'], 9982)
        self.write(revision=3, stage='textured')
        self.monitor.poll()
        self.assertEqual(len(self.events), 2)

    def test_outside_path_is_ignored_without_acknowledging_revision(self):
        outside = self.project / 'other.png'
        outside.touch()
        self.write(image=str(outside))
        self.monitor.poll()
        self.assertFalse(self.events)
        self.write()
        self.monitor.poll()
        self.assertEqual(len(self.events), 1)

    def test_missing_or_partial_manifest_does_not_fail_job(self):
        (self.directory / 'latest.json').write_text('{', encoding='utf-8')
        self.monitor.poll()
        self.write(image=str(self.directory / 'not-ready.png'))
        self.monitor.poll()
        self.assertFalse(self.events)

    def test_directory_outside_project_outputs_is_rejected(self):
        self.monitor.configure(self.project / 'unrelated')
        self.assertEqual(self.monitor.directory, self.directory)


if __name__ == '__main__':
    unittest.main()
