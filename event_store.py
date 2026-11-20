"""Versioned SQLite history for newly committed simulation events."""

import hashlib
import json
from dataclasses import dataclass

SCHEMA_VERSION = 2


@dataclass(frozen=True)
class Event:
    type: str
    message: str
    actor_ids: tuple[str, ...]
    effects: dict
    site_id: str = "cell:0:0"
    cause_type: str | None = None


def migrate(db):
    """Add history tables without rewriting the snapshot or legacy chronicle."""
    version = db.execute("PRAGMA user_version").fetchone()[0]
    if version == SCHEMA_VERSION:
        return
    if version not in (0, 1):
        raise ValueError(f"Unsupported database schema version: {version}")
    with db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("""CREATE TABLE IF NOT EXISTS canonical_events (
            turn INTEGER NOT NULL,
            position INTEGER NOT NULL,
            event_id TEXT NOT NULL UNIQUE,
            type TEXT NOT NULL,
            record TEXT NOT NULL,
            PRIMARY KEY (turn, position)
        )""")
        db.execute("""CREATE INDEX IF NOT EXISTS canonical_event_type
            ON canonical_events (type, turn DESC, position DESC)""")
        db.execute("""CREATE TABLE IF NOT EXISTS event_outbox (
            event_id TEXT PRIMARY KEY REFERENCES canonical_events(event_id),
            publication_status TEXT NOT NULL DEFAULT 'pending',
            attempts INTEGER NOT NULL DEFAULT 0
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS artifacts (
            digest TEXT PRIMARY KEY, data BLOB NOT NULL
        )""")
        db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False)


def append_events(db, world_id, turn, events):
    """Append canonical records, their prose projection, and outbox rows."""
    row = db.execute("""SELECT event_id FROM canonical_events
        ORDER BY turn DESC, position DESC LIMIT 1""").fetchone()
    previous = row[0] if row else None
    start = db.execute("SELECT COALESCE(MAX(position) + 1, 0) FROM events WHERE turn = ?",
                       (turn,)).fetchone()[0]
    for position, event in enumerate(events, start):
        causes = []
        if event.cause_type:
            cause = db.execute("""SELECT event_id FROM canonical_events WHERE type = ?
                ORDER BY turn DESC, position DESC LIMIT 1""", (event.cause_type,)).fetchone()
            if cause:
                causes.append(cause[0])
        record = {
            "world_id": world_id,
            "turn": turn,
            "position": position,
            "type": event.type,
            "actor_ids": event.actor_ids,
            "site_id": event.site_id,
            "causes": causes,
            "effects": event.effects,
            "rules_version": 1,
            "previous_hash": previous,
        }
        digest = hashlib.sha256(b"glitch-event-v1\0" + encode(record).encode("utf-8"))
        event_id = f"sha256:{digest.hexdigest()}"
        record.update(event_id=event_id, event_hash=event_id)
        db.execute("INSERT INTO canonical_events VALUES (?, ?, ?, ?, ?)",
                   (turn, position, event_id, event.type, encode(record)))
        db.execute("INSERT INTO event_outbox (event_id) VALUES (?)", (event_id,))
        db.execute("INSERT INTO events VALUES (?, ?, ?)", (turn, position, event.message))
        previous = event_id


def canonical_history(db):
    if db.execute("PRAGMA user_version").fetchone()[0] == 0:
        return []
    return [
        json.loads(row[0]) for row in db.execute(
            "SELECT record FROM canonical_events ORDER BY turn, position"
        )
    ]
