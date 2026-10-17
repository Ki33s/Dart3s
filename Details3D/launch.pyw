"""Independent Windows entry point for the small image-to-3D application."""
from pathlib import Path
import sys
import traceback

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
try:
    from Details3D.gui import main
    main()
except Exception:
    log = root / 'Details3D/startup-error.log'
    log.write_text(traceback.format_exc(), encoding='utf-8')
    import ctypes
    ctypes.windll.user32.MessageBoxW(None, f'Не удалось открыть Детали.\nЖурнал: {log}', 'Детали', 0x10)
