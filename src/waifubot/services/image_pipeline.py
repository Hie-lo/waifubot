"""Image pipeline: content-addressed storage, pHash duplicate detection, and provenance tracking (R43, R47)."""

from dataclasses import dataclass
import hashlib
import io
import time
from typing import Any

from PIL import Image
from sqlalchemy.ext.asyncio import AsyncSession

from waifubot.core.models import ImageAsset
from waifubot.core.ports import ImageStore
from waifubot.db.models import ImageRecord


def compute_dhash(image_bytes: bytes) -> str:
    """Computes a 64-bit perceptual difference hash (dHash) matching pHash requirements (R47)."""
    try:
        img = Image.open(io.BytesIO(image_bytes))
        img_gray = img.convert("L").resize((9, 8), Image.Resampling.LANCZOS)
        pixels = list(img_gray.getdata())
        diff: list[bool] = []
        for row in range(8):
            for col in range(8):
                pixel_left = pixels[row * 9 + col]
                pixel_right = pixels[row * 9 + col + 1]
                diff.append(pixel_left > pixel_right)

        decimal_val = 0
        hex_parts: list[str] = []
        for i, v in enumerate(diff):
            if v:
                decimal_val += 2 ** (i % 4)
            if (i % 4) == 3:
                hex_parts.append(hex(decimal_val)[2:])
                decimal_val = 0
        return "".join(hex_parts).zfill(16)
    except Exception:
        # Fallback to sha256 prefix if image decoding fails
        return hashlib.sha256(image_bytes).hexdigest()[:16]


def hamming_distance(hash1: str, hash2: str) -> int:
    """Computes Hamming distance between two hex hash strings."""
    if not hash1 or not hash2:
        return 64
    try:
        val1 = int(hash1, 16)
        val2 = int(hash2, 16)
        return bin(val1 ^ val2).count("1")
    except ValueError:
        return 64


def is_near_duplicate(
    phash: str,
    existing_phashes: list[str],
    max_distance: int = 6,
) -> bool:
    """Returns True if phash is within max_distance of any existing phash (R47).

    A distance of 0 disables near-duplicate detection.
    """
    if max_distance <= 0 or not phash:
        return False

    for existing in existing_phashes:
        if existing and hamming_distance(phash, existing) <= max_distance:
            return True
    return False


@dataclass
class PipelineFilterResult:
    """Result of passing an image through content hash and pHash filters (R9, R47)."""

    accepted: bool
    rejection_reason: str | None = None
    sha256: str = ""
    phash: str = ""


def filter_image_candidate(
    data: bytes,
    existing_sha256_set: set[str],
    existing_phashes: list[str],
    phash_distance: int = 6,
) -> PipelineFilterResult:
    """Evaluates candidate image against exact hash and perceptual hash dedupe (R9, R47)."""
    sha256_hash = hashlib.sha256(data).hexdigest()
    if sha256_hash in existing_sha256_set:
        return PipelineFilterResult(
            accepted=False,
            rejection_reason="duplicate",
            sha256=sha256_hash,
        )

    phash = compute_dhash(data)
    if is_near_duplicate(phash, existing_phashes, max_distance=phash_distance):
        return PipelineFilterResult(
            accepted=False,
            rejection_reason="near_duplicate",
            sha256=sha256_hash,
            phash=phash,
        )

    return PipelineFilterResult(
        accepted=True,
        sha256=sha256_hash,
        phash=phash,
    )


async def persist_image_with_provenance(
    session: AsyncSession,
    image_store: ImageStore,
    data: bytes,
    job_id: str,
    final_prompt: str,
    negative_prompt: str = "",
    seed: str | None = None,
    provider: str = "perchance",
    provider_version: str = "v1",
    character_version_id: int | None = None,
    is_enhanced: bool = False,
    mime_type: str = "image/png",
) -> ImageRecord:
    """Saves image to content-addressed storage and stores complete provenance (R43, R47)."""
    sha256_hash = await image_store.save_image(data, mime_type=mime_type)
    phash = compute_dhash(data)

    width, height = None, None
    try:
        img = Image.open(io.BytesIO(data))
        width, height = img.size
    except Exception:
        pass

    record = ImageRecord(
        image_id=sha256_hash,
        file_path=f"{sha256_hash[:2]}/{sha256_hash[2:4]}/{sha256_hash}.png",
        phash=phash,
        mime_type=mime_type,
        width=width,
        height=height,
        file_size_bytes=len(data),
        job_id=job_id,
        character_version_id=character_version_id,
        final_prompt=final_prompt,
        negative_prompt=negative_prompt,
        seed=seed,
        provider=provider,
        provider_version=provider_version,
        is_enhanced=is_enhanced,
        created_at=time.time(),
    )

    session.add(record)
    await session.flush()
    return record
