"""Unit tests for image pipeline: content-addressed storage, pHash dedupe, and provenance (R43, R47)."""

import io
import os
import shutil
import tempfile
import unittest

from PIL import Image, ImageDraw
from sqlalchemy import select

from waifubot.adapters.storage.local_fs import LocalFsImageStore
from waifubot.db.models import ImageRecord
from waifubot.db.session import create_engine_and_sessionmaker, init_db
from waifubot.services.image_pipeline import (
    compute_dhash,
    filter_image_candidate,
    hamming_distance,
    persist_image_with_provenance,
)


def make_test_image_bytes(shape: str = "circle", color: str = "black", bg: str = "white") -> bytes:
    img = Image.new("RGB", (64, 64), color=bg)
    draw = ImageDraw.Draw(img)
    if shape == "circle":
        draw.ellipse([10, 10, 54, 54], fill=color)
    elif shape == "rect":
        draw.rectangle([10, 10, 54, 54], fill=color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class TestImagePipeline(unittest.IsolatedAsyncioTestCase):
    """Test suite covering pHash, near-duplicate rejection, and provenance persistence."""

    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="waifu_pipe_test_")
        self.db_path = os.path.join(self.temp_dir, "test.db")
        self.db_url = f"sqlite+aiosqlite:///{self.db_path}"
        self.engine, self.session_factory = create_engine_and_sessionmaker(self.db_url)
        await init_db(self.engine)
        self.storage_dir = os.path.join(self.temp_dir, "storage")
        self.store = LocalFsImageStore(self.storage_dir)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_phash_computation_and_hamming_distance(self) -> None:
        """pHash distinguishes shapes and detects close visual variants (R47)."""
        img1 = make_test_image_bytes("circle", "black")
        img2 = make_test_image_bytes("circle", "#222222")  # very close shade
        img3 = make_test_image_bytes("rect", "black")  # distinct shape

        h1 = compute_dhash(img1)
        h2 = compute_dhash(img2)
        h3 = compute_dhash(img3)

        dist_near = hamming_distance(h1, h2)
        dist_far = hamming_distance(h1, h3)

        self.assertLessEqual(dist_near, 6, "Visually near images should have Hamming distance <= 6")
        self.assertGreater(dist_far, 6, "Different shapes should have Hamming distance > 6")

    def test_filter_exact_and_near_duplicate(self) -> None:
        """filter_image_candidate detects exact duplicates and near duplicates (R9, R47)."""
        img1 = make_test_image_bytes("circle", "black")
        img2_near = make_test_image_bytes("circle", "#222222")
        img3_distinct = make_test_image_bytes("rect", "black")

        h1 = compute_dhash(img1)
        existing_sha = {__import__("hashlib").sha256(img1).hexdigest()}
        existing_phashes = [h1]

        # 1. Exact duplicate rejected
        res_exact = filter_image_candidate(img1, existing_sha, existing_phashes)
        self.assertFalse(res_exact.accepted)
        self.assertEqual(res_exact.rejection_reason, "duplicate")

        # 2. Near duplicate rejected with near_duplicate reason (R9, R47)
        res_near = filter_image_candidate(img2_near, set(), existing_phashes, phash_distance=6)
        self.assertFalse(res_near.accepted)
        self.assertEqual(res_near.rejection_reason, "near_duplicate")

        # 3. Distinct image accepted
        res_distinct = filter_image_candidate(
            img3_distinct, existing_sha, existing_phashes, phash_distance=6
        )
        self.assertTrue(res_distinct.accepted)
        self.assertIsNone(res_distinct.rejection_reason)

    async def test_persist_image_with_provenance(self) -> None:
        """persist_image_with_provenance stores image atomically and records full provenance (R43)."""
        img_bytes = make_test_image_bytes("circle", "blue")

        async with self.session_factory() as session, session.begin():
            await persist_image_with_provenance(
                session=session,
                image_store=self.store,
                data=img_bytes,
                job_id="job-prov-1",
                final_prompt="1girl, rem, maid outfit",
                negative_prompt="bad quality",
                seed="123456",
                provider="perchance",
                provider_version="v1.2",
                is_enhanced=True,
            )

        # Verify in DB
        async with self.session_factory() as session:
            res = await session.execute(
                select(ImageRecord).where(ImageRecord.job_id == "job-prov-1")
            )
            saved = res.scalar_one()
            self.assertEqual(saved.final_prompt, "1girl, rem, maid outfit")
            self.assertEqual(saved.negative_prompt, "bad quality")
            self.assertEqual(saved.seed, "123456")
            self.assertEqual(saved.provider, "perchance")
            self.assertEqual(saved.provider_version, "v1.2")
            self.assertTrue(saved.is_enhanced)
            self.assertIsNotNone(saved.phash)

        # Verify storage content-addressed retrieval
        loaded_bytes = await self.store.load_image(saved.image_id)
        self.assertEqual(loaded_bytes, img_bytes)


if __name__ == "__main__":
    unittest.main()
