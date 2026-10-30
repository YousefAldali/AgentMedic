# Loads config.yaml and .env once. Every module reads settings from here.
import os
from functools import lru_cache
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"

load_dotenv(ROOT / ".env")


@lru_cache
def get_config() -> dict:
    with open(ROOT / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def env(name: str, default: str | None = None) -> str | None:
    # Read an env var, treating empty strings as unset.
    value = os.getenv(name)
    return value if value not in (None, "") else default


def mock_llm() -> bool:
    return env("MOCK_LLM", "1") == "1"


def service_url(name: str) -> str:
    svc = get_config()["services"][name]
    return f"http://{svc['host']}:{svc['port']}"


def db_path() -> Path:
    return ROOT / get_config()["db_path"]
