"""Create a standalone SQLite snapshot using the online backup API."""

import sqlite3
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory


def backup_database(source: Path, destination: Path) -> None:
    """Publish a checked snapshot without overwriting an existing path."""
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f"Backup destination already exists: {destination}")

    # A sibling temporary file makes publication atomic and keeps partial
    # snapshots out of the requested destination, even on interruption.
    with TemporaryDirectory(prefix=".glitch-backup-", dir=destination.parent) as directory:
        snapshot = Path(directory) / "snapshot.sqlite3"
        with (
            closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as original,
            closing(sqlite3.connect(snapshot)) as copy,
        ):
            original.backup(copy)
            if copy.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                raise sqlite3.DatabaseError("Backup failed the SQLite integrity check.")
        snapshot.chmod(0o600)
        destination.hardlink_to(snapshot)
