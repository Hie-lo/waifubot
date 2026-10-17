"""In-memory image store for deterministic testing."""

import hashlib


class MemoryImageStore:
    """In-memory storage implementing core.ports.ImageStore."""

    def __init__(self) -> None:
        self.storage: dict[str, bytes] = {}

    async def save_image(self, data: bytes, mime_type: str = "image/png") -> str:
        sha256_hash = hashlib.sha256(data).hexdigest()
        self.storage[sha256_hash] = data
        return sha256_hash

    async def load_image(self, file_id: str) -> bytes:
        if file_id not in self.storage:
            raise FileNotFoundError(f"Image {file_id} not found in memory store")
        return self.storage[file_id]
