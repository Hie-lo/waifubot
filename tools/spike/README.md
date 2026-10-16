# Sprint 0 — Provider Feasibility Spike (THROWAWAY)

These scripts are **not application code**. They exist only for the Sprint 0 feasibility spike.

## Files

- `spike_env_check.py` — environment check (no network, no provider page). Safe to run anywhere.
- `spike_run.py` — controlled live provider run. Opens a real browser and submits a real generation request. Requires `--confirm-live`.
- `spike_config.example.yaml` — config template.

## Rules

- Never run `spike_run.py` in CI, tests, or at startup.
- Always tell the user before a live run that a browser will open and a real generation request will be submitted.
- Respect the permitted rate (spacing between runs). Do not exceed it.
- On CAPTCHA/challenge/rate-limit/access-denied: stop, record `blocked_by_provider`, notify admin. No bypass.
- Selectors are a baseline to verify, never assumed.

## How to run (on the user's WSL2 machine)

1. `python tools/spike/spike_env_check.py`
2. Copy `spike_config.example.yaml` to `spike_config.yaml` and verify selectors.
3. `python tools/spike/spike_run.py --config tools/spike/spike_config.yaml --confirm-live --runs 1`
4. Review metrics in `storage/spike_out/spike_metrics.csv`.
5. Repeat up to the permitted number of runs, spaced out.
6. Fill in `docs/spike-report.md` and decide go/no-go.