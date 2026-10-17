"""CPU-only risk tests: image identity, explainable features and no outputs."""
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image, ImageDraw
from dart3s.image_risk import analyze_image


class RiskTests(unittest.TestCase):
    def test_simple_contours_are_not_automatically_low_and_inputs_are_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            for mode, background, fill in [('RGBA', (0, 0, 0, 0), (120, 80, 40, 255)),
                                           ('RGB', (255, 255, 255), (120, 80, 40))]:
                path = Path(directory) / (mode + '.png')
                image = Image.new(mode, (256, 256), background)
                ImageDraw.Draw(image).rectangle((80, 30, 175, 225), fill=fill)
                image.save(path)
                before = hashlib.sha256(path.read_bytes()).hexdigest()
                risk = analyze_image(path)
                self.assertNotEqual(risk['level'], 'low')
                self.assertTrue(risk['reasons'])
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), before)
            self.assertEqual(len(list(Path(directory).iterdir())), 2)

    def test_many_holes_and_thin_connections_raise_risk(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'thin-grid.png'
            image = Image.new('RGBA', (256, 256))
            draw = ImageDraw.Draw(image)
            for point in range(30, 230, 25):
                draw.line((point, 30, point, 205), fill=(60, 30, 10, 255), width=3)
                draw.line((30, point, 205, point), fill=(60, 30, 10, 255), width=3)
            image.save(path)
            risk = analyze_image(path)
            self.assertEqual(risk['level'], 'high')
            self.assertGreater(risk['metrics']['holes'], 3)

    def test_empty_transparency_is_unknown_not_easy(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'empty.png'
            Image.new('RGBA', (50, 50)).save(path)
            self.assertEqual(analyze_image(path)['level'], 'unknown')

    def test_actual_tree_is_not_low_without_history_and_high_with_measured_history(self):
        import json
        root = Path(__file__).resolve().parents[1]
        source = root / '.state/gui/inputs/b4f722ebdda14fc785611004804b710d.png'
        if not source.exists():
            self.skipTest('Tree regression fixture unavailable')
        self.assertNotEqual(analyze_image(source)['level'], 'low')
        from dart3s.risk_history import fingerprint
        parameters = {'quality': 'fast'}
        index = {'rows': [{'fingerprint': fingerprint(source), 'seconds': 458.749,
                          'parameters': parameters, 'metadata_path': 'fixture'}]}
        measured = analyze_image(source, history_index=index, parameters=parameters)
        self.assertEqual(measured['level'], 'high')
        self.assertEqual(measured['basis'], 'measured_history')
        # A different quality must not inherit a timing under incompatible settings.
        other = analyze_image(source, history_index=index, parameters={'quality': 'standard'})
        self.assertEqual(other['basis'], 'image_features')

    def test_history_uses_recent_median_instead_of_one_old_slowdown(self):
        import json
        from dart3s.risk_history import fingerprint
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'object.png'
            image = Image.new('RGB', (50, 50), 'white')
            ImageDraw.Draw(image).rectangle((10, 10, 40, 40), fill='brown')
            image.save(path)
            index = {'rows': [dict(fingerprint=fingerprint(path), seconds=seconds, parameters={}, metadata_path='fixture')
                              for seconds in (92, 95, 94, 420)]}
            self.assertEqual(analyze_image(path, history_index=index, parameters={})['level'], 'low')

    def test_history_index_excludes_partly_cached_neural_generations(self):
        import json
        from dart3s.risk_history import build_index
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source.png'
            Image.new('RGB', (30, 30), 'brown').save(source)
            for name, cached in [('full', []), ('cached', ['3']), ('refinement_cached', ['23'])]:
                run = root / 'outputs' / name
                run.mkdir(parents=True)
                metadata = dict(status='completed', image=str(source), parameters={}, prompt_id=name)
                (run / 'generation.json').write_text(json.dumps(metadata), encoding='utf-8')
                history = {name: {'status': {'messages': [
                    ['execution_start', {'timestamp': 1000}], ['execution_cached', {'nodes': cached}],
                    ['execution_success', {'timestamp': 101000}]]}}}
                (run / 'history.json').write_text(json.dumps(history), encoding='utf-8')
            output = root / 'index.json'
            self.assertEqual(build_index(root, output)['executions'], 1)
            index = json.loads(output.read_text(encoding='utf-8'))
            self.assertEqual(index['rows'][0]['prompt_id'], 'full')


if __name__ == '__main__':
    unittest.main(verbosity=2)
