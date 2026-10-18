"""Environment and configuration loading for WaifuBot (R25, R65)."""

import os
from pathlib import Path


def load_env(env_path: str | Path | None = None) -> dict[str, str]:
    """Loads environment variables from .env file into os.environ."""
    # Try python-dotenv first if installed in virtual environment
    try:
        import dotenv

        dotenv.load_dotenv(env_path or ".env")
    except ImportError:
        pass

    # Pure standard-library fallback parser (zero external dependencies)
    candidate_paths: list[Path] = []
    if env_path:
        candidate_paths.append(Path(env_path))
    else:
        candidate_paths.extend(
            [
                Path.cwd() / ".env",
                Path(".env"),
                Path(__file__).resolve().parents[3] / ".env",
            ]
        )

    loaded: dict[str, str] = {}
    for path in candidate_paths:
        if path.is_file():
            try:
                for line in path.read_text(encoding="utf-8").splitlines():
                    stripped = line.strip()
                    if not stripped or stripped.startswith("#") or "=" not in stripped:
                        continue
                    key, val = stripped.split("=", 1)
                    key = key.strip()
                    val = val.strip()
                    if (val.startswith('"') and val.endswith('"')) or (
                        val.startswith("'") and val.endswith("'")
                    ):
                        val = val[1:-1]
                    # Set in os.environ if not set or if empty
                    if key and (key not in os.environ or not os.environ[key]):
                        os.environ[key] = val
                    loaded[key] = val
                break
            except Exception:
                pass

    return loaded
