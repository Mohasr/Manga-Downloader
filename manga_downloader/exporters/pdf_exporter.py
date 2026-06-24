"""PDF exporter for manga chapters using Pillow.

Converts a collection of manga page images into a single PDF file.
Handles RGB conversion, large images, and corrupted image recovery.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL import Image


class PdfExporter:
    """Exports manga chapter images to a PDF file."""

    def __init__(
        self,
        quality: int = 95,
        max_image_size: int = 100_000_000,
        skip_corrupted: bool = True,
    ) -> None:
        """Initialize the PDF exporter.

        Args:
            quality: JPEG quality for image compression in PDF.
            max_image_size: Maximum pixel count before downscaling (width * height).
            skip_corrupted: Skip corrupted images instead of failing.
        """
        self.quality = quality
        self.max_image_size = max_image_size
        self.skip_corrupted = skip_corrupted

    def export(self, image_paths: list[str], output_path: str) -> str:
        """Export a list of images to a single PDF file.

        Args:
            image_paths: Ordered list of image file paths.
            output_path: Path for the output PDF file.

        Returns:
            The output path on success.

        Raises:
            RuntimeError: If no valid images were found.
            FileNotFoundError: If an image path doesn't exist and skip_corrupted is False.
        """
        if not image_paths:
            raise RuntimeError("No images provided for PDF export")

        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)

        valid_images: list[Image.Image] = []

        for path in image_paths:
            img = self._load_image(path)
            if img is not None:
                valid_images.append(img)

        if not valid_images:
            raise RuntimeError("No valid images found for PDF export")

        first = valid_images[0]
        first.save(
            output,
            save_all=True,
            append_images=valid_images[1:],
            optimize=True,
            quality=self.quality,
        )

        for img in valid_images:
            img.close()

        return str(output)

    def _load_image(self, path: str) -> Image.Image | None:
        """Load a single image, handling errors and conversion.

        Args:
            path: Path to the image file.

        Returns:
            PIL Image object or None if corrupted and skip_corrupted is True.
        """
        p = Path(path)
        if not p.exists():
            if self.skip_corrupted:
                return None
            raise FileNotFoundError(f"Image not found: {path}")

        try:
            img = Image.open(path)
            img.load()

            if img.mode not in ("RGB", "RGBA", "L", "P", "CMYK", "YCbCr"):
                img = img.convert("RGB")
            elif img.mode == "RGBA":
                background = Image.new("RGB", img.size, (255, 255, 255))
                background.paste(img, mask=img.split()[3])
                img = background
            elif img.mode in ("P", "L", "CMYK", "YCbCr"):
                img = img.convert("RGB")

            pixel_count = img.width * img.height
            if pixel_count > self.max_image_size:
                scale = (self.max_image_size / pixel_count) ** 0.5
                new_width = int(img.width * scale)
                new_height = int(img.height * scale)
                img = img.resize((new_width, new_height), Image.LANCZOS)

            return img

        except Exception as e:
            if self.skip_corrupted:
                return None
            raise RuntimeError(f"Failed to load image {path}: {e}")
