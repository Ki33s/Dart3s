"""Select a whole bounded structure before Trellis shape inference."""
import json
import logging
from pathlib import Path

from comfy_api.latest import io
from dart3s.structure_budget import select_structure


class Dart3sStructureBudget(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(node_id='Dart3sStructureBudget', display_name='Dart3s Structure Budget + Retry',
            category='Dart3s/structure', inputs=[
                io.Voxel.Input('voxel'), io.Model.Input('model'), io.Vae.Input('vae'),
                io.Conditioning.Input('positive'), io.Conditioning.Input('negative'),
                io.Latent.Input('latent'), io.Int.Input('seed', default=56, min=0, max=2**64-1),
                io.Int.Input('steps', default=6, min=1, max=10000),
                io.Float.Input('cfg', default=7.5, min=0, max=100),
                io.String.Input('sampler_name', default='euler'), io.String.Input('scheduler', default='normal'),
                io.Float.Input('denoise', default=1.0, min=0, max=1),
                io.Combo.Input('resolution', options=['32', '64'], default='32'),
                io.Int.Input('max_elements', default=4096, min=0, max=32768),
                io.Int.Input('retries', default=2, min=0, max=4),
                io.String.Input('report_path', default='')], outputs=[io.Voxel.Output('voxel')])

    @classmethod
    def execute(cls, voxel, model, vae, positive, negative, latent, seed, steps, cfg,
                sampler_name, scheduler, denoise, resolution, max_elements, retries, report_path):
        import nodes
        import comfy.model_management as mm
        from comfy_extras.nodes_trellis2 import VaeDecodeStructureTrellis2

        def regenerate(next_seed):
            # The structure VAE was force-loaded for the candidate just decoded.
            # Release it before diffusion instead of making it compete for 8 GB.
            mm.unload_model_and_clones(vae.patcher)
            mm.soft_empty_cache()
            samples = nodes.common_ksampler(model, next_seed, steps, cfg, sampler_name,
                                           scheduler, positive, negative, latent, denoise=denoise)[0]
            return VaeDecodeStructureTrellis2.execute(samples, vae, resolution)[0]

        def record(report):
            logging.info('Dart3s structure budget: %s', json.dumps(report))
            if report_path:
                path = Path(report_path).resolve()
                root = Path(__file__).resolve().parents[4] / 'outputs'
                if not path.is_relative_to(root.resolve()):
                    raise ValueError('Structure report must be inside Dart3s outputs')
                path.parent.mkdir(parents=True, exist_ok=True)
                temp = path.with_suffix('.tmp')
                temp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
                temp.replace(path)

        selected = select_structure(voxel, seed=seed, limit=max_elements, retries=retries,
            count=lambda v: v.data.bool().reshape(v.data.shape[0], -1).sum(1).max().item(),
            regenerate=regenerate, record=record, check_cancel=mm.throw_exception_if_processing_interrupted)
        return io.NodeOutput(selected)
