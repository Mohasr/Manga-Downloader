"""CBZ exporter for manga chapters.

CBZ is a ZIP archive containing images, commonly used by manga readers.
Images are stored in order with zero-padded filenames.
Includes ComicInfo.xml metadata for Komga/Kavita/Mihon compatibility.
"""

from __future__ import annotations

import os
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any


class CbzExporter:
    """Exports manga chapter images to a CBZ (Comic Book ZIP) file."""

    def __init__(self, compression: int = zipfile.ZIP_DEFLATED) -> None:
        self.compression = compression

    def export(self, image_paths: list[str], output_path: str,
               metadata: dict[str, Any] | None = None) -> str:
        """Export images to CBZ with optional ComicInfo.xml metadata.

        Args:
            image_paths: Ordered list of image file paths.
            output_path: Output CBZ file path.
            metadata: Optional dict with keys: title, chapter, volume,
                      author, summary, genre, cover_image_path.
        """
        if not image_paths:
            raise RuntimeError("No images provided for CBZ export")

        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        if output.suffix.lower() != ".cbz":
            output = output.with_suffix(".cbz")

        valid_paths = [p for p in image_paths if os.path.exists(p)]
        if not valid_paths:
            raise RuntimeError("None of the provided image paths exist")

        with zipfile.ZipFile(output, mode="w", compression=self.compression) as zf:
            for i, img_path in enumerate(valid_paths, 1):
                ext = Path(img_path).suffix.lower() or ".jpg"
                zf.write(img_path, arcname=f"{i:04d}{ext}")

            # ComicInfo.xml
            if metadata:
                comic_info = self._build_comic_info(metadata)
                zf.writestr("ComicInfo.xml", comic_info.encode("utf-8"))

            # Cover image (Komga uses first image by default, but can be explicit)
            if metadata and metadata.get("cover_image_path"):
                cover_path = metadata["cover_image_path"]
                if os.path.exists(cover_path):
                    ext = Path(cover_path).suffix.lower() or ".jpg"
                    zf.write(cover_path, arcname=f"Cover{ext}")

        # Integrity check
        with zipfile.ZipFile(output, "r") as zf:
            bad = zf.testzip()
            if bad is not None:
                raise RuntimeError(f"CBZ archive is corrupt: {bad}")

        return str(output)

    @staticmethod
    def _build_comic_info(metadata: dict[str, Any]) -> str:
        root = ET.Element("ComicInfo", xmlns_xsd="http://www.w3.org/2001/XMLSchema",
                          xmlns_xsi="http://www.w3.org/2001/XMLSchema-instance")

        fields = {
            "Title": metadata.get("title"),
            "Series": metadata.get("series"),
            "Number": str(metadata.get("chapter", "")) if metadata.get("chapter") else None,
            "Volume": str(metadata.get("volume", "")) if metadata.get("volume") else None,
            "Writer": metadata.get("author"),
            "Penciller": metadata.get("artist"),
            "Summary": metadata.get("summary"),
            "Genre": ", ".join(metadata["genre"]) if isinstance(metadata.get("genre"), list) else metadata.get("genre"),
            "PageCount": str(len(metadata.get("image_paths", []))),
            "Manga": "Yes",
            "LanguageISO": metadata.get("language", "ar"),
        }

        for tag, value in fields.items():
            if value:
                ET.SubElement(root, tag).text = value

        ET.indent(root)
        return ET.tostring(root, encoding="unicode", xml_declaration=True)
