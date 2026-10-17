"""Pass-through observations: no sampler, VAE, GPU renderer or file export."""
import logging
from pathlib import Path
import sys

from comfy_api.latest import io

PROJECT = Path(__file__).resolve().parents[4]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))
from dart3s.progress_preview import submit_preview


def observe(*args, **kwargs):
    try:
        submit_preview(*args, **kwargs)
    except Exception:
        logging.exception("Nonfatal Dart3s progress-preview error")


def settings():
    return [io.String.Input("preview_dir", default="", tooltip="Run folder inside Dart3s outputs; empty disables previews."),
            io.Int.Input("preview_size", default=256, min=128, max=512, step=128)]


class PreviewValidation:
    @classmethod
    def validate_inputs(cls, preview_size):
        return True if type(preview_size) is int and preview_size in (128, 256, 512) else "Preview size must be 128, 256 or 512"


class Dart3sProgressVoxel(PreviewValidation, io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(node_id="Dart3sProgressVoxel", display_name="Dart3s Structure Progress", category="Dart3s/progress",
                         inputs=[io.Voxel.Input("voxel"), *settings(),
                                 io.Combo.Input("model_frame", options=["z_up", "y_up"], default="z_up", optional=True, advanced=True)],
                         outputs=[io.Voxel.Output("voxel")])

    @classmethod
    def execute(cls, voxel, preview_dir, preview_size, model_frame="z_up"):
        observe("structure", voxel, preview_dir, preview_size, model_frame=model_frame)
        return io.NodeOutput(voxel)


class Dart3sProgressMesh(PreviewValidation, io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(node_id="Dart3sProgressMesh", display_name="Dart3s Mesh Progress", category="Dart3s/progress",
                         inputs=[io.Mesh.Input("mesh"), io.Combo.Input("stage", options=["geometry", "prepared"], default="geometry"), *settings()],
                         outputs=[io.Mesh.Output("mesh")])

    @classmethod
    def execute(cls, mesh, stage, preview_dir, preview_size):
        # Publish the cheap gray view before allowing downstream work to run.
        # Waiting releases the GIL and is bounded; a failed preview stays nonfatal.
        observe(stage, mesh, preview_dir, preview_size, wait_for_publication=True)
        return io.NodeOutput(mesh)


class Dart3sGeometryReady(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(node_id="Dart3sGeometryReady", display_name="Dart3s Geometry Before Textures",
                         category="Dart3s/progress",
                         inputs=[io.Latent.Input("samples"), io.Mesh.Input("mesh")],
                         outputs=[io.Latent.Output(display_name="LATENT")])

    @classmethod
    def execute(cls, samples, mesh):
        # This dependency forces mesh decoding and its gray publication before
        # texture sampling. The calculated latent is untouched.
        return io.NodeOutput(samples)


class Dart3sProgressTexture(PreviewValidation, io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(node_id="Dart3sProgressTexture", display_name="Dart3s Texture Progress", category="Dart3s/progress",
                         inputs=[io.Image.Input("base_color"), io.Mesh.Input("mesh"), *settings()], outputs=[io.Image.Output("base_color")])

    @classmethod
    def execute(cls, base_color, mesh, preview_dir, preview_size):
        observe("textured", mesh, preview_dir, preview_size, texture=base_color)
        return io.NodeOutput(base_color)
