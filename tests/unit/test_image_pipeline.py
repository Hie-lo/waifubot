"""Image pipeline tests (SPEC §9.2): validation, dedupe, atomic saves, safety."""
from __future__ import annotations

import io
from pathlib import Path

from PIL import Image

from waifubot.core.image_pipeline import HARD_MAX_PIXELS, ImagePipeline
from waifubot.core.image_provider import ImageCandidate


def _png_bytes(
    width: int = 512, height: int = 512, color: tuple[int, int, int] = (10, 20, 30)
) -> bytes:
    img = Image.new("RGB", (width, height), color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _pipeline(tmp_path: Path, **kwargs) -> ImagePipeline:
    return ImagePipeline(tmp_path / "storage", **kwargs)


def test_valid_png_is_stored(tmp_path: Path) -> None:
    pipe = _pipeline(tmp_path)
    stored, rejected = pipe.process([ImageCandidate(data=_png_bytes(), claimed_mime="image/png")])
    assert rejected == []
    assert len(stored) == 1
    item = stored[0]
    assert item.format == "png"
    assert item.width == 512 and item.height == 512
    assert item.file_path.startswith("images/")
    assert item.file_path.endswith(".png")
    on_disk = tmp_path / "storage" / item.file_path
    assert on_disk.exists()
    assert not list((tmp_path / "storage" / "images").glob("*.part"))  # atomic: no partials


def test_extension_follows_real_format(tmp_path: Path) -> None:
    buf = io.BytesIO()
    Image.new("RGB", (300, 300), (1, 2, 3)).save(buf, format="JPEG")
    pipe = _pipeline(tmp_path)
    stored, rejected = pipe.process([ImageCandidate(data=buf.getvalue())])
    assert rejected == []
    assert stored[0].format == "jpeg"
    assert stored[0].file_path.endswith(".jpg")
    assert stored[0].mime_type == "image/jpeg"


def test_duplicate_sha_is_skipped(tmp_path: Path) -> None:
    pipe = _pipeline(tmp_path)
    data = _png_bytes()
    stored, rejected = pipe.process(
        [ImageCandidate(data=data), ImageCandidate(data=bytes(data))]
    )
    assert len(stored) == 1
    assert rejected == ["duplicate"]


def test_corrupt_bytes_rejected(tmp_path: Path) -> None:
    pipe = _pipeline(tmp_path)
    stored, rejected = pipe.process([ImageCandidate(data=b"\x89PNG-not-really")])
    assert stored == []
    assert rejected == ["invalid_image"]


def test_too_small_rejected(tmp_path: Path) -> None:
    pipe = _pipeline(tmp_path, min_width=256, min_height=256)
    stored, rejected = pipe.process([ImageCandidate(data=_png_bytes(64, 64))])
    assert stored == []
    assert rejected == ["too_small"]


def test_too_large_rejected(tmp_path: Path) -> None:
    pipe = _pipeline(tmp_path, max_bytes=100)
    stored, rejected = pipe.process([ImageCandidate(data=_png_bytes(300, 300))])
    assert stored == []
    assert rejected == ["too_large"]


def test_empty_bytes_rejected(tmp_path: Path) -> None:
    pipe = _pipeline(tmp_path)
    stored, rejected = pipe.process([ImageCandidate(data=b"")])
    assert stored == []
    assert rejected == ["empty_bytes"]


def test_decompression_bomb_rejected(tmp_path: Path) -> None:
    # A valid PNG header with absurd dimensions trips the bomb guard or size limits.
    pipe = _pipeline(tmp_path, min_width=1, min_height=1, max_bytes=10**9)
    huge = _png_bytes(20000, 20000)  # 400 MP > HARD_MAX_PIXELS
    assert Image.MAX_IMAGE_PIXELS == HARD_MAX_PIXELS
    stored, rejected = pipe.process([ImageCandidate(data=huge)])
    assert stored == []
    assert rejected == ["invalid_image"]  # DecompressionBombError path


def test_filename_is_generated_not_page_supplied(tmp_path: Path) -> None:
    pipe = _pipeline(tmp_path)
    stored, _ = pipe.process([ImageCandidate(data=_png_bytes())])
    name = Path(stored[0].file_path).name
    assert name != "evil.png"
    assert "/" not in name and ".." not in name


def test_seed_carried_when_present(tmp_path: Path) -> None:
    pipe = _pipeline(tmp_path)
    stored, _ = pipe.process([ImageCandidate(data=_png_bytes(), seed="s-1")])
    assert stored[0].seed == "s-1"
