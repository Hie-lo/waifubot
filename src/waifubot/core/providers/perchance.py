"""Perchance provider adapter — async Playwright (Sprint 2).

Built from the Sprint 0 spike that worked on the user's machine with the
user-approved automation profile (AGENT.md amendment log). Rules that stay:

- Plain fill/click first; human-paced typing + the approved launch profile are
  a recorded user decision (docs/provider-permission.md).
- NO CAPTCHA solving, NO challenge bypass, NO proxy rotation. A challenge page
  is `blocked_by_provider` — a permanent outcome, never retried (SPEC §10).
- All page URLs / selectors / style values / pacing live in config
  (config/providers/perchance.yaml) — never hardcoded here.
- Hard timeouts on every operation; bounded polling; categorized errors with
  actionable summaries (SPEC §4). Never log data URLs, cookies, or tokens.

A live run requires the explicit CLI gate (`live-smoke --confirm-live`).
"""
from __future__ import annotations

import asyncio
import base64
import os
import random
import re
import shutil
import subprocess
import time
from collections.abc import Mapping
from typing import Any

from waifubot.core.image_provider import (
    ERROR_BLOCKED,
    ERROR_PROVIDER,
    ERROR_TIMEOUT,
    STATUS_BLOCKED,
    STATUS_ERROR,
    STATUS_OK,
    STATUS_PARTIAL,
    STATUS_TIMEOUT,
    GenerationRequest,
    GenerationResult,
    ImageCandidate,
)

BLOCKED_MARKERS = (
    "captcha",
    "challenge",
    "access denied",
    "rate limit",
    "too many requests",
    "verify you are human",
    "unusual traffic",
    # Cloudflare interstitial wording (headless test 2026-10-16: the page showed
    # "Performing security verification" and this detector missed it).
    "performing security verification",
    "security verification",
    "security service to protect",
    "just a moment",
    "checking your browser",
    "attention required",
    "additional verification required",
)

HARD_MAX_TIMEOUT_S = 300
HARD_MAX_POLL_INTERVAL_S = 30
HARD_MAX_TYPE_DELAY_MS = 500
SETTLE_MS = 5000
PROMPT_VISIBLE_TIMEOUT_MS = 60000
# Human-approved challenge restarts (bounded — never uncontrolled retries).
MAX_CHALLENGE_RESTARTS = 2

_JS_SCAN = """
    () => {
        const fallback = __CANVAS_FALLBACK__;
        const out = [];
        const imgs = Array.from(document.querySelectorAll('img'));
        for (const img of imgs) {
            const src = img.src || img.getAttribute('src') || '';
            if (src.startsWith('data:image/') && src.length > 2000) {
                out.push(src);
            } else if (fallback && src && !src.includes('icon') && !src.includes('logo')
                       && img.width > 150) {
                try {
                    const canvas = document.createElement('canvas');
                    canvas.width = img.naturalWidth || img.width || 512;
                    canvas.height = img.naturalHeight || img.height || 512;
                    const ctx = canvas.getContext('2d');
                    ctx.drawImage(img, 0, 0);
                    const dataUrl = canvas.toDataURL('image/jpeg', 0.95);
                    if (dataUrl.length > 2000) {
                        out.push(dataUrl);
                    }
                } catch (e) {}
            }
        }
        if (fallback) {
            for (const c of document.querySelectorAll('canvas')) {
                try {
                    if (c.width > 200 && c.height > 200) {
                        const dataUrl = c.toDataURL('image/jpeg', 0.95);
                        if (dataUrl && dataUrl.length > 2000) {
                            out.push(dataUrl);
                        }
                    }
                } catch (e) {}
            }
        }
        return out;
    }
"""


def detect_block(text: str, url: str) -> str | None:
    # Normalize separators so URL slugs like "rate-limit" match "rate limit".
    low = f"{text} {url}".lower().replace("-", " ").replace("_", " ")
    for marker in BLOCKED_MARKERS:
        if marker in low:
            return marker
    return None


