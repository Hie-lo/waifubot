"""Image validation and atomic storage pipeline (SPEC §9.2).

Flow per candidate: size limits → SHA-256 over raw bytes → dedupe → Pillow
open + verify (decompression-bomb protected) → real format/dimensions/size →
limits → atomic write (.part + fsync + os.replace) with a generated UUID
filename (never page-supplied text) under managed storage → relative path
record. A partial or failed file is never visible as ready.
"""
from __future__ import annotations

import hashlib
import os
import uuid
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from waifubot.core.image_provider import ImageCandidate
from waifubot.errors import StorageError

# Decompression-bomb protection (agreed quality item; SPEC §13 testing).
HARD_MAX_PIXELS = 25_000_000
Image.MAX_IMAGE_PIXELS = HARD_MAX_PIXELS

ALLOWED_FORMATS = ("png", "jpeg", "jpg", "webp", "gif")


@dataclass(frozen=True)
class StoredImage:
    file_path: str  # relative, under managed storage
    mime_type: str
    format: str
    width: int
    height: int
    file_size: int
    sha256: str
    seed: str | None = None


class ImagePipeline:
    def __init__(
        self,
        storage_root: Path,
        *,
        min_width: int = 256,
        min_height: int = 256,
        max_bytes: int = 10 * 1024 * 1024,
        allowed_formats: tuple[str, ...] = ALLOWED_FORMATS,
    ) -> None:
        self._storage_root = storage_root
        self._min_width = min_width
        self._min_height = min_height
        self._max_bytes = max_bytes
        self._allowed_formats = allowed_formats
        self._images_dir = storage_root / "images"

    def _atomic_save(self, raw: bytes, ext: str) -> str:
        self._images_dir.mkdir(parents=True, exist_ok=True)
        name = f"{uuid.uuid4().hex}.{ext}"
        part = self._images_dir / (name + ".part")
        final = self._images_dir / name
        with open(part, "wb") as f:
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())
        os.replace(part, final)
        rel = f"images/{name}"
        resolved = (self._storage_root / rel).resolve()
        if not resolved.is_relative_to(self._storage_root.resolve()):
            raise StorageError("refusing to write outside managed storage")
        return rel

    def process(
        self, candidates: list[ImageCandidate], *, seen_sha256: set[str] | None = None
    ) -> tuple[list[StoredImage], list[str]]:
        """Returns (stored images, rejection reason codes). Never raises per
        candidate — every rejection gets a category."""
        seen = seen_sha256 if seen_sha256 is not None else set()
        stored: list[StoredImage] = []
        rejected: list[str] = []
        for cand in candidates:
            raw = cand.data
            if not raw:
                rejected.append("empty_bytes")
                continue
            if len(raw) > self._max_bytes:
                rejected.append("too_large")
                continue
            digest = hashlib.sha256(raw).hexdigest()
            if digest in seen:
                rejected.append("duplicate")
                continue
            try:
                img = Image.open(BytesIO(raw))
                img.verify()
                img2 = Image.open(BytesIO(raw))  # verify() invalidates the object
                fmt = (img2.format or "").lower()
                width, height = img2.size
            except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError):
                rejected.append("invalid_image")
                continue
            if fmt not in self._allowed_formats:
                rejected.append("bad_format")
                continue
            if width < self._min_width or height < self._min_height:
                rejected.append("too_small")
                continue
            ext = "jpg" if fmt in ("jpeg", "jpg") else fmt
            mime = "image/jpeg" if ext == "jpg" else f"image/{ext}"
            try:
                rel_path = self._atomic_save(raw, ext)
            except OSError:
                rejected.append("save_failed")
                continue
            seen.add(digest)
            stored.append(
                StoredImage(
                    file_path=rel_path,
                    mime_type=mime,
                    format=fmt,
                    width=width,
                    height=height,
                    file_size=len(raw),
                    sha256=digest,
                    seed=cand.seed,
                )
            )
        return stored, rejected
