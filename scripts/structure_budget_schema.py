"""Export actual custom-node schema without launching ComfyUI or loading models."""
import json
from pathlib import Path
import sys

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
sys.path.insert(0, str(root / 'ComfyUI_windows_portable/ComfyUI'))
from custom_nodes.dart3s_progress.structure import Dart3sStructureBudget
from custom_nodes.dart3s_progress.early_stage import Dart3sEarlyStage

catalog = json.loads((root / 'logs/object_info-progress.json').read_text(encoding='utf-8'))
catalog['Dart3sStructureBudget'] = Dart3sStructureBudget.GET_NODE_INFO_V1()
catalog['Dart3sEarlyStage'] = Dart3sEarlyStage.GET_NODE_INFO_V1()
path = root / 'logs/object_info-structure-budget.json'
path.write_text(json.dumps(catalog), encoding='utf-8')
print(json.dumps({'path': str(path), 'node': catalog['Dart3sStructureBudget']}))
