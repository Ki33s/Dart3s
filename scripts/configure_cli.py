"""Reapply the project's small Comfy CLI fixes after upgrades.

Comfy CLI currently computes DEFAULT_CONFIG from expanduser('~') and ignores
Windows LOCALAPPDATA. Honoring LOCALAPPDATA lets this project's launchers keep
job state in C:\\AI\\Dart3s\\.state without changing HOME or USERPROFILE.
Only the package installed in this project is changed; the original is backed up.

The schema-aware widget writer must also update an existing widgets_values_named
map: the frontend restores that map before the positional widgets_values array.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / ".venv-cli/Lib/site-packages/comfy_cli/constants.py"
OLD = '    OS.WINDOWS: os.path.join(os.path.expanduser("~"), "AppData", "Local", "comfy-cli"),'
NEW = '    OS.WINDOWS: os.path.join(os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local"), "comfy-cli"),'
WIDGET_TARGET = ROOT / ".venv-cli/Lib/site-packages/comfy_cli/cql/engine.py"
WIDGET_OLD = '''    widgets[widget_idx] = value
    node["widgets_values"] = widgets
    return warnings
'''
WIDGET_NEW = '''    widgets[widget_idx] = value
    node["widgets_values"] = widgets
    # Dart3s: named widget restoration takes precedence in the frontend.
    # Keep an existing map in sync without adding one to legacy workflows.
    named_values = node.get("widgets_values_named")
    if isinstance(named_values, dict):
        named_values[input_name] = value
    return warnings
'''


def configure(*, check: bool = False) -> dict:
    patches = [(TARGET, OLD, NEW, "Windows config honors LOCALAPPDATA"),
               (WIDGET_TARGET, WIDGET_OLD, WIDGET_NEW, "Widget writes synchronize existing named values")]
    plans = []
    # Check every known upstream anchor before writing any package file.
    for path, old, new, label in patches:
        target = path.resolve()
        target.relative_to(ROOT.resolve())
        original = target.read_bytes()
        text = original.decode("utf-8")
        installed = text.count(new) == 1
        if not installed and text.count(old) != 1:
            raise RuntimeError(f"Comfy CLI {target.name} changed upstream. Review {label} before patching.")
        updated = text if installed else text.replace(old, new, 1)
        compile(updated, str(target), "exec")
        plans.append((target, original, updated, installed, label))
    results = []
    for target, original, updated, installed, label in plans:
        result = {"ok": installed or not check, "changed": not installed and not check,
                  "path": str(target), "fix": label}
        if not installed and not check:
            backup = target.with_suffix(".py.pre-dart3s")
            if not backup.exists():
                backup.write_bytes(original)
            target.write_bytes(updated.encode("utf-8"))
            result["backup"] = str(backup)
        results.append(result)
    if not check:
        (ROOT / ".state").mkdir(exist_ok=True)
    return {"ok": all(r["ok"] for r in results), "changed": any(r["changed"] for r in results),
            "fixes": results}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Check without changing the package.")
    args = parser.parse_args()
    try:
        result = configure(check=args.check)
    except (OSError, ValueError, RuntimeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
