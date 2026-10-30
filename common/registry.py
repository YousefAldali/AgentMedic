# Prompt registry (versioned prompts per agent) and the dependency map.
import json
import time

from common import db
from common.config import DATA_DIR, get_config


def seed_prompts() -> None:
    # Load data/prompts.json into the registry if the agent has no prompts yet.
    seeds = json.loads((DATA_DIR / "prompts.json").read_text(encoding="utf-8"))
    for agent, versions in seeds.items():
        if db.query("SELECT 1 FROM prompts WHERE agent = ? LIMIT 1", (agent,)):
            continue
        for v in versions:
            db.execute(
                "INSERT INTO prompts(agent, version, prompt, created_at, is_active, known_good) "
                "VALUES(?, ?, ?, ?, ?, ?)",
                (agent, v["version"], v["prompt"], time.time(),
                 int(v.get("is_active", False)), int(v.get("known_good", False))),
            )


def get_active_prompt(agent: str) -> tuple[int, str]:
    rows = db.query(
        "SELECT version, prompt FROM prompts WHERE agent = ? AND is_active = 1 "
        "ORDER BY version DESC LIMIT 1", (agent,))
    if not rows:
        raise LookupError(f"No active prompt for agent '{agent}'")
    return rows[0]["version"], rows[0]["prompt"]


def activate(agent: str, version: int) -> None:
    conn = db.connect()
    try:
        conn.execute("UPDATE prompts SET is_active = 0 WHERE agent = ?", (agent,))
        conn.execute("UPDATE prompts SET is_active = 1 WHERE agent = ? AND version = ?", (agent, version))
        conn.commit()
    finally:
        conn.close()


def last_known_good(agent: str) -> int | None:
    rows = db.query(
        "SELECT version FROM prompts WHERE agent = ? AND known_good = 1 "
        "ORDER BY version DESC LIMIT 1", (agent,))
    return rows[0]["version"] if rows else None


def list_versions(agent: str) -> list[dict]:
    return db.query(
        "SELECT version, is_active, known_good, created_at FROM prompts WHERE agent = ? "
        "ORDER BY version", (agent,))


def dependencies() -> dict[str, list[str]]:
    # {agent: [shared dependencies]}
    return get_config().get("dependencies") or {}


def upstream() -> dict[str, list[str]]:
    # {agent: [agents whose output it consumes]}
    return get_config().get("upstream") or {}
