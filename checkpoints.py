"""Portable SQLite checkpoints that independent archives can verify and restore."""

import hashlib
import io
import json
import shutil
import sqlite3
import tarfile
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from event_store import encode
from storage_backup import backup_database

SCHEMA = "glitch.checkpoint.v1"


def file_digest(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inspect_snapshot(path):
    uri = path.resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as db:
        if db.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise ValueError("Checkpoint SQLite integrity check failed.")
        if db.execute("PRAGMA foreign_key_check").fetchone():
            raise ValueError("Checkpoint has invalid foreign keys.")
        version = db.execute("PRAGMA user_version").fetchone()[0]
        row = db.execute("SELECT state FROM world WHERE id = 1").fetchone()
        if row is None:
            raise ValueError("Checkpoint contains no initialized world.")
        world = json.loads(row[0])
        previous = None
        count = 0
        if version >= 1:
            for turn, position, identifier, kind, text in db.execute(
                "SELECT turn, position, event_id, type, record FROM canonical_events ORDER BY turn, position"
            ):
                record = json.loads(text)
                actual_id = record.pop("event_id")
                event_hash = record.pop("event_hash")
                payload = encode(record).encode("utf-8")
                expected = "sha256:" + hashlib.sha256(b"glitch-event-v1\0" + payload).hexdigest()
                if (identifier != expected or actual_id != expected or event_hash != expected
                        or record["previous_hash"] != previous or record["type"] != kind
                        or (record["turn"], record["position"]) != (turn, position)):
                    raise ValueError("Checkpoint event chain is invalid.")
                previous = expected
                count += 1
        if version >= 2:
            for digest, data in db.execute("SELECT digest, data FROM artifacts"):
                if hashlib.sha256(data).hexdigest() != digest:
                    raise ValueError("Checkpoint artifact checksum mismatch.")
    return {"schema": SCHEMA, "world_id": world["residents"][0]["birth"]["id"],
            "turn": world["turn"], "database_sha256": file_digest(path),
            "event_count": count, "event_head": previous}


def export_checkpoint(source, archive):
    with TemporaryDirectory(dir=archive.parent) as directory:
        root = Path(directory)
        snapshot = root / "world.sqlite3"
        backup_database(source, snapshot)
        metadata = inspect_snapshot(snapshot)
        bundle = root / "checkpoint.tar"
        with tarfile.open(bundle, "w") as container:
            container.add(snapshot, arcname="world.sqlite3")
            payload = encode(metadata).encode("utf-8")
            item = tarfile.TarInfo("manifest.json")
            item.size = len(payload)
            item.mode = 0o600
            container.addfile(item, io.BytesIO(payload))
        bundle.chmod(0o600)
        archive.hardlink_to(bundle)
    return metadata


def verify_checkpoint(archive, expected=None, restore=None):
    parent = restore.parent if restore else None
    with TemporaryDirectory(dir=parent) as directory:
        snapshot = Path(directory) / "world.sqlite3"
        with tarfile.open(archive, "r:") as container:
            database = container.next()
            if database is None or database.name != "world.sqlite3" or not database.isfile():
                raise ValueError("Malformed checkpoint database entry.")
            with container.extractfile(database) as original, snapshot.open("wb") as copy:
                shutil.copyfileobj(original, copy)
            metadata = container.next()
            if (metadata is None or metadata.name != "manifest.json"
                    or not metadata.isfile() or metadata.size > 8192):
                raise ValueError("Malformed checkpoint manifest entry.")
            with container.extractfile(metadata) as stream:
                manifest = json.load(stream)
            if container.next() is not None:
                raise ValueError("Unexpected extra checkpoint entry.")
        digest = file_digest(snapshot)
        if not isinstance(manifest, dict) or digest != manifest.get("database_sha256"):
            raise ValueError("Checkpoint database checksum mismatch.")
        if expected is not None and digest != expected:
            raise ValueError("Checkpoint does not match the trusted digest.")
        actual = inspect_snapshot(snapshot)
        if actual != manifest:
            raise ValueError("Checkpoint manifest does not match the restored database.")
        if restore:
            snapshot.chmod(0o600)
            restore.hardlink_to(snapshot)
        return actual
