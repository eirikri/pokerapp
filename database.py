import sqlite3
from pathlib import Path
from flask import g

DB_PATH = Path(__file__).parent / "poker.db"


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(str(DB_PATH))
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def close_db(e=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    conn = sqlite3.connect(str(DB_PATH))

    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}

    # Migration 1: old stakes schema → games + blind_levels
    if "stakes" in tables and "games" not in tables:
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("DROP TABLE IF EXISTS sessions")
        conn.execute("DROP TABLE IF EXISTS stakes")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.commit()
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}

    # Migration 2: old sessions schema (buy_in/cash_out) → profit/hands
    if "sessions" in tables:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(sessions)").fetchall()}
        if "buy_in" in cols:
            conn.execute("PRAGMA foreign_keys = OFF")
            conn.execute("""
                CREATE TABLE sessions_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    club_id INTEGER NOT NULL REFERENCES clubs(id),
                    game_id INTEGER NOT NULL REFERENCES games(id),
                    blind_level_id INTEGER NOT NULL REFERENCES blind_levels(id),
                    date TEXT NOT NULL,
                    hands INTEGER,
                    profit REAL NOT NULL,
                    created_at TEXT DEFAULT (datetime('now'))
                )
            """)
            conn.execute("""
                INSERT INTO sessions_new (id, club_id, game_id, blind_level_id, date, profit, created_at)
                SELECT id, club_id, game_id, blind_level_id, date, (cash_out - buy_in), created_at
                FROM sessions
            """)
            conn.execute("DROP TABLE sessions")
            conn.execute("ALTER TABLE sessions_new RENAME TO sessions")
            conn.execute("PRAGMA foreign_keys = ON")
            conn.commit()

    # Migration 4: add 'misc' transaction type
    if "transactions" in tables:
        create_sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='transactions'"
        ).fetchone()
        if create_sql and "misc" not in create_sql[0]:
            conn.execute("PRAGMA foreign_keys = OFF")
            conn.execute("""
                CREATE TABLE transactions_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    club_id INTEGER NOT NULL REFERENCES clubs(id),
                    to_club_id INTEGER REFERENCES clubs(id),
                    type TEXT NOT NULL CHECK(type IN ('deposit', 'withdrawal', 'rakeback', 'swap', 'misc')),
                    amount REAL NOT NULL,
                    date TEXT NOT NULL,
                    notes TEXT,
                    created_at TEXT DEFAULT (datetime('now'))
                )
            """)
            conn.execute("""
                INSERT INTO transactions_new SELECT * FROM transactions
            """)
            conn.execute("DROP TABLE transactions")
            conn.execute("ALTER TABLE transactions_new RENAME TO transactions")
            conn.execute("PRAGMA foreign_keys = ON")
            conn.commit()
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}

    # Migration 3: merge swaps into transactions, add rakeback + swap types
    if "transactions" in tables:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(transactions)").fetchall()}
        if "to_club_id" not in cols:
            conn.execute("PRAGMA foreign_keys = OFF")
            conn.execute("""
                CREATE TABLE transactions_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    club_id INTEGER NOT NULL REFERENCES clubs(id),
                    to_club_id INTEGER REFERENCES clubs(id),
                    type TEXT NOT NULL CHECK(type IN ('deposit', 'withdrawal', 'rakeback', 'swap', 'misc')),
                    amount REAL NOT NULL,
                    date TEXT NOT NULL,
                    notes TEXT,
                    created_at TEXT DEFAULT (datetime('now'))
                )
            """)
            conn.execute("""
                INSERT INTO transactions_new (id, club_id, to_club_id, type, amount, date, notes, created_at)
                SELECT id, club_id, NULL, type, amount, date, notes, created_at
                FROM transactions
            """)
            if "swaps" in tables:
                conn.execute("""
                    INSERT INTO transactions_new (club_id, to_club_id, type, amount, date, notes, created_at)
                    SELECT from_club_id, to_club_id, 'swap', amount, date, notes, created_at
                    FROM swaps
                """)
                conn.execute("DROP TABLE swaps")
            conn.execute("DROP TABLE transactions")
            conn.execute("ALTER TABLE transactions_new RENAME TO transactions")
            conn.execute("PRAGMA foreign_keys = ON")
            conn.commit()
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}

    conn.executescript("""
        CREATE TABLE IF NOT EXISTS clubs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            notes TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS games (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS blind_levels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            small_blind REAL NOT NULL,
            big_blind REAL NOT NULL,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            club_id INTEGER NOT NULL REFERENCES clubs(id),
            game_id INTEGER NOT NULL REFERENCES games(id),
            blind_level_id INTEGER NOT NULL REFERENCES blind_levels(id),
            date TEXT NOT NULL,
            hands INTEGER,
            profit REAL NOT NULL,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS historical_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id INTEGER NOT NULL REFERENCES games(id),
            blind_level_id INTEGER NOT NULL REFERENCES blind_levels(id),
            hands INTEGER,
            profit REAL NOT NULL,
            currency TEXT NOT NULL DEFAULT 'NOK' CHECK(currency IN ('NOK', 'USD')),
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            club_id INTEGER NOT NULL REFERENCES clubs(id),
            to_club_id INTEGER REFERENCES clubs(id),
            type TEXT NOT NULL CHECK(type IN ('deposit', 'withdrawal', 'rakeback', 'swap', 'misc')),
            amount REAL NOT NULL,
            date TEXT NOT NULL,
            notes TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
    """)
    conn.close()
