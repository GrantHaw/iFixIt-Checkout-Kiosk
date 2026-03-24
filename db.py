import sqlite3
import os
from datetime import datetime

DB_PATH = os.environ.get("DB_PATH", "/data/kiosk.db")

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def init_db():
    """set up tables if they dont exist"""
    conn = get_db()
    c = conn.cursor()

    c.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            nfc_uid TEXT UNIQUE,
            slack_id TEXT,
            is_senior INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS kits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            status TEXT DEFAULT 'available',
            checked_out_by INTEGER REFERENCES users(id),
            checked_out_at TEXT,
            notes TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS bits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kit_id INTEGER NOT NULL REFERENCES kits(id) ON DELETE CASCADE,
            bit_name TEXT NOT NULL,
            position TEXT NOT NULL,
            present INTEGER DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS checkouts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kit_id INTEGER NOT NULL REFERENCES kits(id),
            user_id INTEGER NOT NULL REFERENCES users(id),
            checked_out_at TEXT DEFAULT (datetime('now')),
            returned_at TEXT,
            missing_before INTEGER DEFAULT 0,
            missing_after INTEGER
        );

        CREATE TABLE IF NOT EXISTS config (
            key TEXT PRIMARY KEY,
            value TEXT
        );
    """)

    # add is_senior column if it doesnt exist (migration for existing dbs)
    try:
        c.execute("ALTER TABLE users ADD COLUMN is_senior INTEGER DEFAULT 0")
    except:
        pass  # column already exists

    # default config if empty
    existing = c.execute("SELECT COUNT(*) FROM config").fetchone()[0]
    if existing == 0:
        c.executemany("INSERT INTO config (key, value) VALUES (?, ?)", [
            ("slack_webhook_url", ""),
            ("slack_channel", "#ifixit-kits"),
            ("slack_enabled", "false"),
        ])

    conn.commit()
    conn.close()

def get_config(key):
    conn = get_db()
    row = conn.execute("SELECT value FROM config WHERE key = ?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else None

def set_config(key, value):
    conn = get_db()
    conn.execute("INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)", (key, value))
    conn.commit()
    conn.close()


# --- user stuff ---

def get_user_by_nfc(nfc_uid):
    conn = get_db()
    row = conn.execute("SELECT * FROM users WHERE nfc_uid = ?", (nfc_uid,)).fetchone()
    conn.close()
    return dict(row) if row else None

def get_user(user_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    return dict(row) if row else None

def list_users():
    conn = get_db()
    rows = conn.execute("SELECT * FROM users ORDER BY name").fetchall()
    conn.close()
    return [dict(r) for r in rows]

def create_user(name, nfc_uid=None, slack_id=None, is_senior=False):
    conn = get_db()
    c = conn.execute(
        "INSERT INTO users (name, nfc_uid, slack_id, is_senior) VALUES (?, ?, ?, ?)",
        (name, nfc_uid, slack_id, 1 if is_senior else 0)
    )
    conn.commit()
    uid = c.lastrowid
    conn.close()
    return uid

def update_user(user_id, name, nfc_uid=None, slack_id=None, is_senior=False):
    conn = get_db()
    conn.execute(
        "UPDATE users SET name=?, nfc_uid=?, slack_id=?, is_senior=? WHERE id=?",
        (name, nfc_uid, slack_id, 1 if is_senior else 0, user_id)
    )
    conn.commit()
    conn.close()

def toggle_senior(user_id):
    conn = get_db()
    row = conn.execute("SELECT is_senior FROM users WHERE id = ?", (user_id,)).fetchone()
    if row:
        new_val = 0 if row["is_senior"] else 1
        conn.execute("UPDATE users SET is_senior = ? WHERE id = ?", (new_val, user_id))
        conn.commit()
    conn.close()

def delete_user(user_id):
    conn = get_db()
    conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()


# --- kit stuff ---

def list_kits():
    conn = get_db()
    rows = conn.execute("""
        SELECT k.*, u.name as checked_out_by_name,
        (SELECT COUNT(*) FROM bits WHERE kit_id = k.id AND present = 0) as missing_count,
        (SELECT COUNT(*) FROM bits WHERE kit_id = k.id) as total_bits
        FROM kits k
        LEFT JOIN users u ON k.checked_out_by = u.id
        ORDER BY k.name
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_kit(kit_id):
    conn = get_db()
    row = conn.execute("""
        SELECT k.*, u.name as checked_out_by_name
        FROM kits k LEFT JOIN users u ON k.checked_out_by = u.id
        WHERE k.id = ?
    """, (kit_id,)).fetchone()
    conn.close()
    return dict(row) if row else None

def get_kit_bits(kit_id):
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM bits WHERE kit_id = ? ORDER BY id", (kit_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def create_kit(name, bit_definitions):
    conn = get_db()
    c = conn.execute("INSERT INTO kits (name) VALUES (?)", (name,))
    kit_id = c.lastrowid
    for bname, pos in bit_definitions:
        conn.execute(
            "INSERT INTO bits (kit_id, bit_name, position, present) VALUES (?, ?, ?, 1)",
            (kit_id, bname, pos)
        )
    conn.commit()
    conn.close()
    return kit_id

