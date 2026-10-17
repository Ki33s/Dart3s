"""Record sampler readiness without altering its latent or doing GPU work."""
import json
from pathlib import Path
import time

from comfy_api.latest import io


class Dart3sEarlyStage(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(node_id='Dart3sEarlyStage', display_name='Dart3s Early Stage Clock',
            category='Dart3s/structure', inputs=[io.Latent.Input('samples'), io.Model.Input('model'),
                io.Conditioning.Input('positive'), io.Conditioning.Input('negative'),
                io.String.Input('directory', default=''), io.Combo.Input('phase', options=['3','18'], default='3')],
            outputs=[io.Latent.Output()])

    @classmethod
    def execute(cls, samples, model, positive, negative, directory, phase):
        path = Path(directory).resolve()
        root = Path(__file__).resolve().parents[4] / 'outputs'
        if not path.is_relative_to(root.resolve()):
            raise ValueError('Early stage timestamps must be inside Dart3s outputs')
        path.mkdir(parents=True, exist_ok=True)
        target = path / f'early-stage-{phase}.json'
        temp = target.with_suffix('.tmp')
        temp.write_text(json.dumps({'node':phase, 'started':time.monotonic()}),encoding='utf-8')
        temp.replace(target)
        return io.NodeOutput(samples)
