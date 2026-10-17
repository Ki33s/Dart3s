"""Decode an image/Windows clipboard with the portable runtime's Pillow.

The native Tk UI uses its own Python and reads the resulting bounded PNG.
The generation input is an immutable PNG snapshot of the selected image.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from uuid import uuid4

from PIL import Image, ImageGrab, ImageOps


def prepare_image(*, source: Path | None, clipboard: bool, directory: Path,
                  width: int = 440, height: int = 340) -> dict:
    original = str(source) if source else "Из буфера обмена"
    if clipboard:
        item = ImageGrab.grabclipboard()
        if isinstance(item, list):
            source = next((Path(p) for p in item if Path(p).is_file()
                           and Path(p).suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}), None)
            if source is None:
                raise ValueError("В буфере обмена нет файла изображения.")
            original = str(source)
        elif isinstance(item, Image.Image):
            image = item.copy()
        else:
            raise ValueError("В буфере обмена нет картинки. Скопируйте изображение или его файл.")
    if source is not None:
        with Image.open(source) as opened:
            opened.load()
            image = ImageOps.exif_transpose(opened).copy()
    elif not clipboard:
        raise ValueError("Выберите изображение.")
    image = image.convert("RGBA" if "A" in image.getbands() else "RGB")
    directory.mkdir(parents=True, exist_ok=True)
    name = uuid4().hex
    input_path = directory / (name + ".png")
    preview_path = directory / (name + ".thumb.png")
    image.save(input_path)
    preview = image.copy()
    preview.thumbnail((width, height), Image.Resampling.LANCZOS)
    preview.save(preview_path)
    return {"path": str(input_path.resolve()), "preview": str(preview_path.resolve()),
            "original": original, "size": list(image.size)}


def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--source", type=Path)
    group.add_argument("--clipboard", action="store_true")
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--width", type=int, default=440)
    parser.add_argument("--height", type=int, default=340)
    args = parser.parse_args()
    try:
        result = prepare_image(source=args.source, clipboard=args.clipboard, directory=args.directory,
                               width=args.width, height=args.height)
        print(json.dumps({"ok": True, "data": result}, ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
