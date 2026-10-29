"""
FinTrack - database.py

Everything to do with creating and opening the SQLite database lives here.

* The database file is `fintrack.db`, stored next to app.py.
* `init_db()` only creates tables that do not exist yet ("IF NOT EXISTS"),
  so restarting Flask NEVER erases your data.
* Every query in the project uses `?` placeholders (parameterised SQL).
"""

import sqlite3
from datetime import datetime
from pathlib import Path

from flask import g

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "fintrack.db"

# ---------------------------------------------------------------------------
# Predefined categories: (name, icon, colour)
# ---------------------------------------------------------------------------
EXPENSE_CATEGORIES = [
    ("Food", "🍔", "#1F9D6B"),
    ("Travel", "🚌", "#3E7CB1"),
    ("College & Study", "📚", "#8A6FD1"),
    ("Entertainment", "🎬", "#E4574C"),
    ("Shopping", "🛍️", "#E8A33D"),
    ("Bills", "🧾", "#2A9AA6"),
    ("Other", "🏷️", "#7B8782"),
]

INCOME_CATEGORIES = [
    ("Allowance", "💰", "#1F9D6B"),
    ("Scholarship", "🎓", "#3E7CB1"),
    ("Gift", "🎁", "#E8A33D"),
    ("Part-time Income", "💼", "#8A6FD1"),
    ("Other", "🏷️", "#7B8782"),
]

# Colours handed out to categories the student creates themselves
CUSTOM_COLOURS = ["#C0617E", "#5E8C3A", "#B5651D", "#3C8DA8", "#7A5BB5", "#C2842B"]

# Editable example budgets (monthly limits in rupees)
DEFAULT_BUDGETS = {
    "Food": 2000,
    "Travel": 1000,
    "College & Study": 1000,
    "Entertainment": 800,
}

# "Frequent" one-tap items: (label, amount, category, icon). Editable in Settings.
DEFAULT_QUICK_ITEMS = [
    ("Auto", 60, "Travel", "🛺"),
    ("Bus", 20, "Travel", "🚌"),
    ("Snacks", 20, "Food", "🍿"),
    ("Snacks", 10, "Food", "🍿"),
    ("Coke", 40, "Food", "🥤"),
]

