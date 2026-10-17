"""Local filesystem content-addressed image store.

Atomic writes via temporary files and rename.
Images are indexed by their SHA256 hash in a partitioned directory structure:
    storage_root / hash[0:2] / hash[2:4] / hash.{ext}
"""

import hashlib
import os
import tempfile
from pathlib import Path


class LocalFsImageStore:
    """Content-addressed local filesystem storage implementing core.ports.ImageStore."""

    def __init__(self, root_dir: str | Path) -> None:
        self.root_dir = Path(root_dir)
        self.root_dir.mkdir(parents=True, exist_ok=True)

    def _get_path(self, sha256_hash: str, ext: str) -> Path:
        p1 = sha256_hash[:2]
        p2 = sha256_hash[2:4]
        return self.root_dir / p1 / p2 / f"{sha256_hash}.{ext}"

    async def save_image(self, data: bytes, mime_type: str = "image/png") -> str:
        """Saves image atomically and returns its SHA256 hex digest."""
        ext = "png"
        if "jpeg" in mime_type or "jpg" in mime_type:
            ext = "jpg"
        elif "webp" in mime_type:
            ext = "webp"

        # EXIF stripping if Pillow is available
        clean_data = data
        try:
            from io import BytesIO

            from PIL import Image

            img = Image.open(BytesIO(data))
            out_buf = BytesIO()
            # Saving image without passing existing info/exif strips metadata
            img.save(out_buf, format=img.format or "PNG")
            clean_data = out_buf.getvalue()
        except Exception:
            # Fallback to pristine binary data
            clean_data = data

        sha256_hash = hashlib.sha256(clean_data).hexdigest()
        dest_path = self._get_path(sha256_hash, ext)

        if dest_path.exists():
            return sha256_hash

        dest_path.parent.mkdir(parents=True, exist_ok=True)

        # Atomic write: write to temp file on same filesystem, then rename
        temp_fd, temp_path = tempfile.mkstemp(
            prefix="upload_",
            suffix=f".{ext}.tmp",
            dir=dest_path.parent,
        )
        try:
            with os.fdopen(temp_fd, "wb") as f:
                f.write(clean_data)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_path, dest_path)
        except Exception:
            if os.path.exists(temp_path):
                os.remove(temp_path)
            raise

        return sha256_hash

    async def load_image(self, file_id: str) -> bytes:
        """Loads image data by its hash or returns FileNotFoundError."""
        # Find file regardless of extension
        p1 = file_id[:2]
        p2 = file_id[2:4]
        target_dir = self.root_dir / p1 / p2
        if not target_dir.exists():
            raise FileNotFoundError(f"Image {file_id} not found in store")

        for file in target_dir.glob(f"{file_id}.*"):
            if not file.name.endswith(".tmp"):
                return file.read_bytes()

        raise FileNotFoundError(f"Image {file_id} not found in store")
