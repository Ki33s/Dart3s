"""Launch the native UI, recording startup errors without a terminal window."""
from pathlib import Path
import sys
import traceback

project = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project))
try:
    from dart3s.gui import main
    main()
except Exception:
    log = project / "logs/gui-startup-error.log"
    log.parent.mkdir(exist_ok=True)
    log.write_text(traceback.format_exc(), encoding="utf-8")
    import ctypes
    ctypes.windll.user32.MessageBoxW(None, f"Не удалось запустить Dart3s.\nЖурнал: {log}", "Dart3s", 0x10)
