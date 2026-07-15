"""config/settings.toml 로더."""
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load() -> dict:
    with open(ROOT / "config" / "settings.toml", "rb") as f:
        return tomllib.load(f)
