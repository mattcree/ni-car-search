from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Generator

from .config import DB_PATH

SCHEMA_VERSION = 4


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def get_db() -> Generator[sqlite3.Connection, None, None]:
    """Context manager for DB access outside FastAPI routes."""
    conn = _connect()
    try:
        yield conn
    finally:
        conn.close()


def db_dependency() -> Generator[sqlite3.Connection, None, None]:
    """FastAPI ``Depends`` provider — yields a connection, closes on teardown."""
    conn = _connect()
    try:
        yield conn
    finally:
        conn.close()


def init_db() -> None:
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS watches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                make TEXT NOT NULL,
                model TEXT NOT NULL,
                location TEXT NOT NULL DEFAULT 'northern-ireland',
                radius INTEGER,
                min_price INTEGER,
                max_price INTEGER,
                min_year INTEGER,
                max_year INTEGER,
                poll_interval_minutes INTEGER NOT NULL DEFAULT 30,
                poll_start_time TEXT,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                last_polled_at TEXT
            );

            -- A vehicle is a physical car, identified by content fingerprint.
            -- Multiple listings (from different sites) can reference the same vehicle.
            -- Multiple vehicles CAN share a fingerprint (same spec, different car).
            -- Cross-site dedup is done by matching fingerprint + ensuring no
            -- duplicate source per vehicle.
            CREATE TABLE IF NOT EXISTS vehicles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                watch_id INTEGER NOT NULL REFERENCES watches(id) ON DELETE CASCADE,
                fingerprint TEXT NOT NULL,
                year INTEGER,
                mileage_bucket INTEGER,
                transmission TEXT,
                status TEXT NOT NULL DEFAULT 'active',
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                gone_at TEXT
            );

            -- Each listing is a single URL from a single source site.
            CREATE TABLE IF NOT EXISTS listings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                vehicle_id INTEGER NOT NULL REFERENCES vehicles(id) ON DELETE CASCADE,
                watch_id INTEGER NOT NULL REFERENCES watches(id) ON DELETE CASCADE,
                url TEXT NOT NULL,
                source TEXT NOT NULL,
                title TEXT NOT NULL,
                price INTEGER,
                year INTEGER,
                mileage TEXT,
                location TEXT,
                transmission TEXT,
                body_type TEXT,
                fuel_type TEXT,
                image_url TEXT,
                status TEXT NOT NULL DEFAULT 'active',
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                gone_at TEXT,
                UNIQUE(watch_id, url)
            );

            -- Events track changes at the vehicle level, linked to the run
            -- that caused them.
            CREATE TABLE IF NOT EXISTS vehicle_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                vehicle_id INTEGER NOT NULL REFERENCES vehicles(id) ON DELETE CASCADE,
                listing_id INTEGER REFERENCES listings(id) ON DELETE SET NULL,
                run_id INTEGER REFERENCES scrape_runs(id) ON DELETE SET NULL,
                event_type TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                price INTEGER,
                old_price INTEGER,
                source TEXT
            );

            CREATE TABLE IF NOT EXISTS scrape_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                watch_id INTEGER NOT NULL REFERENCES watches(id) ON DELETE CASCADE,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                total_found INTEGER DEFAULT 0,
                new_count INTEGER DEFAULT 0,
                new_source_count INTEGER DEFAULT 0,
                gone_count INTEGER DEFAULT 0,
                price_changed_count INTEGER DEFAULT 0,
                returned_count INTEGER DEFAULT 0,
                errors TEXT
            );

            -- Operational log: every event within a scrape run
            -- (scraper lifecycle, progress, persist phases).
            CREATE TABLE IF NOT EXISTS run_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL REFERENCES scrape_runs(id) ON DELETE CASCADE,
                event_type TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                source TEXT,
                count INTEGER,
                message TEXT
            );

            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_vehicles_watch_status
                ON vehicles(watch_id, status);
            CREATE INDEX IF NOT EXISTS idx_vehicles_watch_fp
                ON vehicles(watch_id, fingerprint);
            -- ── catalogue ────────────────────────────────────────────────

            CREATE TABLE IF NOT EXISTS catalogue_makes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                canonical_name TEXT NOT NULL UNIQUE,
                normalized TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS catalogue_models (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                make_id INTEGER NOT NULL REFERENCES catalogue_makes(id) ON DELETE CASCADE,
                canonical_name TEXT NOT NULL,
                normalized TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(make_id, normalized)
            );

            CREATE TABLE IF NOT EXISTS catalogue_source_aliases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                make_id INTEGER NOT NULL REFERENCES catalogue_makes(id) ON DELETE CASCADE,
                model_id INTEGER REFERENCES catalogue_models(id) ON DELETE CASCADE,
                source_make TEXT NOT NULL,
                source_model TEXT,
                source_make_id TEXT,
                source_model_id TEXT,
                UNIQUE(source, make_id, model_id)
            );

            CREATE TABLE IF NOT EXISTS catalogue_harvest_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                makes_found INTEGER DEFAULT 0,
                models_found INTEGER DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'running',
                error TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_cat_makes_norm
                ON catalogue_makes(normalized);
            CREATE INDEX IF NOT EXISTS idx_cat_models_make
                ON catalogue_models(make_id);
            CREATE INDEX IF NOT EXISTS idx_cat_aliases_source
                ON catalogue_source_aliases(source, make_id);

            CREATE INDEX IF NOT EXISTS idx_listings_watch_status
                ON listings(watch_id, status);
            CREATE INDEX IF NOT EXISTS idx_listings_vehicle
                ON listings(vehicle_id);
            CREATE INDEX IF NOT EXISTS idx_events_vehicle
                ON vehicle_events(vehicle_id);
            CREATE INDEX IF NOT EXISTS idx_events_run
                ON vehicle_events(run_id);
            CREATE INDEX IF NOT EXISTS idx_run_events_run
                ON run_events(run_id);
            CREATE INDEX IF NOT EXISTS idx_runs_watch
                ON scrape_runs(watch_id, started_at DESC);
            CREATE INDEX IF NOT EXISTS idx_events_timestamp
                ON vehicle_events(timestamp DESC);
        """)
        conn.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES ('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )

        # Migrations for existing databases
        existing_cols = {
            row[1] for row in conn.execute("PRAGMA table_info(watches)").fetchall()
        }
        if "poll_start_time" not in existing_cols:
            conn.execute("ALTER TABLE watches ADD COLUMN poll_start_time TEXT")

        conn.execute(
            "UPDATE settings SET value = ? WHERE key = 'schema_version'",
            (str(SCHEMA_VERSION),),
        )
        conn.commit()
