"""CLI integration tests (Sprint 1b exit): init-db, add-character, mock-generate.

Runs the real CLI in a subprocess against a temporary database and storage root.
No network is used (the mock provider only).
"""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ADULT = "adult woman, 25 years old, long blue hair, red eyes"


def _cli(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "PYTHONPATH": str(REPO_ROOT / "src"),
        "BOT_TOKEN": "123:test-token",
        "ADMIN_USER_IDS": "1",
        "DESTINATION_CHAT_ID": "-1001",
        "DB_URL": f"sqlite+aiosqlite:///{tmp_path / 'cli.db'}",
        "STORAGE_ROOT": str(tmp_path / "storage"),
    }
    return subprocess.run(
        [sys.executable, "-m", "waifubot.cli", *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=REPO_ROOT,
        timeout=120,
    )


def test_full_cli_flow(tmp_path: Path) -> None:
    r = _cli(tmp_path, "init-db")
    assert r.returncode == 0, r.stderr
    assert "database ready" in r.stdout

    r = _cli(tmp_path, "add-character", "--name", "Aki", "--identity", ADULT)
    assert r.returncode == 0, r.stderr
    assert "character saved" in r.stdout

    r = _cli(
        tmp_path,
        "mock-generate",
        "--character",
        "Aki",
        "--count",
        "3",
        "--seed",
        "42",
        "--idempotency-key",
        "cli:test-1",
    )
    assert r.returncode == 0, r.stderr + r.stdout
    assert "status=succeeded" in r.stdout
    assert "images stored: 3 (pending_review)" in r.stdout

    con = sqlite3.connect(tmp_path / "cli.db")
    try:
        jobs = con.execute("select status, prompt_snapshot, idempotency_key from jobs").fetchall()
        assert len(jobs) == 1
        assert jobs[0][0] == "succeeded"
        assert jobs[0][1].startswith(ADULT)
        assert jobs[0][2] == "cli:test-1"
        images = con.execute("select status, sha256, width, height from images").fetchall()
        assert len(images) == 3
        assert all(row[0] == "pending_review" for row in images)
    finally:
        con.close()

    saved = list((tmp_path / "storage" / "images").glob("*.png"))
    assert len(saved) == 3


def test_cli_add_character_rejects_minor_identity(tmp_path: Path) -> None:
    _cli(tmp_path, "init-db")
    r = _cli(tmp_path, "add-character", "--name", "Bad", "--identity", "girl, 15 years old")
    assert r.returncode == 2
    assert "error" in r.stderr


def test_cli_mock_generate_blocked_mode_fails_job(tmp_path: Path) -> None:
    _cli(tmp_path, "init-db")
    r = _cli(
        tmp_path,
        "mock-generate",
        "--identity",
        ADULT,
        "--mode",
        "blocked",
        "--idempotency-key",
        "cli:blocked-1",
    )
    assert r.returncode == 1
    assert "blocked_by_provider" in r.stdout

    con = sqlite3.connect(tmp_path / "cli.db")
    try:
        status, code = con.execute("select status, error_code from jobs").fetchone()
        assert status == "failed"
        assert code == "blocked_by_provider"
    finally:
        con.close()


def test_cli_unknown_character_fails(tmp_path: Path) -> None:
    _cli(tmp_path, "init-db")
    r = _cli(tmp_path, "mock-generate", "--character", "Nobody")
    assert r.returncode == 1
    assert "not found" in r.stderr


def test_cli_live_smoke_refuses_without_confirm(tmp_path: Path) -> None:
    r = _cli(tmp_path, "live-smoke", "--runs", "1")
    assert r.returncode == 2
    assert "refusing to run without --confirm-live" in r.stderr
