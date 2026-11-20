import hashlib
import json
import sqlite3
import subprocess
from contextlib import closing

import test_baseline


class ObserverTests(test_baseline.SimulationFixture):
    def signed_request(self, name):
        root = self.db.parent
        private = root / "creator.pem"
        public = root / "creator.pub"
        request = root / f"{name}.json"
        signature = root / f"{name}.sig"
        if not private.exists():
            subprocess.run(["openssl", "genpkey", "-algorithm", "ED25519", "-out", private],
                           check=True, capture_output=True)
            subprocess.run(["openssl", "pkey", "-in", private, "-pubout", "-out", public],
                           check=True, capture_output=True)
        request.write_text(self.command("birth-request", "--public-key", str(public),
                                        "--name", name, "--priorities", "loyalty",
                                        "curiosity", "stability").stdout)
        subprocess.run(["openssl", "pkeyutl", "-sign", "-inkey", private, "-rawin",
                        "-in", request, "-out", signature], check=True, capture_output=True)
        return request, signature

    def test_creator_birth_is_verified_private_and_rate_limited(self):
        self.found_town()
        req, sig = self.signed_request("Robin")
        actor = self.command("birth-admit", str(req), str(sig)).stdout.strip()
        roster = self.view("observer", "status")["residents"]
        robin = next(r for r in roster if r["id"] == actor)
        self.assertEqual(robin["certificate"]["request"], json.loads(req.read_text()))
        self.assertNotIn("seed", json.dumps(robin))
        self.assertNotIn("traits", robin)
        self.assertEqual(self.view("observer", "timeline", "--resident", actor)[0]["site_id"],
                         "127.0.0.1")
        self.assertIn("already admitted", self.command(
            "birth-admit", str(req), str(sig), check=False).stderr)
        req2, sig2 = self.signed_request("Second")
        self.assertIn("admission limit", self.command(
            "birth-admit", str(req2), str(sig2), check=False).stderr)
        req2.write_text(req2.read_text().replace("Second", "Forged"))
        self.assertIn("signature verification failed", self.command(
            "birth-admit", str(req2), str(sig2), check=False).stderr)
        self.assertNotEqual(self.command("observer", "step", check=False).returncode, 0)

    def test_notebook_artifact_survives_reopen_and_rejects_corruption(self):
        self.found_town()
        self.command("step", "--days", "32")
        event = next(e for e in self.view("observer", "timeline")
                     if e["type"] == "research.notebook")
        notebook = event["effects"]["notebook"]
        digest = notebook["artifact"]
        data = self.command("observer", "artifact", digest).stdout
        self.assertEqual(hashlib.sha256(data.encode()).hexdigest(), digest)
        self.assertEqual(json.loads(data)["id"], notebook["id"])
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute("UPDATE artifacts SET data = ? WHERE digest = ?", (b"corrupt", digest))
        self.assertIn("checksum mismatch", self.command(
            "observer", "artifact", digest, check=False).stderr)
