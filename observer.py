"""Creator-signed birth requests and verified content-addressed artifacts."""

import base64
import hashlib
import json
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID, uuid4

from event_store import encode

PUBLIC_EVENTS = {"resident.born", "journey.started", "journey.moved",
                 "site.discovered", "site.seen", "site.studied",
                 "research.started", "research.resumed", "research.experiment",
                 "research.notebook"}


def public_key_der(path):
    result = subprocess.run(["openssl", "pkey", "-pubin", "-in", str(path), "-outform", "DER"],
                            capture_output=True, timeout=5)
    key = result.stdout
    if result.returncode or len(key) != 44 or not key.startswith(bytes.fromhex("302a300506032b6570032100")):
        raise ValueError("An Ed25519 public key is required.")
    return base64.b64encode(key).decode("ascii")


def birth_request(world_id, site, name, priorities, key):
    return {"schema": "glitch.birth.v1", "world_id": world_id, "site": site,
            "resident_id": str(uuid4()), "name": name, "priorities": priorities, "key": key}


def verify_birth(request, signature, priorities):
    fields = {"schema", "world_id", "site", "resident_id", "name", "priorities", "key"}
    if not isinstance(request, dict) or request.keys() != fields:
        raise ValueError("Invalid birth certificate fields.")
    name = request["name"]
    choices = request["priorities"]
    if (request["schema"] != "glitch.birth.v1" or not isinstance(name, str)
            or not 0 < len(name) <= 80 or name != name.strip() or not name.isprintable()
            or not isinstance(choices, list) or len(choices) != 3
            or any(not isinstance(choice, str) or choice not in priorities for choice in choices)
            or len(set(choices)) != 3):
        raise ValueError("Invalid birth certificate values.")
    resident_id = request["resident_id"]
    if not isinstance(resident_id, str) or str(UUID(resident_id)) != resident_id:
        raise ValueError("Invalid resident ID.")
    if not isinstance(request["key"], str):
        raise ValueError("Invalid public key encoding.")
    key = base64.b64decode(request["key"], validate=True)
    if (len(signature) != 64 or len(key) != 44
            or not key.startswith(bytes.fromhex("302a300506032b6570032100"))
            or base64.b64encode(key).decode("ascii") != request["key"]):
        raise ValueError("Invalid Ed25519 key or signature.")
    with TemporaryDirectory() as root:
        root = Path(root)
        (root / "request").write_bytes(encode(request).encode("utf-8"))
        (root / "key").write_bytes(key)
        (root / "signature").write_bytes(signature)
        result = subprocess.run(
            ["openssl", "pkeyutl", "-verify", "-pubin", "-keyform", "DER",
             "-inkey", str(root / "key"), "-rawin", "-in", str(root / "request"),
             "-sigfile", str(root / "signature")], capture_output=True, timeout=5)
    if result.returncode:
        raise ValueError("Creator signature verification failed.")
    return {"request": request, "signature": base64.b64encode(signature).decode("ascii")}


def store_notebooks(db, world):
    for notebook in world.get("research", {}).get("notebooks", {}).values():
        if "artifact" in notebook:
            continue
        data = encode(notebook).encode("utf-8")
        digest = hashlib.sha256(data).hexdigest()
        db.execute("INSERT OR IGNORE INTO artifacts VALUES (?, ?)", (digest, data))
        notebook["artifact"] = digest


def read_artifact(db, digest):
    if not db.execute("PRAGMA user_version").fetchone()[0] >= 2:
        raise ValueError("Unknown artifact.")
    row = db.execute("SELECT data FROM artifacts WHERE digest = ?", (digest,)).fetchone()
    if row is None:
        raise ValueError("Unknown artifact.")
    if hashlib.sha256(row[0]).hexdigest() != digest:
        raise ValueError("Artifact checksum mismatch.")
    return row[0]


def observer_status(world):
    return {"name": world["name"], "turn": world["turn"],
            "residents": [
                {"id": r["birth"]["id"], "name": r["birth"]["name"],
                 "site": r.get("current_site_ip"), "role": r["role"],
                 "certificate": r["birth"].get("certificate")}
                for r in world["residents"]
            ]}


def load_request(path):
    with path.open("rb") as stream:
        data = stream.read(4097)
    if len(data) > 4096:
        raise ValueError("Birth request is too large.")
    return json.loads(data)
