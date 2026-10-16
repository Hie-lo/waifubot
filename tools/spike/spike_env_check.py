"""Sprint 0 — environment check (NO network, NO provider page).

Run on the target WSL2 machine:
    python tools/spike/spike_env_check.py

Reports Python, Playwright, Chromium, DISPLAY, RAM, disk. Does not open any site.
"""
from __future__ import annotations

import os
import platform
import shutil
import sys


def main() -> int:
    print("=== Sprint 0 environment check ===")
    print(f"Python: {sys.version}")
    print(f"Platform: {platform.platform()}")
    print(f"DISPLAY: {os.environ.get('DISPLAY', '(unset)')}")
    print(f"WSL_DISTRO_NAME: {os.environ.get('WSL_DISTRO_NAME', '(unset)')}")
    print(f"WSL_INTEROP: {os.environ.get('WSL_INTEROP', '(unset)')}")

    try:
        import playwright  # noqa: F401

        print(f"Playwright import: OK ({playwright.__version__ if hasattr(playwright, '__version__') else 'version unknown'})")
    except Exception as e:  # noqa: BLE001
        print(f"Playwright import: FAIL ({type(e).__name__}: {e})")

    # Chromium availability (do not launch)
    for name in ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable"):
        path = shutil.which(name)
        if path:
            print(f"Browser binary '{name}': {path}")

    # Playwright-managed chromium cache (common location)
    pw_cache = os.path.expanduser("~/.cache/ms-playwright")
    if os.path.isdir(pw_cache):
        print(f"Playwright cache dir exists: {pw_cache}")
        try:
            print("  contents: " + ", ".join(sorted(os.listdir(pw_cache))))
        except OSError as e:
            print(f"  (could not list: {e})")
    else:
        print(f"Playwright cache dir NOT found: {pw_cache}")

    # RAM
    try:
        with open("/proc/meminfo", encoding="utf-8") as f:
            mem = {}
            for line in f:
                k, v = line.split(":", 1)
                mem[k.strip()] = v.strip()
        print(f"MemTotal: {mem.get('MemTotal', '?')}")
        print(f"MemAvailable: {mem.get('MemAvailable', '?')}")
    except OSError as e:
        print(f"RAM info unavailable: {e}")

    # Disk
    try:
        usage = shutil.disk_usage("/")
        print(f"Disk /: total={usage.total // (1024**3)}GiB free={usage.free // (1024**3)}GiB")
    except OSError as e:
        print(f"Disk info unavailable: {e}")

    print("=== end ===")
    print("NOTE: this script does NOT open any provider page.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())