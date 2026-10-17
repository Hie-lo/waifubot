"""Unit tests for PerchanceProvider adapter (R10, R28 contracts)."""

import unittest
from typing import Any

from waifubot.adapters.providers.mock import MockSlot
from waifubot.adapters.providers.perchance import PerchanceProvider
from waifubot.services.resources import ChallengeGate


class DummyPage:
    """Mock page to test gallery snapshotting and challenge handling."""

    def __init__(self, initial_images: list[str], generated_images: list[str]) -> None:
        self.initial_images = list(initial_images)
        self.generated_images = list(generated_images)
        self._evaluated_once = False
        self.has_challenge = False

    async def evaluate(self, script: str) -> list[str]:
        if not self._evaluated_once:
            self._evaluated_once = True
            return self.initial_images
        return self.initial_images + self.generated_images

    async def query_selector(self, selector: str) -> Any:
        if self.has_challenge and "challenge" in selector:
            return object()  # non-None indicates challenge found
        return None


class TestPerchanceAdapter(unittest.IsolatedAsyncioTestCase):
    """Test suite covering Perchance provider adapter contracts (R10, R28)."""

    def test_provider_name_and_contract(self) -> None:
        provider = PerchanceProvider(mock_mode=True)
        self.assertEqual(provider.name, "perchance")

    async def test_gallery_snapshot_excludes_preexisting_images(self) -> None:
        """Verify pre-existing gallery images are excluded so only fresh images are kept (R10)."""
        provider = PerchanceProvider(mock_mode=False)

        # Existing gallery had img1 and img2
        page = DummyPage(
            initial_images=["https://perchance.org/img1.png", "https://perchance.org/img2.png"],
            generated_images=["https://perchance.org/img3.png", "https://perchance.org/img4.png"],
        )
        slot = MockSlot("slot-perchance")
        slot.page = page

        images = await provider.generate(
            prompt="anime hero",
            negative_prompt="blurry",
            needed_count=2,
            slot=slot,
        )

        self.assertEqual(len(images), 2)
        # None of the images should represent img1 or img2
        for img in images:
            self.assertNotIn(b"img1.png", img)
            self.assertNotIn(b"img2.png", img)

    async def test_challenge_detection_acquires_challenge_gate(self) -> None:
        """Verify detected challenge triggers ChallengeGate acquisition (R28)."""
        gate = ChallengeGate()
        provider = PerchanceProvider(challenge_gate=gate, challenge_wait_seconds=5.0)

        page = DummyPage(
            initial_images=[],
            generated_images=["https://perchance.org/img_fresh.png"],
        )
        page.has_challenge = True

        slot = MockSlot("slot-challenge")
        slot.page = page

        # Generating should detect challenge and pass through gate
        images = await provider.generate(
            prompt="anime girl",
            negative_prompt="",
            needed_count=1,
            slot=slot,
        )
        self.assertEqual(len(images), 1)

    def test_recorded_fixtures_parsing(self) -> None:
        """Verifies recorded HTML and JSON fixtures for offline test determinism (R46)."""
        import json
        from pathlib import Path

        fixtures_dir = Path("tests/fixtures/perchance")
        gen_html = (fixtures_dir / "generator_page.html").read_text(encoding="utf-8")
        ch_html = (fixtures_dir / "challenge_page.html").read_text(encoding="utf-8")
        out_json = json.loads((fixtures_dir / "output_gallery.json").read_text(encoding="utf-8"))

        self.assertIn("output-img", gen_html)
        self.assertIn("challengeModal", ch_html)
        self.assertIn("solveOnceBtn", ch_html)
        self.assertEqual(len(out_json["images"]), 2)
        self.assertEqual(out_json["seed"], 421890)


if __name__ == "__main__":
    unittest.main()