def delete_kit(kit_id):
    conn = get_db()
    conn.execute("DELETE FROM kits WHERE id = ?", (kit_id,))
    conn.commit()
    conn.close()


# --- checkout / return ---

def checkout_kit(kit_id, user_id):
    conn = get_db()
    missing = conn.execute(
        "SELECT COUNT(*) FROM bits WHERE kit_id = ? AND present = 0", (kit_id,)
    ).fetchone()[0]

    conn.execute(
        "UPDATE kits SET status='checked_out', checked_out_by=?, checked_out_at=datetime('now') WHERE id=?",
        (user_id, kit_id)
    )
    conn.execute(
        "INSERT INTO checkouts (kit_id, user_id, missing_before) VALUES (?, ?, ?)",
        (kit_id, user_id, missing)
    )
    conn.commit()
    conn.close()

def return_kit(kit_id, user_id, missing_bit_ids):
    conn = get_db()

    old_missing = conn.execute(
        "SELECT COUNT(*) FROM bits WHERE kit_id = ? AND present = 0", (kit_id,)
    ).fetchone()[0]

    conn.execute("UPDATE bits SET present = 1 WHERE kit_id = ?", (kit_id,))
    if missing_bit_ids:
        placeholders = ",".join(["?"] * len(missing_bit_ids))
        conn.execute(
            f"UPDATE bits SET present = 0 WHERE kit_id = ? AND position IN ({placeholders})",
            [kit_id] + list(missing_bit_ids)
        )

    new_missing = len(missing_bit_ids) if missing_bit_ids else 0

    conn.execute(
        "UPDATE kits SET status='available', checked_out_by=NULL, checked_out_at=NULL WHERE id=?",
        (kit_id,)
    )

    conn.execute(
        """UPDATE checkouts SET returned_at=datetime('now'), missing_after=?
           WHERE kit_id=? AND user_id=? AND returned_at IS NULL""",
        (new_missing, kit_id, user_id)
    )

    conn.commit()

    increased = new_missing > old_missing

    last_borrowers = conn.execute(
        """SELECT DISTINCT u.name, u.slack_id FROM checkouts c
           JOIN users u ON c.user_id = u.id
           WHERE c.kit_id = ? AND c.user_id != ? AND c.returned_at IS NOT NULL
           ORDER BY c.returned_at DESC LIMIT 3""",
        (kit_id, user_id)
    ).fetchall()

    conn.close()

    return {
        "old_missing": old_missing,
        "new_missing": new_missing,
        "increased": increased,
        "last_borrowers": [dict(r) for r in last_borrowers],
    }


def get_checkout_history(limit=50):
    conn = get_db()
    rows = conn.execute("""
        SELECT c.*, u.name as user_name, k.name as kit_name
        FROM checkouts c
        JOIN users u ON c.user_id = u.id
        JOIN kits k ON c.kit_id = k.id
        ORDER BY c.checked_out_at DESC
        LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


MAKO_BITS = [
    ("nut2.5", "nut2.5"), ("nut3", "nut3"), ("nut3.5", "nut3.5"),
    ("nut4", "nut4"), ("nut5", "nut5"), ("nut5.5", "nut5.5"),
    ("mag", "mag"), ("adapter", "adapter"),
    ("h0.7", "h0.7"), ("h0.9", "h0.9"), ("h1.3", "h1.3"),
    ("h1.5", "h1.5"), ("h2", "h2"), ("h2.5", "h2.5"), ("h3", "h3"),
    ("h3.5", "h3.5"), ("h4", "h4"), ("h4.5", "h4.5"),
    ("h5", "h5"), ("oval", "oval"), ("sp6", "sp6"), ("sp8", "sp8"),
    ("standoff", "standoff"), ("sq1", "sq1"), ("sq2", "sq2"),
    ("j000", "j000"), ("j00", "j00"), ("j0", "j0"), ("j1", "j1"),
    ("t2", "t2"), ("t3", "t3"), ("t4", "t4"), ("t5", "t5"),
    ("tr6", "tr6"), ("tr7", "tr7"), ("tr8", "tr8"),
    ("tr9", "tr9"), ("tr10", "tr10"), ("tr15", "tr15"),
    ("tr20", "tr20"), ("tr25", "tr25"), ("gb3.8", "gb3.8"), ("gb4.5", "gb4.5"),
    ("sim", "sim"), ("sl1", "sl1"), ("sl1.5", "sl1.5"),
    ("sl2", "sl2"), ("sl2.5", "sl2.5"), ("sl3", "sl3"), ("sl4", "sl4"),
    ("y000", "y000"), ("y00", "y00"), ("y0", "y0"), ("y1", "y1"),
    ("p2", "p2"), ("p5", "p5"), ("p6", "p6"),
    ("ph000", "ph000"), ("ph00", "ph00"), ("ph0", "ph0"),
    ("ph1", "ph1"), ("ph2", "ph2"), ("tri2", "tri2"), ("tri3", "tri3"),
    ("flex", "flex"), ("driver", "driver"),
]
