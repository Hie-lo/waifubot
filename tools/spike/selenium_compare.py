"""Sprint 2 — Selenium comparison probe (THROWAWAY, per AGENT.md §8 decision:
"test both automation tools"). NOT application code. The production adapter is
async Playwright (src/waifubot/core/providers/perchance.py); this probe only
answers "does the same page work under Selenium with the same selectors and the
user-approved launch profile?".

The interaction sequence mirrors the WORKING PoC/spike (2026-10-16): persistent
profile, approved launch args, clear-then-type with human pacing, hover + pause
before the generate click, frame scan for data: images.

Requires: pip install selenium  (Selenium Manager fetches chromedriver).

Usage (on the user's WSL2 machine only):
    python tools/spike/selenium_compare.py --config config/providers/perchance.yaml --confirm-live

Rules: refuses without --confirm-live. No CAPTCHA solving, no bypass, no proxies.
On a challenge page: reports blocked_by_provider and stops.
"""
from __future__ import annotations

import argparse
import random
import sys
import time
from pathlib import Path

import yaml

HARD_TIMEOUT_S = 300
BLOCKED_MARKERS = (
    "captcha",
    "challenge",
    "access denied",
    "rate limit",
    "too many requests",
    "verify you are human",
    "unusual traffic",
)


def detect_block(text: str, url: str) -> str | None:
    low = f"{text} {url}".lower().replace("-", " ").replace("_", " ")
    for marker in BLOCKED_MARKERS:
        if marker in low:
            return marker
    return None


def human_type(element, text: str) -> None:
    """Type char-by-char with human pacing (same policy as the adapter)."""
    for ch in text:
        element.send_keys(ch)
        time.sleep(random.uniform(0.04, 0.12))


def clear_prompt(driver, prompt) -> None:
    """Robustly clear the textarea (click, select-all, backspace, verify)."""
    prompt.click()
    time.sleep(0.5)
    prompt.send_keys("\ue009" + "a")  # Ctrl+A (selenium Keys.CONTROL alias)
    prompt.send_keys("\ue003")  # Backspace
    if prompt.get_attribute("value"):
        prompt.clear()  # fallback: selenium built-in clear
    if prompt.get_attribute("value"):
        driver.execute_script("arguments[0].value = '';", prompt)  # last resort


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--confirm-live", action="store_true")
    ap.add_argument("--prompt", default="masterpiece, anime girl, blue hair, smiling, high quality")
    ap.add_argument("--timeout", type=int, default=240)
    args = ap.parse_args()

    if not args.confirm_live:
        print("ERROR: refusing to run without --confirm-live.", file=sys.stderr)
        return 2

    cfg = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    page_url = cfg["page_url"]
    selectors = cfg["selectors"]
    iframe = cfg["output_iframe"]
    launch = cfg.get("launch", {}) or {}
    timeout_s = min(max(10, args.timeout), HARD_TIMEOUT_S)

    try:
        from selenium import webdriver
        from selenium.webdriver.common.action_chains import ActionChains
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import Select, WebDriverWait
    except ImportError as e:
        print(f"selenium required: {e}", file=sys.stderr)
        return 2

    options = webdriver.ChromeOptions()
    if launch.get("headless"):
        options.add_argument("--headless=new")
    for a in launch.get("args", []):
        options.add_argument(str(a))
    if launch.get("user_data_dir"):
        options.add_argument(f"--user-data-dir={launch['user_data_dir']}")

    print(f"[selenium-compare] page={page_url} timeout={timeout_s}s")
    print("[selenium-compare] WARNING: a real browser will open and a real request is sent.")
    driver = webdriver.Chrome(options=options)
    t0 = time.monotonic()
    try:
        driver.get(page_url)
        time.sleep(5)  # settle (same as the working PoC)
        reason = detect_block(driver.find_element(By.TAG_NAME, "body").text, driver.current_url)
        if reason:
            print(f"[selenium-compare] BLOCKED on load: {reason} (blocked_by_provider)")
            return 1

        wait = WebDriverWait(driver, 30)
        wait.until(EC.frame_to_be_available_and_switch_to_it((By.CSS_SELECTOR, iframe)))
        prompt = wait.until(
            EC.visibility_of_element_located((By.CSS_SELECTOR, selectors["prompt_textarea"]))
        )

        clear_prompt(driver, prompt)
        human_type(prompt, args.prompt)
        if not prompt.get_attribute("value"):
            print("[selenium-compare] ERROR: prompt field is empty after typing")
            return 1
        print("[selenium-compare] prompt typed (old draft cleared)")

        try:
            Select(driver.find_element(By.CSS_SELECTOR, selectors["style_select"])).select_by_value(
                (cfg.get("style_values") or {}).get("anime", "")
            )
            print("[selenium-compare] style selected")
        except Exception:  # noqa: BLE001
            print("[selenium-compare] style select skipped")

        btn = driver.find_element(By.CSS_SELECTOR, selectors["generate_button"])
        # Hover + pause, then click — same behavior as the working PoC.
        try:
            ActionChains(driver).move_to_element(btn).pause(random.uniform(0.5, 1.2)).perform()
        except Exception:  # noqa: BLE001
            time.sleep(0.5)
        driver.execute_script("arguments[0].click();", btn)
        print("[selenium-compare] generate clicked — polling for images...")

        deadline = time.monotonic() + timeout_s
        seen: set[str] = set()
        while time.monotonic() < deadline:
            time.sleep(4)
            try:
                driver.switch_to.default_content()
                driver.switch_to.frame(driver.find_element(By.CSS_SELECTOR, iframe))
            except Exception:  # noqa: BLE001
                pass
            for img in driver.find_elements(By.CSS_SELECTOR, "img"):
                src = img.get_attribute("src") or ""
                if src.startswith("data:image/") and len(src) > 5000:
                    seen.add(src)
            reason = detect_block(
                driver.find_element(By.TAG_NAME, "body").text, driver.current_url
            )
            if reason:
                print(f"[selenium-compare] BLOCKED during poll: {reason} (blocked_by_provider)")
                return 1
            print(f"[selenium-compare] images so far: {len(seen)}")
            if len(seen) >= 4:
                break

        print(
            f"[selenium-compare] DONE images={len(seen)} "
            f"elapsed={round(time.monotonic() - t0, 1)}s"
        )
        print("[selenium-compare] compare with the Playwright adapter results, then decide.")
        return 0 if seen else 1
    except Exception as e:  # noqa: BLE001 — clean error instead of a raw traceback
        print(f"[selenium-compare] ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    finally:
        driver.quit()


if __name__ == "__main__":
    raise SystemExit(main())
