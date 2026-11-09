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


def add_version(agent: str, prompt: str, known_good: bool = False) -> int:
    # Adds a new prompt version (not active yet) and returns its number.
    version = (db.query("SELECT MAX(version) AS v FROM prompts WHERE agent = ?", (agent,))[0]["v"] or 0) + 1
    db.execute("INSERT INTO prompts(agent, version, prompt, created_at, is_active, known_good) "
               "VALUES(?, ?, ?, ?, 0, ?)", (agent, version, prompt, time.time(), int(known_good)))
    return version


def active_info(agent: str) -> dict:
    rows = db.query("SELECT version, created_at, known_good FROM prompts WHERE agent = ? AND is_active = 1 "
                    "ORDER BY version DESC LIMIT 1", (agent,))
    return rows[0] if rows else {"version": None, "created_at": None, "known_good": 0}


def last_known_good(agent: str) -> int | None:
    rows = db.query(
        "SELECT version FROM prompts WHERE agent = ? AND known_good = 1 "
        "ORDER BY version DESC LIMIT 1", (agent,))
    return rows[0]["version"] if rows else None



def dependencies() -> dict[str, list[str]]:
    # {agent: [shared dependencies]}
    return get_config().get("dependencies") or {}


def upstream() -> dict[str, list[str]]:
    # {agent: [agents whose output it consumes]}
    return get_config().get("upstream") or {}
