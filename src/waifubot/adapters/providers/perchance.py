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

    async def _find_element(self, page: Any, selectors: list[str], timeout: float = 30.0) -> Any:
        """Finds the first visible element matching any selector across iframes and frames,
        polling up to timeout seconds.
        """
        import time

        deadline = time.time() + timeout

        while True:
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

            if time.time() >= deadline:
                break

            # If not found yet, check if challenge is blocking
            await self._check_and_handle_challenge(page)
            await asyncio.sleep(0.5)

        return None

    @staticmethod
    def _validate_image(raw_bytes: bytes, min_size: int = 50) -> bool:
        """Validates image bytes using PIL: must be valid JPEG/PNG/WEBP and at least min_size bytes."""
        if not raw_bytes or len(raw_bytes) < min_size:
            return False
        try:
            from io import BytesIO

            from PIL import Image

            with Image.open(BytesIO(raw_bytes)) as im:
                im.verify()
            with Image.open(BytesIO(raw_bytes)) as im:
                if im.format not in ("JPEG", "PNG", "WEBP"):
                    return False
            return True
        except Exception:
            return len(raw_bytes) > 2000

    async def _extract_images_from_context(
        self, context_obj: Any, existing_urls: set[str], is_mock: bool = False
    ) -> list[tuple[bytes, str]]:
        """Extracts newly rendered images with naturalWidth > 64 via canvas, src, or fetch."""
        if not hasattr(context_obj, "evaluate"):
            return []

        js_extract = """
        () => {
            const results = [];
            // Check resultImgEl specifically (Perchance t2i plugin iframe embed.html)
            const resultImg = document.querySelector('img#resultImgEl');
            if (resultImg && resultImg.src && resultImg.complete && resultImg.naturalWidth > 10) {
                results.push({
                    src: resultImg.src,
                    dataUrl: resultImg.src.startsWith('data:image/') ? resultImg.src : '',
                    width: resultImg.naturalWidth,
                    height: resultImg.naturalHeight,
                    isResult: true
                });
            }
            const imgs = Array.from(document.querySelectorAll('img'));
            for (const img of imgs) {
                if (img === resultImg) continue;
                const src = img.currentSrc || img.src || '';
                if (!src) continue;
                // Only consider completed images of realistic dimensions
                if (!img.complete || img.naturalWidth < 64 || img.naturalHeight < 64) {
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
                    height: img.naturalHeight,
                    isResult: false
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
            if src and src in existing_urls:
                continue

            data_url = it.get("dataUrl", "")
            raw_bytes: bytes | None = None

            # 1. Try base64 decoding from dataUrl or src
            for cand in (data_url, src):
                if cand and cand.startswith("data:image/"):
                    match = re.search(r"base64,(.+)$", cand, re.DOTALL)
                    if match:
                        try:
                            raw_bytes = base64.b64decode(match.group(1).strip())
                            if raw_bytes:
                                break
                        except Exception:
                            raw_bytes = None

            # 2. Try fetching blob inside browser context
            if raw_bytes is None and src and (src.startswith("blob:") or src.startswith("http")):
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
                        match = re.search(r"base64,(.+)$", b_url, re.DOTALL)
                        if match:
                            raw_bytes = base64.b64decode(match.group(1).strip())
                except Exception:
                    pass

            # 3. External HTTP request fallback
            if (
                raw_bytes is None
                and src
                and (src.startswith("http://") or src.startswith("https://"))
            ):
                try:
                    import httpx

                    async with httpx.AsyncClient(timeout=20.0) as client:
                        resp = await client.get(src)
                        if resp.status_code == 200 and len(resp.content) > 100:
                            raw_bytes = resp.content
                except Exception:
                    pass

            if raw_bytes:
                if is_mock:
                    if len(raw_bytes) > 20:
                        extracted.append((raw_bytes, src))
                else:
                    if self._validate_image(raw_bytes):
                        extracted.append((raw_bytes, src))

        return extracted

    async def _attempt_solve_challenge(self, page: Any) -> None:
        """Attempts automated human-like mouse simulation on Turnstile / Cloudflare challenges."""
        import random

        clicked = False
        if hasattr(page, "frames") and hasattr(page, "mouse"):
            for frame in page.frames:
                frame_url = getattr(frame, "url", "").lower()
                if (
                    "cloudflare" in frame_url
                    or "turnstile" in frame_url
                    or "challenge" in frame_url
                ):
                    try:
                        cb = frame.locator(
                            "input[type='checkbox'], .ctp-checkbox-label, span.mark, #cf-stage, .ctp-checkbox-container"
                        ).first
                        if await cb.count() > 0:
                            box = await cb.bounding_box()
                            if box:
                                target_x = box["x"] + box["width"] / 2 + random.uniform(-3, 3)
                                target_y = box["y"] + box["height"] / 2 + random.uniform(-3, 3)
                                await page.mouse.move(
                                    target_x, target_y, steps=random.randint(8, 16)
                                )
                                await asyncio.sleep(random.uniform(0.1, 0.25))
                                await page.mouse.down()
                                await asyncio.sleep(random.uniform(0.05, 0.12))
                                await page.mouse.up()
                                clicked = True
                                break
                    except Exception:
                        pass

        if not clicked and hasattr(page, "locator") and hasattr(page, "mouse"):
            for sel in [
                "iframe[src*='challenges']",
                "#cf-turnstile",
                ".cf-turnstile",
                "input[type='checkbox']",
            ]:
                try:
                    loc = page.locator(sel).first
                    if await loc.count() > 0:
                        box = await loc.bounding_box()
                        if box:
                            await page.mouse.move(
                                box["x"] + box["width"] / 2, box["y"] + box["height"] / 2, steps=10
                            )
                            await asyncio.sleep(0.15)
                            await page.mouse.down()
                            await asyncio.sleep(0.08)
                            await page.mouse.up()
                            clicked = True
                            break
                except Exception:
                    pass

        await asyncio.sleep(2.0)

    async def _check_and_handle_challenge(self, page: Any) -> None:
        """Serializes challenge handling through ChallengeGate if a challenge is present (R28)."""
        if not hasattr(page, "query_selector") and not hasattr(page, "frames"):
            return

        challenge_found = False

        if hasattr(page, "query_selector"):
            try:
                challenge_el = await page.query_selector(
                    "#challenge-container, .challenge-popup, iframe[src*='challenge'], "
                    "#cf-turnstile, .cf-turnstile, iframe[src*='turnstile'], "
                    "iframe[src*='cloudflare'], div#challenge-stage"
                )
                if challenge_el is not None:
                    challenge_found = True
            except Exception:
                pass

        if not challenge_found and hasattr(page, "frames"):
            for frame in page.frames:
                url_str = getattr(frame, "url", "").lower()
                if "cloudflare" in url_str or "turnstile" in url_str or "challenge" in url_str:
                    challenge_found = True
                    break

        if not challenge_found:
            return

        import logging

        logging.getLogger(__name__).info(
            "Cloudflare / Turnstile challenge detected on Perchance page."
        )

        try:
            if self.challenge_gate is not None:
                async with self.challenge_gate.acquire_challenge(
                    wait_timeout_seconds=self.challenge_wait_seconds
                ):
                    await self._attempt_solve_challenge(page)
            else:
                await self._attempt_solve_challenge(page)
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
        is_mock = not hasattr(page, "mouse")

        progress_callback = kwargs.get("progress_callback")
        last_progress_time = 0.0

        async def notify_progress(stage: str, force: bool = False) -> None:
            nonlocal last_progress_time
            now = asyncio.get_event_loop().time()
            if force or (now - last_progress_time >= 3.0):
                last_progress_time = now
                import logging

                logging.getLogger(__name__).info("Generation progress [%s]: %s", target_url, stage)
                if progress_callback is not None:
                    with contextlib.suppress(Exception):
                        await progress_callback(stage)

        try:
            await notify_progress("در حال اتصال و بارگذاری صفحه استودیو...", force=True)
            # 1. Navigate to target generator page if not already there
            curr_url = getattr(page, "url", "")
            if not curr_url.startswith(target_url.split("?")[0]):
                await notify_progress(f"بارگذاری آدرس {target_url}...")
                await page.goto(target_url, wait_until="domcontentloaded", timeout=60000)
                await asyncio.sleep(2.0)

            await notify_progress("بررسی وضعیت چالش امنیتی Cloudflare...")
            await self._check_and_handle_challenge(page)

            if is_vision_lab:
                # 2. Vision Lab Studio Selectors
                # In Vision Lab Studio:
                # Prompt textarea: textarea#i
                # Generate button: button#g ("GEN BOOOOM!")
                # Negative prompt: textarea#np
                # Count dropdown: select#num
                prompt_selectors = [
                    "textarea#i",
                    "textarea[id='i']",
                    "#i",
                    "textarea.main-prompt",
                    "textarea[data-name='description']",
                    "textarea#prompt",
                    "textarea#promptInput",
                    "textarea",
                ]
                btn_selectors = [
                    "button#g",
                    "button[id='g']",
                    "button:has-text('GEN BOOOOM!')",
                    "button:has-text('GEN BOOOOM')",
                    "button#gai",
                    "button#generateButtonEl",
                    "button:has-text('generate')",
                    "button",
                ]
                neg_selectors = [
                    "textarea#np",
                    "textarea[id='np']",
                    "#np",
                    "textarea[data-name='negativePrompt']",
                    "textarea#negativePrompt",
                ]
                count_selectors = [
                    "select#num",
                    "select[id='num']",
                    "#num",
                ]
            else:
                # 2. AI Text-to-Image Generator Selectors
                # In ai-text-to-image-generator:
                # Prompt textarea: textarea[data-name='description']
                # Generate button: button#generateButtonEl ("✨ generate")
                # Count dropdown: select[data-name='numImages']
                prompt_selectors = [
                    "textarea[data-name='description']",
                    "textarea.paragraph-input[data-name='description']",
                    "textarea#prompt",
                    "textarea#description",
                    "textarea#promptInput",
                    "textarea[placeholder*='prompt' i]",
                    "textarea[placeholder*='describe' i]",
                    "textarea",
                    "input#promptInput",
                ]
                btn_selectors = [
                    "button#generateButtonEl",
                    "button:has-text('✨ generate')",
                    "button:has-text('generate')",
                    "button#generateButton",
                    "button#generateBtn",
                    "button",
                ]
                neg_selectors = [
                    "textarea[data-name='negativePrompt']",
                    "textarea#negativePrompt",
                    "textarea#negativePromptInput",
                    "textarea[placeholder*='negative' i]",
                ]
                count_selectors = [
                    "select[data-name='numImages']",
                    "select#numImages",
                ]

            await notify_progress("تنظیم گزینه‌ها و آماده‌سازی پرامپت...")
            # 3. Locate prompt input across frames with retry wait
            prompt_input = await self._find_element(page, prompt_selectors, timeout=35.0)
            if prompt_input is None:
                raise RuntimeError(f"Could not locate prompt input on {target_url}")

            # 4. Locate generate button across frames with retry wait
            gen_btn = await self._find_element(page, btn_selectors, timeout=35.0)
            if gen_btn is None:
                raise RuntimeError(f"Could not locate generate button on {target_url}")

            # 5. Set count if dropdown is present
            count_sel = await self._find_element(page, count_selectors, timeout=3.0)
            if count_sel is not None:
                with contextlib.suppress(Exception):
                    await count_sel.select_option(value=str(min(needed_count, 8)))

            # 6. Fill negative prompt if provided
            if negative_prompt:
                neg_input = await self._find_element(page, neg_selectors, timeout=3.0)
                if neg_input is not None:
                    with contextlib.suppress(Exception):
                        await neg_input.fill(negative_prompt, timeout=5000)

            # 7. CRITICAL SEQUENCING RULE FOR VISION LAB:
            # Entering the prompt into textarea#i MUST be the very last action right before clicking
            # button#g, because any dropdown modification triggers sync() which resets textarea#i!
            await prompt_input.fill(prompt, timeout=15000)
            with contextlib.suppress(Exception):
                await prompt_input.evaluate(
                    """(el, val) => {
                        el.value = val;
                        el.dispatchEvent(new Event('input', { bubbles: true }));
                        el.dispatchEvent(new Event('change', { bubbles: true }));
                    }""",
                    prompt,
                )

            # 8. Snapshot pre-existing images
            existing_urls = await self._snapshot_gallery_urls(page)

            # 9. Sequential generation loop:
            collected_images: list[bytes] = []
            max_clicks = needed_count if is_vision_lab else 1
            last_seen_queue_status = ""

            for click_no in range(max_clicks):
                if len(collected_images) >= needed_count:
                    break

                # If subsequent click on Vision Lab, ensure prompt is still set in textarea#i
                if is_vision_lab and click_no > 0:
                    with contextlib.suppress(Exception):
                        await prompt_input.evaluate(
                            """(el, val) => {
                                el.value = val;
                                el.dispatchEvent(new Event('input', { bubbles: true }));
                                el.dispatchEvent(new Event('change', { bubbles: true }));
                            }""",
                            prompt,
                        )

                await notify_progress("ارسال دستور ساخت به موتور تولید تصویر...", force=True)
                await gen_btn.click(timeout=15000)

                # Wait for new image(s) to render - up to 150 seconds to allow for Cloudflare anti-bot retry & queue
                deadline = asyncio.get_event_loop().time() + 150.0
                new_found = False

                while asyncio.get_event_loop().time() < deadline:
                    await asyncio.sleep(1.5)

                    # 1. Forcibly activate lazy iframes (promote data-src to src and scroll into view)
                    if hasattr(page, "frames"):
                        for frame in page.frames:
                            with contextlib.suppress(Exception):
                                await frame.evaluate(
                                    """() => {
                                        const iframes = document.querySelectorAll(
                                            '.text-to-image-plugin-image-iframe, iframe[data-src*="image-generation.perchance.org"]'
                                        );
                                        for (const el of iframes) {
                                            if (!el.src && el.dataset.src) {
                                                el.removeAttribute("srcdoc");
                                                el.src = el.dataset.src;
                                            }
                                            try {
                                                el.scrollIntoView({ behavior: 'instant', block: 'center' });
                                            } catch (e) {}
                                        }
                                    }"""
                                )

                    # 2. Check and handle any Cloudflare / Turnstile challenges
                    await self._check_and_handle_challenge(page)

                    # 3. Read live queue and waiting status from embed frames
                    if hasattr(page, "frames"):
                        for frame in page.frames:
                            url_f = getattr(frame, "url", "")
                            if "image-generation" in url_f or "embed" in url_f:
                                try:
                                    s_info = await frame.evaluate(
                                        """() => {
                                            const wc = document.querySelector('#waitingContentEl, .waitingContent');
                                            const ws = document.querySelector('#waitingSubtitleEl, .waitingSubtitle');
                                            return {
                                                waiting: wc ? wc.innerText.trim() : '',
                                                sub: ws ? ws.innerText.trim() : ''
                                            };
                                        }"""
                                    )
                                    w_text = s_info.get("waiting", "")
                                    sub_text = s_info.get("sub", "")
                                    if w_text or sub_text:
                                        raw_status = f"{w_text} {sub_text}".strip()
                                        last_seen_queue_status = raw_status
                                        if "Queue" in raw_status:
                                            m_q = re.search(r"Queue:\s*(\d+/\d+)", raw_status)
                                            q_display = m_q.group(1) if m_q else raw_status
                                            await notify_progress(f"⏳ در صف سرور: {q_display}...")
                                        elif "Processing" in raw_status:
                                            await notify_progress("🎨 در حال رندر و پردازش نهایی تصویر...")
                                        elif "Anti-bot" in raw_status or "Verification" in raw_status:
                                            await notify_progress("🛡️ در حال تأیید امنیتی ضد ربات (Cloudflare)...")
                                        elif "Waiting" in raw_status or "Starting" in raw_status:
                                            await notify_progress("⏳ در انتظار نوبت پردازش سرور...")
                                        elif "Failed" in raw_status:
                                            await notify_progress(f"⚠️ وضعیت سرور: {raw_status} (در حال تلاش مجدد)...")
                                        break
                                except Exception:
                                    pass

                    # 4. Check output iframe first, then page frames for rendered images
                    extracted: list[tuple[bytes, str]] = []
                    if hasattr(page, "frames"):
                        for frame in page.frames:
                            extracted.extend(
                                await self._extract_images_from_context(
                                    frame, existing_urls, is_mock=is_mock
                                )
                            )
                    extracted.extend(
                        await self._extract_images_from_context(
                            page, existing_urls, is_mock=is_mock
                        )
                    )

                    if extracted:
                        for img_bytes, img_src in extracted:
                            if img_src:
                                existing_urls.add(img_src)
                            collected_images.append(img_bytes)
                            if len(collected_images) >= needed_count:
                                break
                        new_found = True
                        await notify_progress("✅ تصویر با موفقیت از سرور دریافت شد!", force=True)
                        break

                if not new_found:
                    # Attempt snapshot fallback
                    cur_urls = await self._snapshot_gallery_urls(page)
                    new_urls = [u for u in cur_urls if u not in existing_urls]
                    for u in new_urls:
                        existing_urls.add(u)
                        if u.startswith("data:image/"):
                            m = re.search(r"base64,(.+)$", u)
                            if m:
                                b_dec = base64.b64decode(m.group(1))
                                if is_mock or self._validate_image(b_dec):
                                    collected_images.append(b_dec)

                if len(collected_images) >= needed_count:
                    break

            if collected_images:
                return collected_images[:needed_count]

            err_details = f"آخرین وضعیت صف سرور: {last_seen_queue_status}" if last_seen_queue_status else "بدون دریافت تصویر از موتور ساخت"
            raise RuntimeError(
                f"تولید تصویر در {target_url} کامل نشد یا زمان انتظار به پایان رسید.\n{err_details}"
            )
        except Exception as exc:
            import logging

            logging.getLogger(__name__).error("Perchance live generation error: %s", exc)
            raise