def decode_data_url(data_url: str) -> tuple[str, bytes] | None:
    """Parse a data: URL into (mime, raw bytes). Returns None if malformed."""
    m = re.match(r"^data:([^;,]+)(;base64)?,(.*)$", data_url, re.DOTALL)
    if not m:
        return None
    mime = m.group(1).strip().lower()
    try:
        raw = base64.b64decode(m.group(3), validate=True)
    except Exception:  # noqa: BLE001 — malformed Base64 is a categorized reject
        return None
    return (mime, raw) if raw else None


def clamp_limits(limits: Mapping[str, Any]) -> dict[str, float]:
    timeout_s = float(limits.get("timeout_s", 240))
    poll_s = float(limits.get("poll_interval_s", 3.0))
    challenge_wait = float(limits.get("challenge_wait_s", 180))
    return {
        "timeout_s": min(max(10.0, timeout_s), HARD_MAX_TIMEOUT_S),
        "poll_interval_s": min(max(1.0, poll_s), HARD_MAX_POLL_INTERVAL_S),
        "min_data_url_chars": float(limits.get("min_data_url_chars", 5000)),
        "include_canvas_fallback": bool(limits.get("include_canvas_fallback", True)),
        # Wait for a HUMAN to approve a challenge page (we never solve it):
        # 0 = disabled (blocked_by_provider immediately).
        "challenge_wait_s": min(max(0.0, challenge_wait), 600.0),
    }


def clamp_pacing(launch: Mapping[str, Any]) -> dict[str, Any]:
    pacing = launch.get("human_pacing", {}) or {}
    lo = int(pacing.get("type_delay_ms_min", 15))
    hi = int(pacing.get("type_delay_ms_max", 45))
    lo = min(max(5, lo), 300)
    hi = min(max(lo, hi), HARD_MAX_TYPE_DELAY_MS)
    pause_chance = float(pacing.get("pause_chance", 0.05))
    pause_lo = int(pacing.get("pause_ms_min", 120))
    pause_hi = int(pacing.get("pause_ms_max", 350))
    pause_lo = min(max(50, pause_lo), 2000)
    pause_hi = min(max(pause_lo, pause_hi), 2000)
    return {
        "type_delay_ms_min": lo,
        "type_delay_ms_max": hi,
        "pause_chance": min(max(0.0, pause_chance), 0.5),
        "pause_ms_min": pause_lo,
        "pause_ms_max": pause_hi,
    }


SITE_COUNTS: tuple[int, ...] = (2, 4, 6, 8, 16, 32)  # the site's real options
# (user report 2026-10-16: 2/4/6/8/16/32 — never 1/2/3/4).


def snap_to_site_count(count: int) -> int:
    """Round a requested count UP to the nearest value the site can produce."""
    for value in SITE_COUNTS:
        if value >= count:
            return value
    return SITE_COUNTS[-1]


class _Restart(Exception):
    """Internal: the operator approved a challenge — restart the flow once."""


def pick_launch_mode(
    launch_cfg: Mapping[str, Any],
    *,
    xvfb_available: bool,
    force_headless: bool = False,
    force_headed: bool = False,
) -> str:
    """Choose how the browser runs (user UX rules 2026-10-16):
    - "window":  a real on-screen window (last resort; minimized, never on top).
    - "virtual": a REAL headed browser on an invisible Xvfb display — no window on
      the user's desktop, but pages see a normal windowed browser (no challenge).
    - "headless": pure headless (fastest; currently challenged by Cloudflare).
    Priority: explicit flags > virtual (if Xvfb exists) > headless > window."""
    if force_headed:
        return "window"
    if force_headless:
        return "headless"
    prefer = str(launch_cfg.get("virtual_display", "auto")).lower()
    if prefer in ("auto", "true", "1", "yes") and xvfb_available:
        return "virtual"
    if bool(launch_cfg.get("headless", True)):
        return "headless"
    return "window"


