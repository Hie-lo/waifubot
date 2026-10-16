"""Sprint 0 — controlled live provider spike (MANUAL, OPT-IN).

THROWAWAY script. Not application code. Run ONLY after the user approves Sprint 0.
Before running, tell the user a browser will open and a real generation request
will be submitted.

Usage (on the user's WSL2 machine):
    python tools/spike/spike_run.py --config tools/spike/spike_config.example.yaml --confirm-live

Safety:
- Refuses to run without --confirm-live.
- Stops on CAPTCHA / challenge / rate-limit / access-denied pages.
- Bounded: count, timeout, spacing, max images per run are config values with hard caps.
- Does NOT retry on blocked_by_provider.
- Records metrics to a CSV; never logs cookies/tokens/Base64.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import csv
import hashlib
import os
import re
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import yaml  # type: ignore
except Exception as e:  # noqa: BLE001
    print(f"PyYAML required: {e}", file=sys.stderr)
    sys.exit(2)

try:
    from playwright.async_api import async_playwright  # type: ignore
except Exception as e:  # noqa: BLE001
    print(f"Playwright required: {e}", file=sys.stderr)
    sys.exit(2)

try:
    from PIL import Image  # type: ignore
except Exception as e:  # noqa: BLE001
    print(f"Pillow required: {e}", file=sys.stderr)
    sys.exit(2)

HARD_MAX_COUNT = 10
HARD_MAX_TIMEOUT_S = 300
HARD_MAX_IMAGES = 20
BLOCKED_MARKERS = (
    "captcha",
    "challenge",
    "access denied",
    "rate limit",
    "too many requests",
    "verify you are human",
    "unusual traffic",
)


@dataclass
class SpikeConfig:
    page_url: str
    headless: bool = False
    count: int = 4
    timeout_s: int = 240
    poll_interval_s: float = 3.0
    spacing_s: int = 30
    min_width: int = 256
    min_height: int = 256
    max_bytes: int = 10 * 1024 * 1024
    output_dir: str = "storage/spike_out"
    prompt_textarea: str = "textarea[data-name='description']"
    style_select: str = "select[data-name='artStyle']"
    generate_button: str = "button#generateButtonEl"
    output_iframe: str = "#outputIframeEl"
    style_value: str = ""
    prompt: str = "a friendly anime girl, soft lighting"
    user_data_dir: str = ""  # persistent browser profile; "" = fresh profile each run


@dataclass
class RunMetrics:
    run_id: str
    started_at: str
    finished_at: str = ""
    elapsed_s: float = 0.0
    candidates_found: int = 0
    valid_images: int = 0
    errors: list[str] = field(default_factory=list)
    blocked: bool = False
    blocked_reason: str = ""
    saved_files: list[str] = field(default_factory=list)


def load_config(path: str) -> SpikeConfig:
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    cfg = SpikeConfig(**{k: v for k, v in raw.items() if k in SpikeConfig.__dataclass_fields__})
    # Hard caps
    cfg.count = min(max(1, cfg.count), HARD_MAX_COUNT)
    cfg.timeout_s = min(max(10, cfg.timeout_s), HARD_MAX_TIMEOUT_S)
    if cfg.poll_interval_s < 1.0:
        cfg.poll_interval_s = 1.0
    if cfg.spacing_s < 0:
        cfg.spacing_s = 0
    return cfg


def is_blocked(text: str, url: str) -> str | None:
    low = (text + " " + url).lower()
    for m in BLOCKED_MARKERS:
        if m in low:
            return m
    return None


def parse_data_url(data_url: str) -> tuple[str, bytes] | None:
    m = re.match(r"^data:([^;,]+)(;base64)?,(.*)$", data_url, re.DOTALL)
    if not m:
        return None
    mime = m.group(1).strip().lower()
    b64 = m.group(3)
    try:
        raw = base64.b64decode(b64, validate=True)
    except Exception:  # noqa: BLE001
        return None
    if not raw:
        return None
    return mime, raw


def validate_image(raw: bytes, mime: str, cfg: SpikeConfig) -> dict[str, Any] | None:
    if len(raw) > cfg.max_bytes:
        return None
    try:
        from io import BytesIO

        img = Image.open(BytesIO(raw))
        img.verify()
        # Re-open for dimensions/format (verify() invalidates)
        img2 = Image.open(BytesIO(raw))
        fmt = (img2.format or "").lower()
        w, h = img2.size
    except Exception:  # noqa: BLE001
        return None
    if w < cfg.min_width or h < cfg.min_height:
        return None
    if fmt not in ("png", "jpeg", "jpg", "webp", "gif"):
        return None
    return {"mime": mime, "format": fmt, "width": w, "height": h, "size": len(raw)}


def atomic_save(raw: bytes, out_dir: Path, fmt: str) -> str:
    out_dir.mkdir(parents=True, exist_ok=True)
    ext = "jpg" if fmt in ("jpeg", "jpg") else fmt
    name = f"{uuid.uuid4().hex}.{ext}"
    part = out_dir / (name + ".part")
    final = out_dir / name
    with open(part, "wb") as f:
        f.write(raw)
        f.flush()
        os.fsync(f.fileno())
    os.replace(part, final)
    return str(final)


async def collect_candidates(page, cfg: SpikeConfig) -> list[str]:
    """Collect direct data: image URLs by scanning ALL frames (PoC §8.1).

    Only img.src values that start with ``data:image/`` and are longer than
    5000 chars. No canvas conversion (§8.2).
    """
    urls: list[str] = []
    for frame in page.frames:
        try:
            found = await frame.evaluate(
                """() => {
                    const out = [];
                    for (const img of document.querySelectorAll('img')) {
                        const src = img.src || img.getAttribute('src') || '';
                        if (src.startsWith('data:image/') && src.length > 5000) {
                            out.push(src);
                        }
                    }
                    return out;
                }"""
            )
        except Exception:  # noqa: BLE001 — frame may be detached or cross-origin
            continue
        urls.extend(found)
    # De-dup preserving order
    seen: set[str] = set()
    out: list[str] = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


async def run_one(cfg: SpikeConfig, run_index: int) -> RunMetrics:
    run_id = f"run-{run_index:02d}-{uuid.uuid4().hex[:6]}"
    metrics = RunMetrics(run_id=run_id, started_at=datetime.now(timezone.utc).isoformat())
    t0 = time.monotonic()
    out_dir = Path(cfg.output_dir) / run_id

    async with async_playwright() as pw:
        browser = None
        if cfg.user_data_dir:
            # Persistent profile (PoC-style). Plain automation — no stealth args.
            context = await pw.chromium.launch_persistent_context(
                cfg.user_data_dir, headless=cfg.headless
            )
            page = context.pages[0] if context.pages else await context.new_page()
        else:
            browser = await pw.chromium.launch(headless=cfg.headless)
            context = await browser.new_context()
            page = await context.new_page()
        try:
            await page.goto(cfg.page_url, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(5000)  # let the output iframe settle (PoC)

            # Blocked check (initial)
            body_text = await page.inner_text("body")
            reason = is_blocked(body_text, page.url)
            if reason:
                metrics.blocked = True
                metrics.blocked_reason = reason
                metrics.errors.append(f"blocked_on_load:{reason}")
                return _finish(metrics, t0)

            # Interactions live inside the output iframe (PoC §8.1)
            frame = page.frame_locator(cfg.output_iframe)
            prompt_box = frame.locator(cfg.prompt_textarea)
            await prompt_box.wait_for(state="visible", timeout=30000)
            await prompt_box.click()
            await page.keyboard.press("Control+A")
            await page.keyboard.press("Backspace")
            await prompt_box.fill(cfg.prompt)
            print("prompt filled")

            if cfg.style_value and cfg.style_select:
                try:
                    await frame.locator(cfg.style_select).select_option(
                        value=cfg.style_value, timeout=10000
                    )
                except Exception as e:  # noqa: BLE001
                    metrics.errors.append(f"style_select_failed:{type(e).__name__}")

            await frame.locator(cfg.generate_button).click(force=True, timeout=30000)
            print("generate clicked")

            # Poll
            deadline = time.monotonic() + cfg.timeout_s
            collected_hashes: set[str] = set()
            while time.monotonic() < deadline:
                await asyncio.sleep(cfg.poll_interval_s)
                body_text = await page.inner_text("body")
                reason = is_blocked(body_text, page.url)
                if reason:
                    metrics.blocked = True
                    metrics.blocked_reason = reason
                    metrics.errors.append(f"blocked_during_poll:{reason}")
                    return _finish(metrics, t0)

                cands = await collect_candidates(page, cfg)
                metrics.candidates_found = max(metrics.candidates_found, len(cands))

                for du in cands:
                    parsed = parse_data_url(du)
                    if not parsed:
                        continue
                    mime, raw = parsed
                    info = validate_image(raw, mime, cfg)
                    if not info:
                        continue
                    h = hashlib.sha256(raw).hexdigest()
                    if h in collected_hashes:
                        continue
                    collected_hashes.add(h)
                    try:
                        saved = atomic_save(raw, out_dir, info["format"])
                        metrics.saved_files.append(saved)
                        metrics.valid_images = len(metrics.saved_files)
                    except Exception as e:  # noqa: BLE001
                        metrics.errors.append(f"save_failed:{type(e).__name__}")

                if metrics.valid_images >= cfg.count:
                    break

            if metrics.valid_images < cfg.count:
                metrics.errors.append(f"insufficient_images:{metrics.valid_images}/{cfg.count}")

        except Exception as e:  # noqa: BLE001
            metrics.errors.append(f"exception:{type(e).__name__}:{str(e)[:200]}")
        finally:
            try:
                await context.close()
            except Exception:  # noqa: BLE001
                pass
            # With a persistent context, context.close() is enough (§9.4).
            if browser is not None:
                try:
                    await browser.close()
                except Exception:  # noqa: BLE001
                    pass

    return _finish(metrics, t0)


def _finish(metrics: RunMetrics, t0: float) -> RunMetrics:
    metrics.elapsed_s = round(time.monotonic() - t0, 2)
    metrics.finished_at = datetime.now(timezone.utc).isoformat()
    return metrics


async def main_async(args: argparse.Namespace) -> int:
    if not args.confirm_live:
        print("ERROR: refusing to run without --confirm-live. This script opens a real browser and submits a real generation request.", file=sys.stderr)
        return 2

    cfg = load_config(args.config)
    print(f"[spike] page_url={cfg.page_url} headless={cfg.headless} count={cfg.count} timeout={cfg.timeout_s}s spacing={cfg.spacing_s}s")
    print("[spike] WARNING: a browser will open and a real generation request will be submitted.")

    metrics_list: list[RunMetrics] = []
    runs = min(max(1, args.runs), HARD_MAX_COUNT)
    for i in range(1, runs + 1):
        print(f"\n[spike] === run {i}/{runs} ===")
        m = await run_one(cfg, i)
        metrics_list.append(m)
        print(f"[spike] run {i}: valid={m.valid_images} candidates={m.candidates_found} elapsed={m.elapsed_s}s blocked={m.blocked} errors={m.errors}")
        if m.blocked:
            print("[spike] BLOCKED by provider. Stopping further runs.")
            break
        if i < runs and cfg.spacing_s > 0:
            print(f"[spike] spacing {cfg.spacing_s}s...")
            await asyncio.sleep(cfg.spacing_s)

    # Write CSV
    out_csv = Path(cfg.output_dir) / "spike_metrics.csv"
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    write_header = not out_csv.exists()
    with open(out_csv, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if write_header:
            w.writerow(["run_id", "started_at", "finished_at", "elapsed_s", "candidates_found", "valid_images", "blocked", "blocked_reason", "errors", "saved_files"])
        for m in metrics_list:
            w.writerow([m.run_id, m.started_at, m.finished_at, m.elapsed_s, m.candidates_found, m.valid_images, m.blocked, m.blocked_reason, ";".join(m.errors), ";".join(m.saved_files)])

    print(f"\n[spike] metrics written to {out_csv}")
    print("[spike] Done. Review results before any further live runs.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--confirm-live", action="store_true")
    ap.add_argument("--runs", type=int, default=1, help="number of controlled runs (max 10)")
    args = ap.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())