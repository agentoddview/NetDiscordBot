import sqlite3
from pathlib import Path

DB_PATH = Path("bot_data.db")


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_connection()
    cur = conn.cursor()

    # Shift tracking
    cur.execute("""
        CREATE TABLE IF NOT EXISTS shifts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            guild_id INTEGER NOT NULL,
            start_time TEXT NOT NULL,
            end_time TEXT
        )
    """)

    # LOA tracking
    cur.execute("""
        CREATE TABLE IF NOT EXISTS loas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            guild_id INTEGER NOT NULL,
            reason TEXT,
            start_date TEXT NOT NULL,
            end_date TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending'
        )
    """)

    # Settings table
    cur.execute("""
        CREATE TABLE IF NOT EXISTS guild_settings (
            guild_id INTEGER PRIMARY KEY,
            modlog_channel_id INTEGER,
            botlog_channel_id INTEGER,
            loa_channel_id INTEGER
        )
    """)

    # Add columns if the user didn't have them before
    cur.execute("PRAGMA table_info(guild_settings)")
    cols = {row["name"] for row in cur.fetchall()}
    if "botlog_channel_id" not in cols:
        cur.execute("ALTER TABLE guild_settings ADD COLUMN botlog_channel_id INTEGER")
    if "loa_channel_id" not in cols:
        cur.execute("ALTER TABLE guild_settings ADD COLUMN loa_channel_id INTEGER")

    # Clock stuff
    cur.execute("""
        CREATE TABLE IF NOT EXISTS clock_periods (
            guild_id INTEGER PRIMARY KEY,
            reset_at TEXT NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS clock_adjustments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            guild_id INTEGER NOT NULL,
            seconds INTEGER NOT NULL,
            created_at TEXT NOT NULL
        )
    """)

    conn.commit()
    conn.close()
