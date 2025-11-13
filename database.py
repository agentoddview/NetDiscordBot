import sqlite3
from pathlib import Path

DB_PATH = Path("bot_data.db")


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Create tables if they don't exist."""
    conn = get_connection()
    cur = conn.cursor()

    # Shift tracking: one row per shift
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS shifts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            guild_id INTEGER NOT NULL,
            start_time TEXT NOT NULL,
            end_time TEXT
        )
        """
    )

    # LOA tracking: basic example
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS loas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            guild_id INTEGER NOT NULL,
            reason TEXT,
            start_date TEXT NOT NULL,
            end_date TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending'
        )
        """
    )

    # Guild settings (e.g., mod log channel)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS guild_settings (
            guild_id INTEGER PRIMARY KEY,
            modlog_channel_id INTEGER
        )
        """
    )

    conn.commit()
    conn.close()