class PerchanceProvider:
    name = "perchance_playwright"

    def __init__(self, cfg: Mapping[str, Any]) -> None:
        self._cfg = cfg
        self._limits = clamp_limits(cfg.get("limits", {}) or {})
        self._pacing = clamp_pacing(cfg.get("launch", {}) or {})
        launch_cfg = cfg.get("launch", {}) or {}
        self._headless_default = bool(launch_cfg.get("headless", True))
        self._mode_default = pick_launch_mode(
            launch_cfg,
            xvfb_available=bool(shutil.which("Xvfb")),
            force_headless=bool(launch_cfg.get("force_headless", False)),
            force_headed=bool(launch_cfg.get("force_headed", False)),
        )
        self._mode: str = self._mode_default
        self._context: Any = None
        self._browser: Any = None
        self._pw: Any = None
        self._xvfb_proc: Any = None
        self._virtual_display: str = ""

    # ---- browser lifecycle -------------------------------------------------
    async def _ensure_virtual_display(self) -> None:  # noqa: ANN202
        if self._virtual_display:
            return
        if not shutil.which("Xvfb"):
            return
        self._xvfb_proc = subprocess.Popen(
            ["Xvfb", ":99", "-screen", "0", "1280x800x24"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        await asyncio.sleep(0.6)
        self._virtual_display = ":99"

    async def _launch(self) -> Any:  # noqa: ANN202
        from playwright.async_api import async_playwright

        launch_cfg = self._cfg.get("launch", {}) or {}
        self._pw = await async_playwright().start()
        kwargs: dict[str, Any] = {"headless": self._mode == "headless"}
        slow_mo = int(launch_cfg.get("slow_mo_ms", 100))
        if slow_mo > 0:
            kwargs["slow_mo"] = min(slow_mo, 2000)
        args = [str(a) for a in (launch_cfg.get("args") or [])]
        if self._mode == "virtual":
            await self._ensure_virtual_display()
            if self._virtual_display:
                kwargs["env"] = {**os.environ, "DISPLAY": self._virtual_display}
        if self._mode == "window":
            # User UX rule: never on top, never big — small window + real minimize.
            for extra in ("--window-size=520,360", "--start-minimized"):
                if extra not in args:
                    args.append(extra)
        if args:
            kwargs["args"] = args
        channel = str(launch_cfg.get("channel", "") or "")
        if channel:
            kwargs["channel"] = channel
        user_data_dir = str(launch_cfg.get("user_data_dir", "") or "")
        if user_data_dir:
            self._context = await self._pw.chromium.launch_persistent_context(
                user_data_dir, **kwargs
            )
            page = self._context.pages[0] if self._context.pages else await self._context.new_page()
        else:
            self._browser = await self._pw.chromium.launch(**kwargs)
            self._context = await self._browser.new_context()
            page = await self._context.new_page()
        if self._mode == "window":
            await self._minimize_window(page)
        return page

    async def _minimize_window(self, page: Any) -> None:  # noqa: ANN201, ANN202
        """Real OS-level minimize via CDP (the --start-minimized flag is ignored on
        Windows/WSLg — user report 2026-10-16). Best-effort, never raises."""
        try:
            cdp = await self._context.new_cdp_session(page)
            info = await cdp.send("Browser.getWindowForTarget")
            await cdp.send(
                "Browser.setWindowBounds",
                {"windowId": info["windowId"], "bounds": {"windowState": "minimized"}},
            )
        except Exception:  # noqa: BLE001
            pass

    async def _ensure_page(self) -> Any:  # noqa: ANN202
        if self._context is not None:
            return (
                self._context.pages[0] if self._context.pages else await self._context.new_page()
            )
        return await self._launch()

    async def close(self) -> None:
        if self._context is not None:
            try:
                await self._context.close()
            except Exception:  # noqa: BLE001
                pass
            self._context = None
        if self._browser is not None:
            try:
                await self._browser.close()
            except Exception:  # noqa: BLE001
                pass
            self._browser = None
        if self._pw is not None:
            try:
                await self._pw.stop()
            except Exception:  # noqa: BLE001
                pass
            self._pw = None
        if self._xvfb_proc is not None:
            try:
                self._xvfb_proc.terminate()
            except Exception:  # noqa: BLE001
                pass
            self._xvfb_proc = None
            self._virtual_display = ""
        self._mode = self._mode_default

    # ---- helpers ----------------------------------------------------------
    async def _blocked_reason(self, page: Any) -> str | None:  # noqa: ANN201
        try:
            text = await page.inner_text("body", timeout=5000)
        except Exception:  # noqa: BLE001
            text = ""
        return detect_block(text, page.url)

    async def _human_type(self, locator: Any, text: str) -> None:  # noqa: ANN201
        p = self._pacing
        for ch in text:
            await locator.type(ch)
            delay = random.uniform(p["type_delay_ms_min"], p["type_delay_ms_max"]) / 1000.0
            if random.random() < p["pause_chance"]:
                delay += random.uniform(p["pause_ms_min"], p["pause_ms_max"]) / 1000.0
            await asyncio.sleep(delay)

    async def _resolve_challenge(self, page: Any, notes: list[str], where: str) -> str:
        """A challenge page appeared. UX ladder (user rules 2026-10-16) — the
        automation NEVER solves or bypasses it:
        - headless  -> switch to a virtual (Xvfb) display: a REAL windowed browser
          on an invisible screen — pages see a normal browser (the interstitial
          clears by itself), and NO window appears on the user's desktop.
        - virtual   -> if a real checkbox ever blocks, open ONE small minimized
          window for a human tick (never on top).
        - window    -> wait in the window.
        Each step waits for the page to clear, then restarts the flow in that mode
        (the mode stays for the rest of the batch). Returns "restart" or "blocked"."""
        budget = self._limits["challenge_wait_s"]
        if budget <= 0:
            return "blocked"
        if self._mode in ("headless", "virtual"):
            use_virtual = self._mode == "headless" and bool(shutil.which("Xvfb"))
            next_mode = "virtual" if use_virtual else "window"
            if use_virtual:
                print(
                    f"[provider] challenge ({where}) — switching to a virtual "
                    "(invisible) display: real browser, NO window on your desktop. "
                    f"Waiting up to {budget:.0f}s..."
                )
            else:
                if not shutil.which("Xvfb"):
                    print(
                        "[provider] tip: install Xvfb for fully windowless runs: "
                        "sudo apt install -y xvfb"
                    )
                print(
                    f"[provider] challenge ({where}) — opening ONE small minimized "
                    "window (never on top; tick a checkbox only if one appears). "
                    f"Waiting up to {budget:.0f}s..."
                )
            await self.close()
            self._mode = next_mode
            page = await self._launch()
            try:
                await page.goto(
                    self._cfg["page_url"], wait_until="domcontentloaded", timeout=60000
                )
            except Exception:  # noqa: BLE001
                pass
        else:
            print(
                f"[provider] challenge ({where}) — waiting up to {budget:.0f}s for the "
                "security check to clear (usually automatic; tick only if a checkbox appears)"
            )
        deadline = time.monotonic() + budget
        while time.monotonic() < deadline:
            await asyncio.sleep(5)
            if not await self._blocked_reason(page):
                notes.append(f"challenge_cleared_in_{self._mode}")
                print(f"[provider] challenge cleared — continuing ({self._mode})")
                return "restart"
        return "blocked"

    async def _collect(self, page: Any) -> list[str]:  # noqa: ANN201
        js = _JS_SCAN.replace(
            "__CANVAS_FALLBACK__",
            "true" if self._limits["include_canvas_fallback"] else "false",
        )
        urls: list[str] = []
        for frame in page.frames:
            try:
                found = await frame.evaluate(js)
            except Exception:  # noqa: BLE001 — frame may be detached or cross-origin
                continue
            urls.extend(found)
        seen: set[str] = set()
        out: list[str] = []
        for u in urls:
            if u not in seen and len(u) > self._limits["min_data_url_chars"]:
                seen.add(u)
                out.append(u)
        return out

    async def _page_diagnostics(self, page: Any) -> str:  # noqa: ANN201
        """Short, safe diagnostic string (never tokens/cookies/Base64): url, whether
        the output iframe exists, and a page-text snippet — makes selector failures
        actionable (user test 2026-10-16: 'page structure changed?' was not enough)."""
        iframe = self._cfg["output_iframe"]
        try:
            iframe_count = await page.locator(iframe).count()
        except Exception:  # noqa: BLE001
            iframe_count = -1
        try:
            text = " ".join((await page.inner_text("body", timeout=5000)).split())[:100]
        except Exception:  # noqa: BLE001
            text = ""
        return f"url={page.url} iframe[{iframe}]={iframe_count} body≈{text!r}"

    async def _apply_count_select(self, frame: Any, wanted: int) -> tuple[int, str]:
        """Set the site's images-per-run selector. The selector is MEASURED at
        runtime (the <select> whose options are the site counts), never invented
        (AGENT.md). If not found, the site keeps its own default — noted."""
        try:
            count = await frame.locator("select").count()
        except Exception:  # noqa: BLE001
            return wanted, "count_select_unavailable"
        for i in range(count):
            sel = frame.locator("select").nth(i)
            try:
                vals = [
                    str(v).strip()
                    for v in await sel.evaluate(
                        "el => Array.from(el.options).map(o => o.value)"
                    )
                ]
            except Exception:  # noqa: BLE001
                continue
            if str(wanted) in vals and {"2", "4", "8"}.issubset(set(vals)):
                try:
                    await sel.select_option(value=str(wanted), timeout=5000)
                    return wanted, ""
                except Exception:  # noqa: BLE001
                    return wanted, "count_select_failed"
        return wanted, "count_select_not_found"

    async def _prompt_visible(self, page: Any, prompt_box: Any, timeout_ms: int) -> bool:  # noqa: ANN001, ANN201, ANN202
        try:
            await prompt_box.wait_for(state="visible", timeout=timeout_ms)
            return True
        except Exception:  # noqa: BLE001
            return False

    # ---- interface --------------------------------------------------------
    async def health_check(self) -> bool:
        """Page loads, not blocked, prompt visible. Never generates an image."""
        try:
            page = await self._launch()
            await page.goto(self._cfg["page_url"], wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(SETTLE_MS)
            if await self._blocked_reason(page):
                return False
            frame = page.frame_locator(self._cfg["output_iframe"])
            await frame.locator(self._cfg["selectors"]["prompt_textarea"]).wait_for(
                state="visible", timeout=PROMPT_VISIBLE_TIMEOUT_MS
            )
            return True
        except Exception:  # noqa: BLE001
            return False
        finally:
            await self.close()

    async def generate(self, request: GenerationRequest) -> GenerationResult:
        t0 = time.monotonic()
        notes: list[str] = []

        def finish(status: str, candidates: list[ImageCandidate], code=None, summary=None):  # noqa: ANN001, ANN202
            return GenerationResult(
                status=status,
                candidates=candidates,
                elapsed_s=round(time.monotonic() - t0, 2),
                provider=self.name,
                error_code=code,
                error_summary=summary,
                notes=tuple(notes),
            )

        # Keep the browser across generate() calls (user UX rule 2026-10-16):
        # one background window per batch, fewer challenges; close() ends it.
        if self._context is None:
            self._mode = self._mode_default
        restarts = 0
        try:
            while True:
                try:
                    return await self._run_flow(
                        request, notes, finish, allow_escalation=restarts < MAX_CHALLENGE_RESTARTS
                    )
                except _Restart:
                    restarts += 1
                    if restarts > MAX_CHALLENGE_RESTARTS:
                        return finish(
                            STATUS_BLOCKED, [], ERROR_BLOCKED,
                            "challenge reappeared after it cleared",
                        )
                    notes.append(f"flow_restarted_after_challenge_{restarts}")
        except Exception as e:  # noqa: BLE001
            return finish(STATUS_ERROR, [], ERROR_PROVIDER,
                          f"unexpected provider failure: {type(e).__name__}")

    async def _run_flow(self, request, notes, finish, *, allow_escalation):  # noqa: ANN001, ANN202
        page = await self._ensure_page()

        try:
            await page.goto(
                self._cfg["page_url"], wait_until="domcontentloaded", timeout=60000
            )
        except Exception as e:  # noqa: BLE001
            return finish(
                STATUS_ERROR, [], "page_error", f"page failed to load: {type(e).__name__}"
            )
        await page.wait_for_timeout(SETTLE_MS)

        reason = await self._blocked_reason(page)
        if reason:
            if allow_escalation:
                decision = await self._resolve_challenge(page, notes, "on load")
                if decision == "restart":
                    raise _Restart()
            return finish(STATUS_BLOCKED, [], ERROR_BLOCKED, f"blocked on load: {reason}")

        frame = page.frame_locator(self._cfg["output_iframe"])
        selectors = self._cfg["selectors"]
        prompt_box = frame.locator(selectors["prompt_textarea"])
        if not await self._prompt_visible(page, prompt_box, PROMPT_VISIBLE_TIMEOUT_MS):
            # One bounded hydration reload: page scripts sometimes fail to init on
            # first load (seen in headless test 2026-10-16). Controlled, not a retry.
            notes.append("page_reloaded_for_hydration")
            try:
                await page.reload(wait_until="domcontentloaded", timeout=60000)
                await page.wait_for_timeout(SETTLE_MS)
            except Exception:  # noqa: BLE001
                pass
            if not await self._prompt_visible(page, prompt_box, 30000):
                diag = await self._page_diagnostics(page)
                return finish(
                    "error", [], "selector_timeout",
                    f"prompt textarea not visible — {diag}",
                )

        wanted = snap_to_site_count(request.count)
        applied, note = await self._apply_count_select(frame, wanted)
        if note:
            notes.append(note)
        await prompt_box.click()
        await asyncio.sleep(0.5)
        await page.keyboard.press("Control+A")
        await page.keyboard.press("Backspace")
        await self._human_type(prompt_box, request.prompt)

        if request.style_value:
            try:
                await frame.locator(selectors["style_select"]).select_option(
                    value=request.style_value, timeout=10000
                )
            except Exception:  # noqa: BLE001
                notes.append("style_select_failed")  # non-fatal (user decision)
        gen = frame.locator(selectors["generate_button"])
        try:
            await gen.hover()
            await asyncio.sleep(random.uniform(0.5, 1.2))
            await gen.click(force=True, timeout=30000)
        except Exception:  # noqa: BLE001
            return finish(
                "error", [], "selector_timeout",
                "generate button not clickable — verify selectors in config",
            )

        deadline = time.monotonic() + self._limits["timeout_s"]
        collected: dict[str, ImageCandidate] = {}
        while time.monotonic() < deadline:
            await asyncio.sleep(self._limits["poll_interval_s"])
            reason = await self._blocked_reason(page)
            if reason:
                if allow_escalation:
                    decision = await self._resolve_challenge(page, notes, "during poll")
                    if decision == "restart":
                        raise _Restart()
                return finish(STATUS_BLOCKED, list(collected.values()), ERROR_BLOCKED,
                              f"blocked during poll: {reason}")
            for url in await self._collect(page):
                if url in collected:
                    continue
                parsed = decode_data_url(url)
                if not parsed:
                    continue
                mime, raw = parsed
                collected[url] = ImageCandidate(
                    data=raw, claimed_mime=mime, source="img[data-url]"
                )
            if len(collected) >= request.count:
                break

        candidates = list(collected.values())
        if len(candidates) >= request.count:
            return finish(STATUS_OK, candidates)
        if candidates:
            return finish(STATUS_PARTIAL, candidates)
        return finish(STATUS_TIMEOUT, [], ERROR_TIMEOUT,
                      "no images appeared before the timeout")