# Default values for the Settings page
DEFAULT_SETTINGS = {
    "display_name": "Ayesha",
    "currency": "INR",
    "opening_balance": "0",        # money you already had before using FinTrack
    "monthly_spend_target": "0",   # 0 = not set
    "monthly_savings_target": "0", # 0 = not set
    "warn_threshold": "80",        # budget warning at this % used
    "show_insights": "1",          # 1 = show smart insights / notifications
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS categories (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    name      TEXT NOT NULL,
    kind      TEXT NOT NULL CHECK (kind IN ('expense', 'income')),
    icon      TEXT NOT NULL DEFAULT '🏷️',
    color     TEXT NOT NULL DEFAULT '#7B8782',
    is_custom INTEGER NOT NULL DEFAULT 0,
    UNIQUE (name, kind)
);

CREATE TABLE IF NOT EXISTS transactions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    type        TEXT NOT NULL CHECK (type IN ('expense', 'income')),
    amount      REAL NOT NULL CHECK (amount > 0),
    category    TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    tx_date     TEXT NOT NULL,            -- YYYY-MM-DD (local date)
    tx_time     TEXT NOT NULL,            -- HH:MM (local time)
    is_demo     INTEGER NOT NULL DEFAULT 0, -- 1 = sample data
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tx_date ON transactions (tx_date);
CREATE INDEX IF NOT EXISTS idx_tx_cat  ON transactions (category);

CREATE TABLE IF NOT EXISTS budgets (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    category     TEXT NOT NULL,
    period       TEXT NOT NULL DEFAULT 'monthly',
    limit_amount REAL NOT NULL CHECK (limit_amount > 0),
    UNIQUE (category, period)
);

CREATE TABLE IF NOT EXISTS savings_goals (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    target_amount REAL NOT NULL CHECK (target_amount > 0),
    saved_amount  REAL NOT NULL DEFAULT 0 CHECK (saved_amount >= 0),
    target_date   TEXT,
    created_at    TEXT NOT NULL,
    reached_at    TEXT
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS dismissed_insights (
    insight_key  TEXT PRIMARY KEY,
    dismissed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS quick_items (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    label      TEXT NOT NULL,
    amount     REAL NOT NULL CHECK (amount > 0),
    category   TEXT NOT NULL,
    icon       TEXT NOT NULL DEFAULT '⚡'
);

CREATE TABLE IF NOT EXISTS weekly_reviews (
    week_start  TEXT PRIMARY KEY,   -- Monday of the reviewed week
    reviewed_at TEXT NOT NULL
);
"""


def get_db():
    """Open (once per request) and return the database connection."""
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def close_db(_error=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    """Create tables and starter data if they do not exist yet. Safe to run every start."""
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    # The Gmail feature was removed - clean up its leftover tables if they exist
    db.execute("DROP TABLE IF EXISTS email_suggestions")
    db.execute("DROP TABLE IF EXISTS gmail_seen")
    db.execute("DELETE FROM settings WHERE key = 'gmail_last_scan'")

    for name, icon, color in EXPENSE_CATEGORIES:
        db.execute(
            "INSERT OR IGNORE INTO categories (name, kind, icon, color) VALUES (?, 'expense', ?, ?)",
            (name, icon, color),
        )
    for name, icon, color in INCOME_CATEGORIES:
        db.execute(
            "INSERT OR IGNORE INTO categories (name, kind, icon, color) VALUES (?, 'income', ?, ?)",
            (name, icon, color),
        )

    for key, value in DEFAULT_SETTINGS.items():
        db.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, value))

    # The four example budgets are added only ONCE, so a budget you remove stays removed.
    already = db.execute("SELECT 1 FROM settings WHERE key = 'budgets_seeded'").fetchone()
    if not already:
        for category, limit_amount in DEFAULT_BUDGETS.items():
            db.execute(
                "INSERT OR IGNORE INTO budgets (category, period, limit_amount) VALUES (?, 'monthly', ?)",
                (category, limit_amount),
            )
        db.execute("INSERT INTO settings (key, value) VALUES ('budgets_seeded', '1')")

    # The frequent items are added only ONCE, so ones you delete stay deleted.
    if not db.execute("SELECT 1 FROM settings WHERE key = 'quick_seeded'").fetchone():
        for label, amount, category, icon in DEFAULT_QUICK_ITEMS:
            db.execute("INSERT INTO quick_items (label, amount, category, icon) VALUES (?, ?, ?, ?)",
                       (label, amount, category, icon))
        db.execute("INSERT INTO settings (key, value) VALUES ('quick_seeded', '1')")

    # One-time update for databases created earlier: swap my guessed Lunch/Chai items
    # for Snacks 10 and Coke 40 (only if you hadn't changed them), then never touch them again.
    if not db.execute("SELECT 1 FROM settings WHERE key = 'quick_v2'").fetchone():
        db.execute("DELETE FROM quick_items WHERE (label = 'Lunch' AND amount = 50) OR (label = 'Chai' AND amount = 10)")
        for label, amount, category, icon in DEFAULT_QUICK_ITEMS:
            is_new_default = (label, amount) in (("Snacks", 10), ("Coke", 40))
            exists = db.execute("SELECT 1 FROM quick_items WHERE label = ? AND amount = ?", (label, amount)).fetchone()
            if is_new_default and not exists:
                db.execute("INSERT INTO quick_items (label, amount, category, icon) VALUES (?, ?, ?, ?)",
                           (label, amount, category, icon))
        db.execute("INSERT INTO settings (key, value) VALUES ('quick_v2', '1')")

    db.commit()
    db.close()


def now_stamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
# Small settings helpers
# ---------------------------------------------------------------------------
def load_settings(db):
    """Return all settings as a dict with sensible Python types."""
    raw = {row["key"]: row["value"] for row in db.execute("SELECT key, value FROM settings")}
    for key, value in DEFAULT_SETTINGS.items():
        raw.setdefault(key, value)

    def num(key):
        try:
            return float(raw[key])
        except (TypeError, ValueError):
            return float(DEFAULT_SETTINGS[key])

    return {
        "display_name": raw["display_name"],
        "currency": raw["currency"],
        "opening_balance": num("opening_balance"),
        "monthly_spend_target": num("monthly_spend_target"),
        "monthly_savings_target": num("monthly_savings_target"),
        "warn_threshold": int(num("warn_threshold")),
        "show_insights": raw["show_insights"] == "1",
    }


def save_setting(db, key, value):
    db.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, str(value)),
    )


def get_raw_setting(db, key, default=None):
    row = db.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default