"""Portable Merkle manifests and retryable dry-run publication."""

import hashlib
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory

from event_store import encode
from observer import PUBLIC_EVENTS, verify_signature

BATCH_LIMIT = 64
SCHEMA = "glitch.public-proof.v1"


def merkle_root(records):
    if type(records) is not list or not 0 < len(records) <= BATCH_LIMIT:
        raise ValueError("Invalid number of records.")
    leaves = []
    previous = None
    world_id = None
    for record in records:
        if not isinstance(record, dict) or record.get("type") not in PUBLIC_EVENTS:
            raise ValueError("Manifest includes a nonpublic event.")
        payload = {k: v for k, v in record.items() if k not in {"event_id", "event_hash"}}
        expected = "sha256:" + hashlib.sha256(
            b"glitch-event-v1\0" + encode(payload).encode("utf-8")
        ).hexdigest()
        if record.get("event_id") != expected or record.get("event_hash") != expected:
            raise ValueError("Event hash mismatch.")
        order = (record.get("turn"), record.get("position"))
        if any(type(value) is not int or value < 0 for value in order):
            raise ValueError("Invalid event position.")
        if previous is not None and (order <= previous or record.get("world_id") != world_id):
            raise ValueError("Manifest records must be ordered within one world.")
        world_id, previous = record.get("world_id"), order
        leaves.append(hashlib.sha256(b"\x00" + encode(record).encode("utf-8")).digest())
    while len(leaves) > 1:
        if len(leaves) % 2:
            leaves.append(leaves[-1])
        leaves = [hashlib.sha256(b"\x01" + left + right).digest()
                  for left, right in zip(leaves[::2], leaves[1::2])]
    return hashlib.sha256(b"\x02" + len(records).to_bytes(4, "big") + leaves[0]).hexdigest()


def verify_manifest(manifest, expected_root, required_events=()):
    if not isinstance(manifest, dict) or manifest.get("schema") != SCHEMA:
        raise ValueError("Unknown proof format.")
    root = merkle_root(manifest.get("records"))
    if root != expected_root or manifest.get("root") != root:
        raise ValueError("Manifest root does not match the trusted root.")
    if not set(required_events) <= {record["event_id"] for record in manifest["records"]}:
        raise ValueError("A required event is absent from the batch.")
    return {"root": root, "records": len(manifest["records"])}


def prepare_batch(db):
    db.execute("BEGIN IMMEDIATE")
    with db:
        row = db.execute("SELECT manifest FROM proof_batches WHERE status = 'pending'").fetchone()
        if row:
            manifest = json.loads(row[0])
        else:
            placeholders = ",".join("?" for _ in PUBLIC_EVENTS)
            rows = db.execute(f"""SELECT e.record FROM canonical_events e
                JOIN event_outbox o USING (event_id)
                WHERE o.publication_status = 'pending' AND e.type IN ({placeholders})
                ORDER BY e.turn, e.position LIMIT ?""", (*sorted(PUBLIC_EVENTS), BATCH_LIMIT))
            records = [json.loads(row[0]) for row in rows]
            if not records:
                row = db.execute("SELECT manifest FROM proof_batches ORDER BY rowid DESC LIMIT 1").fetchone()
                if not row:
                    raise ValueError("No public events to publish.")
                return json.loads(row[0])
            root = merkle_root(records)
            manifest = {"schema": SCHEMA, "root": root, "records": records}
            db.execute("INSERT INTO proof_batches VALUES (?, ?, 'pending')", (root, encode(manifest)))
        db.executemany("UPDATE event_outbox SET attempts = attempts + 1 WHERE event_id = ?",
                       [(record["event_id"],) for record in manifest["records"]])
        return manifest


def publish_local(directory, manifest):
    """Dry-run adapter with atomic, content-addressed writes."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / (manifest["root"] + ".json")
    data = encode(manifest).encode("utf-8")
    with TemporaryDirectory(dir=directory) as temporary:
        pending = Path(temporary) / "manifest.json"
        pending.write_bytes(data)
        try:
            os.link(pending, destination)
        except FileExistsError:
            if destination.read_bytes() != data:
                raise ValueError("Existing publication has different bytes.")
    return destination


def publish_batch(db, directory, publisher=publish_local):
    manifest = prepare_batch(db)
    root = manifest["root"]
    verify_manifest(manifest, root)
    destination = publisher(directory, manifest)
    db.execute("BEGIN IMMEDIATE")
    with db:
        db.execute("UPDATE proof_batches SET status = 'published' WHERE root = ?", (root,))
        db.executemany("UPDATE event_outbox SET publication_status = 'published' WHERE event_id = ?",
                       [(record["event_id"],) for record in manifest["records"]])
    return {"root": root, "manifest": str(destination), "records": len(manifest["records"])}


def witness_payload(manifest, trusted_root, required_events=()):
    result = verify_manifest(manifest, trusted_root, required_events)
    return {"schema": "glitch.proof-witness.v1", **result,
            "world_id": manifest["records"][0]["world_id"]}


def confirm_batch(db, root, signature, witness):
    saved = db.execute("SELECT manifest FROM proof_batches WHERE root = ? AND status = 'published'",
                       (root,)).fetchone()
    if not saved:
        raise ValueError("Publish this batch before confirming it.")
    payload = witness_payload(json.loads(saved[0]), root)
    verify_signature(payload, signature, witness)
    with db:
        db.execute("INSERT OR IGNORE INTO proof_confirmations VALUES (?, ?, ?)",
                   (root, witness, signature))
    return confirmation_status(db, root)


def confirmation_status(db, root):
    row = db.execute("SELECT status FROM proof_batches WHERE root = ?", (root,)).fetchone()
    if not row:
        raise ValueError("Unknown batch root.")
    count, = db.execute("SELECT COUNT(*) FROM proof_confirmations WHERE root = ?",
                        (root,)).fetchone()
    return {"root": root, "status": "confirmed" if count else row[0], "witnesses": count}
