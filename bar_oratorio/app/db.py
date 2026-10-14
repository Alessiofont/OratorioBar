"""Database SQLite: schema, connessione e utilità di data/ora."""
from __future__ import annotations

import os
import secrets
import sqlite3
import threading
from contextlib import contextmanager
from datetime import date, datetime
from zoneinfo import ZoneInfo

DATA_DIR = os.environ.get("BAR_DATA_DIR", "/data")
DB_PATH = os.path.join(DATA_DIR, "bar.db")
PHOTO_DIR = os.path.join(DATA_DIR, "photos")
try:
    TZ = ZoneInfo(os.environ.get("TZ") or "Europe/Rome")
except Exception:  # fuso non valido o tzdata mancante
    TZ = ZoneInfo("Europe/Rome")

_write_lock = threading.RLock()

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY,
    username      TEXT NOT NULL UNIQUE COLLATE NOCASE,
    display_name  TEXT NOT NULL,
    role          TEXT NOT NULL CHECK (role IN ('admin','coordinatore','operatore')),
    secret_hash   TEXT NOT NULL,
    builtin       INTEGER NOT NULL DEFAULT 0,
    active        INTEGER NOT NULL DEFAULT 1,
    failed        INTEGER NOT NULL DEFAULT 0,
    locked_until  TEXT,
    last_login    TEXT,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id           INTEGER PRIMARY KEY,
    token_hash   TEXT NOT NULL UNIQUE,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    device_name  TEXT,
    device       TEXT,
    user_agent   TEXT,
    ip           TEXT,
    created_at   TEXT NOT NULL,
    expires_at   TEXT NOT NULL,
    last_seen    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_sessions_user ON sessions(user_id);

CREATE TABLE IF NOT EXISTS categories (
    id               INTEGER PRIMARY KEY,
    name             TEXT NOT NULL UNIQUE COLLATE NOCASE,
    color            TEXT,
    sort             INTEGER NOT NULL DEFAULT 0,
    shelf_life_days  INTEGER            -- durata tipica per stimare la scadenza; NULL = non scade
);

-- Anagrafica. Quantità sempre in pezzi interi.
CREATE TABLE IF NOT EXISTS products (
    id               INTEGER PRIMARY KEY,
    name             TEXT NOT NULL,
    category_id      INTEGER REFERENCES categories(id) ON DELETE SET NULL,
    min_stock        INTEGER NOT NULL DEFAULT 0,
    ideal_stock      INTEGER,
    shelf_life_days  INTEGER,
    tracks_expiry    INTEGER,
    stock            INTEGER NOT NULL DEFAULT 0,
    photo            TEXT,
    photo_source     TEXT,
    note             TEXT,
    sort             INTEGER NOT NULL DEFAULT 0,
    status           TEXT NOT NULL DEFAULT 'attivo' CHECK (status IN ('attivo','proposto','archiviato')),
    proposed_by      INTEGER REFERENCES users(id),
    proposed_at      TEXT,
    proposal_note    TEXT,
    created_at       TEXT NOT NULL,
    updated_at       TEXT
);

CREATE TABLE IF NOT EXISTS barcodes (
    code        TEXT PRIMARY KEY,
    product_id  INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    pending     INTEGER NOT NULL DEFAULT 0,   -- proposto da un operatore: non usabile finché non approvato
    added_by    INTEGER REFERENCES users(id),
    added_at    TEXT
);
CREATE INDEX IF NOT EXISTS ix_barcodes_prod ON barcodes(product_id);

-- Documento: un carico (carrello confermato), una vendita, uno scarico, una correzione, un inventario.
CREATE TABLE IF NOT EXISTS batches (
    id         TEXT PRIMARY KEY,
    ts         TEXT NOT NULL,
    type       TEXT NOT NULL CHECK (type IN ('carico','vendita','scarico','correzione','inventario')),
    user_id    INTEGER REFERENCES users(id),
    reason     TEXT,
    note       TEXT,
    cart_id    INTEGER,
    edited_at  TEXT
);
CREATE INDEX IF NOT EXISTS ix_batches_ts ON batches(ts);
CREATE INDEX IF NOT EXISTS ix_batches_user ON batches(user_id, ts);

