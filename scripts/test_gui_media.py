"""Pillow tests using the existing portable Python, without touching clipboard."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

spec = importlib.util.spec_from_file_location("gui_image_bridge", Path(__file__).with_name("gui_image_bridge.py"))
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


class ImageTests(unittest.TestCase):
    def test_file_snapshot_alpha_and_bounded_preview(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "исходная картинка.png"
            Image.new("RGBA", (900, 600), (20, 80, 160, 100)).save(source)
            data = bridge.prepare_image(source=source, clipboard=False, directory=root/"cache")
            with Image.open(data["path"]) as image:
                self.assertEqual(image.size, (900, 600))
                self.assertEqual(image.getpixel((0, 0)), (20, 80, 160, 100))
            with Image.open(data["preview"]) as image:
                self.assertLessEqual(image.width, 440)
                self.assertLessEqual(image.height, 340)
            self.assertEqual(data["original"], str(source))

    def test_clipboard_image_is_saved_without_using_real_clipboard(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(bridge.ImageGrab, "grabclipboard", return_value=Image.new("RGB", (64, 32), "red")):
                data = bridge.prepare_image(source=None, clipboard=True, directory=Path(directory))
                self.assertEqual(data["size"], [64, 32])
                self.assertEqual(data["original"], "Из буфера обмена")
                self.assertTrue(Path(data["path"]).is_file())

    def test_empty_clipboard_has_clear_error(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(bridge.ImageGrab, "grabclipboard", return_value=None):
                with self.assertRaisesRegex(ValueError, "нет картинки"):
                    bridge.prepare_image(source=None, clipboard=True, directory=Path(directory))


if __name__ == "__main__":
    unittest.main()
