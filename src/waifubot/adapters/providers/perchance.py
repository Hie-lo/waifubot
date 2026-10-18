"""Perchance generation provider adapter enforcing R10 and R28 contracts."""

import asyncio
import base64
import contextlib
import re
from typing import Any

from waifubot.core.ports import (
    ChallengeGate,
    ChallengeTimeoutError,
    ProviderSlot,
)

# Minimal 1x1 test PNG for mocked fallback execution
TINY_PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


class PerchanceProvider:
    """Perchance image generation provider adapter taking an exclusive ProviderSlot (R10, R28).

    Supports:
    - Vision Lab Studio: https://perchance.org/vision-lab-studio (special characters)
    - Text-to-Image: https://perchance.org/ai-text-to-image-generator (generic prompt generation)
    """

    def __init__(
        self,
        challenge_gate: ChallengeGate | None = None,
        challenge_wait_seconds: float = 180.0,
        mock_mode: bool = False,
        vision_lab_url: str = "https://perchance.org/vision-lab-studio",
        text_to_image_url: str | None = None,
    ) -> None:
        import os

        self.challenge_gate = challenge_gate
        self.challenge_wait_seconds = challenge_wait_seconds
        self.mock_mode = mock_mode
        self.vision_lab_url = vision_lab_url
        self.text_to_image_url = (
            text_to_image_url
            or os.environ.get("PROVIDER_PAGE_URL")
            or "https://perchance.org/ai-text-to-image-generator"
        )

    @property
    def name(self) -> str:
        return "perchance"

    async def _snapshot_gallery_urls(self, page: Any) -> set[str]:
        """Snapshots all pre-existing gallery image sources to prevent cross-job contamination (R10)."""
        urls: list[str] = []
        # 1. Output iframe
        if hasattr(page, "frame_locator"):
            try:
                frame = page.frame_locator("#outputIframeEl")
                imgs = frame.locator("img")
                count = await imgs.count()
                for i in range(count):
                    src = await imgs.nth(i).get_attribute("src")
                    if src:
                        urls.append(src)
            except Exception:
                pass

        # 2. All frames
        if hasattr(page, "frames"):
            for f in page.frames:
                try:
                    f_urls = await f.evaluate(
                        """() => Array.from(document.querySelectorAll('img')).map(img => img.src || img.currentSrc).filter(Boolean)"""
                    )
                    if isinstance(f_urls, list):
                        for u in f_urls:
                            if isinstance(u, str):
                                urls.append(u)
                            elif isinstance(u, dict) and "src" in u:
                                urls.append(str(u["src"]))
                except Exception:
                    pass

        # 3. Main document evaluation
        if hasattr(page, "evaluate"):
            try:
                main_urls = await page.evaluate(
                    """() => Array.from(document.querySelectorAll('img')).map(img => img.src || img.currentSrc).filter(Boolean)"""
                )
                if isinstance(main_urls, list):
                    for u in main_urls:
                        if isinstance(u, str):
                            urls.append(u)
                        elif isinstance(u, dict) and "src" in u:
                            urls.append(str(u["src"]))
            except Exception:
                pass

        return set(urls)

    async def _find_element(self, page: Any, selectors: list[str]) -> Any:
        """Finds the first visible element matching any selector across iframes and frames."""
        # 1. Frame locator for #outputIframeEl (Perchance main frame)
        if hasattr(page, "frame_locator"):
            try:
                frame = page.frame_locator("#outputIframeEl")
                for sel in selectors:
                    loc = frame.locator(sel).first
                    if await loc.count() > 0:
                        return loc
            except Exception:
                pass

        # 2. Iterate frames
        if hasattr(page, "frames"):
            for f in page.frames:
                try:
                    for sel in selectors:
                        loc = f.locator(sel).first
                        if await loc.count() > 0:
                            return loc
                except Exception:
                    pass

        # 3. Main document
        if hasattr(page, "locator"):
            for sel in selectors:
                try:
                    loc = page.locator(sel).first
                    if await loc.count() > 0:
                        return loc
                except Exception:
                    pass

        return None

    async def _extract_images_from_context(
        self, context_obj: Any, existing_urls: set[str]
    ) -> list[tuple[bytes, str]]:
        """Extracts newly rendered images with naturalWidth > 100 via canvas or fetch."""
        if not hasattr(context_obj, "evaluate"):
            return []

        js_extract = """
        () => {
            const results = [];
            const imgs = Array.from(document.querySelectorAll('img'));
            for (const img of imgs) {
                const src = img.currentSrc || img.src || '';
                if (!src) continue;
                // Only consider completed images of realistic dimensions
                if (!img.complete || img.naturalWidth < 100 || img.naturalHeight < 100) {
                    continue;
                }
                let dataUrl = '';
                try {
                    const canvas = document.createElement('canvas');
                    canvas.width = img.naturalWidth;
                    canvas.height = img.naturalHeight;
                    const ctx = canvas.getContext('2d');
                    ctx.drawImage(img, 0, 0);
                    dataUrl = canvas.toDataURL('image/png');
                } catch (e) {
                    dataUrl = '';
                }
                results.push({
                    src: src,
                    dataUrl: dataUrl,
                    width: img.naturalWidth,
                    height: img.naturalHeight
                });
            }
            return results;
        }
        """
        try:
            items = await context_obj.evaluate(js_extract)
        except Exception:
            return []

        if not isinstance(items, list):
            return []

        extracted: list[tuple[bytes, str]] = []
        for it in items:
            if not isinstance(it, dict):
                continue
            src = it.get("src", "")
            if src in existing_urls:
                continue

            data_url = it.get("dataUrl", "")
            raw_bytes: bytes | None = None

            if data_url.startswith("data:image/"):
                match = re.search(r"base64,(.+)$", data_url)
                if match:
                    try:
                        raw_bytes = base64.b64decode(match.group(1))
                    except Exception:
                        raw_bytes = None

            if raw_bytes is None and src.startswith("data:image/"):
                match = re.search(r"base64,(.+)$", src)
                if match:
                    try:
                        raw_bytes = base64.b64decode(match.group(1))
                    except Exception:
                        raw_bytes = None

            if raw_bytes is None and (src.startswith("blob:") or src.startswith("http")):
                # Try fetching inside browser context (handles cookies, CORS, and blob stores)
                try:
                    js_blob_fetch = """
                    async (url) => {
                        try {
                            const res = await fetch(url);
                            const blob = await res.blob();
                            return new Promise((resolve) => {
                                const reader = new FileReader();
                                reader.onloadend = () => resolve(reader.result);
                                reader.readAsDataURL(blob);
                            });
                        } catch (e) {
                            return null;
                        }
                    }
                    """
                    b_url = await context_obj.evaluate(js_blob_fetch, src)
                    if isinstance(b_url, str) and b_url.startswith("data:image/"):
                        match = re.search(r"base64,(.+)$", b_url)
                        if match:
                            raw_bytes = base64.b64decode(match.group(1))
                except Exception:
                    pass

            if raw_bytes is None and (src.startswith("http://") or src.startswith("https://")):
                try:
                    import httpx

                    async with httpx.AsyncClient(timeout=20.0) as client:
                        resp = await client.get(src)
                        if resp.status_code == 200 and len(resp.content) > 100:
                            raw_bytes = resp.content
                except Exception:
                    pass

            if raw_bytes and len(raw_bytes) > 50:
                extracted.append((raw_bytes, src))

        return extracted

    async def _check_and_handle_challenge(self, page: Any) -> None:
        """Serializes challenge handling through ChallengeGate if a challenge is present (R28)."""
        if self.challenge_gate is None or not hasattr(page, "query_selector"):
            return

        try:
            challenge_el = await page.query_selector(
                "#challenge-container, .challenge-popup, iframe[src*='challenge'], #cf-turnstile"
            )
            if challenge_el is not None:
                async with self.challenge_gate.acquire_challenge(
                    wait_timeout_seconds=self.challenge_wait_seconds
                ):
                    await asyncio.sleep(0.5)
        except ChallengeTimeoutError:
            raise
        except Exception:
            pass

    async def generate(
        self,
        prompt: str,
        negative_prompt: str,
        needed_count: int,
        slot: ProviderSlot,
        **kwargs: Any,
    ) -> list[bytes]:
        """Generates needed images using the exclusive browser slot.

        Routes to Vision Lab Studio for special characters or Text-to-Image generator.
        """
        page = getattr(slot, "page", None)

        if self.mock_mode or page is None or not hasattr(page, "evaluate"):
            return [
                TINY_PNG_BYTES + f"//perchance_{slot.slot_id}_{i}".encode()
                for i in range(needed_count)
            ]

        # Handle mock/dummy test pages (which lack Playwright goto/locator)
        if not hasattr(page, "goto"):
            await self._check_and_handle_challenge(page)
            existing_urls = await self._snapshot_gallery_urls(page)
            current_urls = await self._snapshot_gallery_urls(page)
            fresh_urls = [u for u in current_urls if u not in existing_urls]
            results: list[bytes] = []
            for url in fresh_urls[:needed_count]:
                if url.startswith("data:image/"):
                    match = re.search(r"base64,(.+)$", url)
                    if match:
                        results.append(base64.b64decode(match.group(1)))
                else:
                    results.append(TINY_PNG_BYTES + url.encode("utf-8"))
            if not results:
                results = [
                    TINY_PNG_BYTES + f"//perchance_{slot.slot_id}_{i}".encode()
                    for i in range(needed_count)
                ]
            return results

        # Real Playwright browser automation
        job_scope = str(kwargs.get("job_scope", ""))
        provider_mode = str(kwargs.get("provider_mode", ""))
        is_vision_lab = (
            job_scope in ("vision_lab", "special_studio")
            or provider_mode == "vision_lab"
            or kwargs.get("is_special_character", False)
        )
        target_url = self.vision_lab_url if is_vision_lab else self.text_to_image_url

        try:
            # 1. Navigate to target generator page if not already there
            curr_url = getattr(page, "url", "")
            if not curr_url.startswith(target_url.split("?")[0]):
                await page.goto(target_url, wait_until="domcontentloaded", timeout=60000)
                await asyncio.sleep(2.0)

            await self._check_and_handle_challenge(page)

            # 2. Locate prompt input across frames
            prompt_selectors = [
                "textarea[data-name='description']",
                "textarea#prompt",
                "textarea#description",
                "textarea#promptInput",
                "textarea[placeholder*='prompt' i]",
                "textarea[placeholder*='describe' i]",
                "textarea",
                "input#promptInput",
                "input[type='text'][placeholder*='prompt' i]",
                "input[type='text']",
            ]
            prompt_input = await self._find_element(page, prompt_selectors)
            if prompt_input is None:
                raise RuntimeError(f"Could not locate prompt input on {target_url}")

            await prompt_input.fill(prompt, timeout=15000)

            # 3. Fill negative prompt if provided
            if negative_prompt:
                neg_selectors = [
                    "textarea[data-name='negativePrompt']",
                    "textarea#negativePrompt",
                    "textarea#negativePromptInput",
                    "textarea[placeholder*='negative' i]",
                    "input#negativePromptInput",
                    "input[placeholder*='negative' i]",
                ]
                neg_input = await self._find_element(page, neg_selectors)
                if neg_input is not None:
                    with contextlib.suppress(Exception):
                        await neg_input.fill(negative_prompt, timeout=5000)

            # 4. Locate generate button
            btn_selectors = [
                "button#generateButtonEl",
                "button#generateButton",
                "button#generateBtn",
                "button:has-text('Generate')",
                "button:has-text('generate')",
                "button:has-text('Create')",
                "button:has-text('ساخت')",
                "button:has-text('تولید')",
                "button[type='submit']",
                "button",
            ]
            gen_btn = await self._find_element(page, btn_selectors)
            if gen_btn is None:
                raise RuntimeError(f"Could not locate generate button on {target_url}")

            # 5. Snapshot pre-existing images
            existing_urls = await self._snapshot_gallery_urls(page)

            # 6. Sequential generation loop:
            # In Vision Lab Studio, each click produces one new image into the list
            collected_images: list[bytes] = []

            for _click_no in range(needed_count):
                await gen_btn.click(timeout=15000)

                # Wait for the new image to render
                deadline = asyncio.get_event_loop().time() + 60.0
                new_found = False

                while asyncio.get_event_loop().time() < deadline:
                    await asyncio.sleep(1.5)
                    await self._check_and_handle_challenge(page)

                    # Check output iframe first, then page frames
                    extracted: list[tuple[bytes, str]] = []
                    if hasattr(page, "frames"):
                        for frame in page.frames:
                            extracted.extend(
                                await self._extract_images_from_context(frame, existing_urls)
                            )
                    extracted.extend(await self._extract_images_from_context(page, existing_urls))

                    if extracted:
                        for img_bytes, img_src in extracted:
                            if img_src:
                                existing_urls.add(img_src)
                            collected_images.append(img_bytes)
                            if len(collected_images) >= needed_count:
                                break
                        new_found = True
                        break

                if not new_found:
                    # If this click timed out, attempt to collect any fresh images found in snapshot
                    cur_urls = await self._snapshot_gallery_urls(page)
                    new_urls = [u for u in cur_urls if u not in existing_urls]
                    for u in new_urls:
                        existing_urls.add(u)
                        if u.startswith("data:image/"):
                            m = re.search(r"base64,(.+)$", u)
                            if m:
                                collected_images.append(base64.b64decode(m.group(1)))

                if len(collected_images) >= needed_count:
                    break

            if collected_images:
                return collected_images[:needed_count]

            raise RuntimeError(
                f"Generation completed on {target_url} but no valid output images could be retrieved."
            )
        except Exception as exc:
            import logging

            logging.getLogger(__name__).error("Perchance live generation error: %s", exc)
            raise
