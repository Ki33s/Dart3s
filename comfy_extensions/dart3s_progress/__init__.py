"""Dart3s lightweight native-app progress milestones (ComfyUI V3)."""
from comfy_api.latest import ComfyExtension
from .nodes import Dart3sProgressVoxel, Dart3sProgressMesh, Dart3sProgressTexture, Dart3sGeometryReady
from .structure import Dart3sStructureBudget
from .early_stage import Dart3sEarlyStage


class Dart3sProgressExtension(ComfyExtension):
    async def get_node_list(self):
        return [Dart3sProgressVoxel, Dart3sProgressMesh, Dart3sProgressTexture, Dart3sGeometryReady, Dart3sStructureBudget, Dart3sEarlyStage]


async def comfy_entrypoint():
    from dart3s.benchmark_timings import install
    install()
    return Dart3sProgressExtension()
