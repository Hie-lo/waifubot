"""Sprint 0 — controlled live provider spike (MANUAL, OPT-IN). THROWAWAY script.

The browser flow is DELEGATED to the product adapter
(`src/waifubot/core/providers/perchance.py`) — the exact code path used by
`python -m waifubot.cli live-smoke`. This tool only adds: multiple runs with
spacing, metrics CSV, and its own validation/saving under storage/spike_out.

Usage (on the user's WSL2 machine):
    python tools/spike/spike_run.py --config tools/spike/spike_config.example.yaml --confirm-live

Safety:
- Refuses to run without --confirm-live.
- Stops on CAPTCHA / challenge / rate-limit / access-denied (blocked_by_provider).
- Bounded: count, timeout, spacing are config values with hard caps.
- Records metrics to a CSV; never logs cookies/tokens/Base64.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import os
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

try:
    import yaml  # type: ignore
except Exception as e:  # noqa: BLE001
    print(f"PyYAML required: {e}", file=sys.stderr)
    sys.exit(2)

try:
    from waifubot.core.image_provider import STATUS_BLOCKED, GenerationRequest
    from waifubot.core.providers.perchance import PerchanceProvider
except Exception as e:  # noqa: BLE001
    print(f"waifubot package required (run from the repo): {e}", file=sys.stderr)
    sys.exit(2)

try:
    from PIL import Image  # type: ignore
except Exception as e:  # noqa: BLE001
    print(f"Pillow required: {e}", file=sys.stderr)
    sys.exit(2)

HARD_MAX_COUNT = 10
HARD_MAX_TIMEOUT_S = 300
HARD_MAX_IMAGES = 20


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
    # Human-paced typing (user-approved 2026-10-16 after a plain fill() run got blocked)
    type_delay_ms_min: int = 40
    type_delay_ms_max: int = 120
    # Browser automation profile — user decision 2026-10-16: match the user's working PoC
    slow_mo_ms: int = 100
    launch_args: list[str] = field(default_factory=list)
    include_canvas_fallback: bool = True  # PoC extraction fallback; spike-only


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
    cfg.type_delay_ms_min = int(min(max(10, cfg.type_delay_ms_min), 300))
    cfg.type_delay_ms_max = int(min(max(cfg.type_delay_ms_min, cfg.type_delay_ms_max), 500))
    cfg.slow_mo_ms = int(min(max(0, cfg.slow_mo_ms), 2000))
    if not isinstance(cfg.launch_args, list):
        cfg.launch_args = []
    cfg.launch_args = [str(a) for a in cfg.launch_args]
    return cfg


def provider_config(cfg: SpikeConfig) -> dict[str, Any]:
    """Map the spike config onto the product adapter config (one shared flow)."""
    return {
        "name": "perchance_playwright",
        "page_url": cfg.page_url,
        "output_iframe": cfg.output_iframe,
        "selectors": {
            "prompt_textarea": cfg.prompt_textarea,
            "style_select": cfg.style_select,
            "generate_button": cfg.generate_button,
        },
        "style_values": {},
        "launch": {
            "headless": cfg.headless,
            "slow_mo_ms": cfg.slow_mo_ms,
            "user_data_dir": cfg.user_data_dir,
            "args": list(cfg.launch_args),
            "human_pacing": {
                "type_delay_ms_min": cfg.type_delay_ms_min,
                "type_delay_ms_max": cfg.type_delay_ms_max,
            },
        },
        "limits": {
            "timeout_s": cfg.timeout_s,
            "poll_interval_s": cfg.poll_interval_s,
            "min_data_url_chars": 5000,
            "include_canvas_fallback": cfg.include_canvas_fallback,
        },
    }


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


async def run_one(cfg: SpikeConfig, run_index: int) -> RunMetrics:
    run_id = f"run-{run_index:02d}-{uuid.uuid4().hex[:6]}"
    metrics = RunMetrics(run_id=run_id, started_at=datetime.now(UTC).isoformat())
    t0 = time.monotonic()
    out_dir = Path(cfg.output_dir) / run_id

    provider = PerchanceProvider(provider_config(cfg))
    try:
        result = await provider.generate(
            GenerationRequest(
                prompt=cfg.prompt,
                count=cfg.count,
                style_value=cfg.style_value,
                timeout_s=float(cfg.timeout_s),
            )
        )
    except Exception as e:  # noqa: BLE001
        metrics.errors.append(f"exception:{type(e).__name__}:{str(e)[:200]}")
        return _finish(metrics, t0)
    finally:
        await provider.close()

    metrics.candidates_found = len(result.candidates)
    if result.status == STATUS_BLOCKED:
        metrics.blocked = True
        metrics.blocked_reason = result.error_summary or "blocked"
        metrics.errors.append(f"blocked:{result.error_summary or ''}")
        return _finish(metrics, t0)
    if result.error_code:
        metrics.errors.append(f"{result.error_code}:{result.error_summary or ''}")
    for note in result.notes:
        metrics.errors.append(note)

    seen: set[str] = set()
    for cand in result.candidates:
        info = validate_image(cand.data, cand.claimed_mime, cfg)
        if not info:
            metrics.errors.append("candidate_rejected_by_validation")
            continue
        digest = hashlib.sha256(cand.data).hexdigest()
        if digest in seen:
            continue
        seen.add(digest)
        try:
            saved = atomic_save(cand.data, out_dir, info["format"])
            metrics.saved_files.append(saved)
            metrics.valid_images = len(metrics.saved_files)
        except Exception as e:  # noqa: BLE001
            metrics.errors.append(f"save_failed:{type(e).__name__}")

    if metrics.valid_images < cfg.count:
        metrics.errors.append(f"insufficient_images:{metrics.valid_images}/{cfg.count}")
    return _finish(metrics, t0)


def _finish(metrics: RunMetrics, t0: float) -> RunMetrics:
    metrics.elapsed_s = round(time.monotonic() - t0, 2)
    metrics.finished_at = datetime.now(UTC).isoformat()
    return metrics


async def main_async(args: argparse.Namespace) -> int:
    if not args.confirm_live:
        print("ERROR: refusing to run without --confirm-live. This script opens a real browser and submits a real generation request.", file=sys.stderr)
        return 2

    cfg = load_config(args.config)
    print(f"[spike] page_url={cfg.page_url} headless={cfg.headless} count={cfg.count} timeout={cfg.timeout_s}s spacing={cfg.spacing_s}s")
    print("[spike] WARNING: a browser will open and a real generation request will be submitted.")
    print("[spike] browser flow: shared product adapter (PerchanceProvider) — the tested code path")

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
