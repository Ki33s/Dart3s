# External components

This repository contains the Dart3s application and project scripts.
ComfyUI, Comfy CLI, model weights, generated meshes, source artwork and the
Art3dViewer binary are installed separately and are not included.

- [ComfyUI](https://github.com/Comfy-Org/ComfyUI): upstream license applies.
- [Comfy CLI](https://github.com/Comfy-Org/comfy-cli): upstream GPL-3.0-only.
- [Official workflow templates](https://github.com/Comfy-Org/workflow_templates):
  an upstream template snapshot is included in `downloads/template.original.json`.
  Its upstream terms apply; the builder records the source hash.
- Models are downloaded from the Comfy-Org Hugging Face repositories linked
  in `downloads/models.manifest.json`. Each model retains its own terms.
- Art3dViewer is an optional external viewer configured with `DART3S_VIEWER_EXE`.
