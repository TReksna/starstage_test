"""StarSage database layer.

Connection management, thin query helpers, the versioned prompt/config
accessors, and the prompt-module text-backup writer.
"""
import hashlib
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import List, Optional

from settings import settings

DB_PATH = settings.DATABASE_URL.replace("sqlite:///", "")

# Plain-text backups of prompt modules; kept in sync when a module is edited.
PROMPT_BACKUP_DIR = Path(__file__).parent / "prompt_modules"


def write_prompt_backup(module_key: str, content: str) -> None:
    """Mirror a prompt module's active content to prompt_modules/<key>.txt."""
    PROMPT_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    (PROMPT_BACKUP_DIR / f"{module_key}.txt").write_text(content, encoding="utf-8")


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def db_row(conn: sqlite3.Connection, sql: str, params=()) -> Optional[sqlite3.Row]:
    return conn.execute(sql, params).fetchone()


def db_rows(conn: sqlite3.Connection, sql: str, params=()) -> List[sqlite3.Row]:
    return conn.execute(sql, params).fetchall()


def db_insert(conn: sqlite3.Connection, sql: str, params=()) -> int:
    cur = conn.execute(sql, params)
    return cur.lastrowid


def get_active_value(conn: sqlite3.Connection, option_key: str, default: str = "") -> str:
    row = db_row(
        conn,
        """SELECT cv.value FROM config_options co
           JOIN config_versions cv ON co.active_version_id = cv.id
           WHERE co.option_key = ?""",
        (option_key,),
    )
    return row["value"] if row else default


def get_active_prompt(conn: sqlite3.Connection, module_key: str) -> str:
    row = db_row(
        conn,
        """SELECT pv.content FROM prompt_modules pm
           JOIN prompt_versions pv ON pm.active_version_id = pv.id
           WHERE pm.module_key = ?""",
        (module_key,),
    )
    return row["content"] if row else ""


def compute_state_hash(conn: sqlite3.Connection) -> tuple[str, str]:
    """Return (prompt_hash, config_hash) for the current active state."""
    prompts = db_rows(
        conn,
        "SELECT pm.module_key, pv.content FROM prompt_modules pm "
        "JOIN prompt_versions pv ON pm.active_version_id=pv.id ORDER BY pm.module_key",
    )
    configs = db_rows(
        conn,
        "SELECT co.option_key, cv.value FROM config_options co "
        "JOIN config_versions cv ON co.active_version_id=cv.id ORDER BY co.option_key",
    )
    phash = hashlib.sha256(json.dumps([(r["module_key"], r["content"]) for r in prompts]).encode()).hexdigest()[:12]
    chash = hashlib.sha256(json.dumps([(r["option_key"], r["value"]) for r in configs]).encode()).hexdigest()[:12]
    return phash, chash