-- Lotto = carrello di ingresso (prodotti misti). Nome = data, con progressivo se più nello stesso giorno.
CREATE TABLE IF NOT EXISTS carts (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    date        TEXT NOT NULL,
    kind        TEXT NOT NULL DEFAULT 'carico' CHECK (kind IN ('carico','correzione')),
    created_by  INTEGER REFERENCES users(id),
    note        TEXT,
    batch       TEXT REFERENCES batches(id),
    created_at  TEXT NOT NULL
);

-- Righe del lotto: un prodotto con la sua scadenza.
CREATE TABLE IF NOT EXISTS lots (
    id                INTEGER PRIMARY KEY,
    cart_id           INTEGER NOT NULL REFERENCES carts(id),
    product_id        INTEGER NOT NULL REFERENCES products(id),
    expiry            TEXT,
    expiry_estimated  INTEGER NOT NULL DEFAULT 0,
    qty_in            INTEGER NOT NULL,
    qty               INTEGER NOT NULL,
    created_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_lots_prod ON lots(product_id, qty);
CREATE INDEX IF NOT EXISTS ix_lots_cart ON lots(cart_id);

CREATE TABLE IF NOT EXISTS movements (
    id           INTEGER PRIMARY KEY,
    ts           TEXT NOT NULL,
    product_id   INTEGER NOT NULL REFERENCES products(id),
    type         TEXT NOT NULL CHECK (type IN ('carico','vendita','scarico','correzione','inventario')),
    qty          INTEGER NOT NULL,
    stock_after  INTEGER NOT NULL,
    reason       TEXT,
    user_id      INTEGER REFERENCES users(id),
    batch        TEXT NOT NULL REFERENCES batches(id)
);
CREATE INDEX IF NOT EXISTS ix_mov_ts ON movements(ts);
CREATE INDEX IF NOT EXISTS ix_mov_prod_ts ON movements(product_id, ts);
CREATE INDEX IF NOT EXISTS ix_mov_batch ON movements(batch);

CREATE TABLE IF NOT EXISTS movement_lots (
    movement_id  INTEGER NOT NULL REFERENCES movements(id) ON DELETE CASCADE,
    lot_id       INTEGER NOT NULL REFERENCES lots(id),
    qty          INTEGER NOT NULL,
    PRIMARY KEY (movement_id, lot_id)
);
CREATE INDEX IF NOT EXISTS ix_movlots_lot ON movement_lots(lot_id);

-- Carrello in bozza: uno per persona, salvato a ogni modifica.
CREATE TABLE IF NOT EXISTS drafts (
    user_id     INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    lines       TEXT NOT NULL DEFAULT '[]',   -- JSON [{product_id, qty, expiry}]
    note        TEXT,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS inventories (
    id          INTEGER PRIMARY KEY,
    started_at  TEXT NOT NULL,
    started_by  INTEGER REFERENCES users(id),
    closed_at   TEXT,
    closed_by   INTEGER REFERENCES users(id),
    status      TEXT NOT NULL CHECK (status IN ('aperto','chiuso','annullato')),
    note        TEXT,
    batch       TEXT
);

CREATE TABLE IF NOT EXISTS inventory_lines (
    inventory_id  INTEGER NOT NULL REFERENCES inventories(id) ON DELETE CASCADE,
    product_id    INTEGER NOT NULL REFERENCES products(id),
    counted       INTEGER,
    target        TEXT,              -- dove vanno i pezzi in più: id riga di lotto o 'nuovo'
    counted_by    INTEGER REFERENCES users(id),
    counted_at    TEXT,
    expected      INTEGER,
    PRIMARY KEY (inventory_id, product_id)
);

-- Lista della spesa: una sola aperta; quando l'operatore la chiude va in archivio.
CREATE TABLE IF NOT EXISTS shop_items (
    id          INTEGER PRIMARY KEY,
    product_id  INTEGER REFERENCES products(id) ON DELETE CASCADE,
    text        TEXT,
    qty_text    TEXT,
    checked     INTEGER NOT NULL DEFAULT 0,
    checked_by  INTEGER REFERENCES users(id),
    added_by    INTEGER REFERENCES users(id),
    added_at    TEXT NOT NULL,
    sort        INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS shop_lists (
    id         INTEGER PRIMARY KEY,
    closed_at  TEXT NOT NULL,
    closed_by  INTEGER REFERENCES users(id),
    items      TEXT NOT NULL      -- JSON [{label, qty, checked}]
);

-- Log dettagliato (solo admin): chi, quando, da dove, cosa, prima/dopo.
CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY,
    ts          TEXT NOT NULL,
    user_id     INTEGER,
    user_name   TEXT,
    device      TEXT,
    ip          TEXT,
    category    TEXT NOT NULL,
    action      TEXT NOT NULL,
    detail      TEXT,
    before      TEXT,
    after       TEXT
);
CREATE INDEX IF NOT EXISTS ix_audit_ts ON audit_log(ts);
CREATE INDEX IF NOT EXISTS ix_audit_user ON audit_log(user_id, ts);

CREATE TABLE IF NOT EXISTS settings (
    key    TEXT PRIMARY KEY,
    value  TEXT
);
"""

DEFAULT_SETTINGS = {
    "bar_name": "Bar Oratorio",
    "window_days": "56",            # uscite medie: ultime N giornate (multiplo di 7)
    "include_inventory_loss": "1",  # gli ammanchi di inventario/correzioni contano come consumo
    "edit_minutes": "30",           # entro quanti minuti si può modificare un proprio movimento
    "expiry_warning_days": "14",
    "ideal_period_days": "30",
    "photo_lookup": "1",
    "ntfy_server": "https://ntfy.sh",
    "ntfy_proposals": "1",
    "ntfy_list_closed": "1",
    "ntfy_digest": "1",
    "digest_weekday": "0",          # 0 = lunedì
    "digest_time": "15:00",
    "digest_last": "",
}

DEFAULT_CATEGORIES = [
    ("Bibite", "#F4C7C3", 10, 270),
    ("Acqua", "#CFE0F5", 20, 365),
    ("Succhi e tè", "#F5E3A8", 30, 270),
    ("Snack dolci", "#E2D6F7", 40, 150),
    ("Snack salati", "#F7E7B5", 50, 120),
    ("Gelati", "#C9EBF0", 60, 365),
    ("Caffetteria", "#E3D3C4", 70, 365),
    ("Freschi", "#F6D5C4", 80, 5),
    ("Materiale di consumo", "#DCE2DF", 90, None),
]


def now() -> datetime:
    return datetime.now(TZ).replace(tzinfo=None, microsecond=0)


def now_s() -> str:
    return now().isoformat(sep=" ")


def today() -> date:
    return now().date()


def connect() -> sqlite3.Connection:
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=15, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 15000")
    return conn


def init_db() -> None:
    conn = connect()
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA)
        for k, v in DEFAULT_SETTINGS.items():
            conn.execute("INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)", (k, v))
        # canali ntfy: nomi lunghi e casuali, creati una volta sola
        for key, prefix in (("ntfy_topic_coord", "bar-oratorio-coord-"), ("ntfy_topic_admin", "bar-oratorio-admin-")):
            conn.execute("INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)",
                         (key, prefix + secrets.token_hex(6)))
        if conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0] == 0:
            conn.executemany("INSERT INTO categories(name, color, sort, shelf_life_days) VALUES (?,?,?,?)",
                             DEFAULT_CATEGORIES)
        os.makedirs(PHOTO_DIR, exist_ok=True)
    finally:
        conn.close()


@contextmanager
def db():
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def tx():
    """Transazione serializzata per tutte le scritture che toccano le giacenze."""
    with _write_lock:
        conn = connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()


def get_settings(conn: sqlite3.Connection) -> dict:
    out = dict(DEFAULT_SETTINGS)
    out.update({r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM settings").fetchall()})
    return out


def set_setting(conn, key: str, value) -> None:
    conn.execute("INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                 (key, str(value)))


def rows(cur) -> list[dict]:
    return [dict(r) for r in cur.fetchall()]
