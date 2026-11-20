import json
import sqlite3
from contextlib import closing

import test_baseline


class PublicProofTests(test_baseline.SimulationFixture):
    def test_independent_verification_and_retry_after_failed_publication(self):
        self.found_town()
        self.command("step", "--days", "6")
        output = self.db.with_name("published")
        output.write_text("block the dry-run adapter")
        self.assertNotEqual(self.command("proof", "publish", str(output), check=False).returncode, 0)
        with closing(sqlite3.connect(self.db)) as db:
            root, saved = db.execute(
                "SELECT root, manifest FROM proof_batches WHERE status = 'pending'"
            ).fetchone()
        self.command("step", "--days", "2")
        output.unlink()
        receipt = self.view("proof", "publish", str(output))
        self.assertEqual(receipt["root"], root)
        manifest = output / f"{root}.json"
        self.assertEqual(manifest.read_text(), saved)
        records = json.loads(saved)["records"]
        birth = next(e["event_id"] for e in records if e["type"] == "resident.born")
        discovery = next(e["event_id"] for e in records if e["type"] == "site.discovered")
        missing_db = self.db.with_name("absent.sqlite3")
        args = ("proof", "verify", str(manifest), "--root", root,
                "--event", birth, "--event", discovery)
        result = self.view(*args, database=missing_db)
        self.assertEqual(result["root"], root)
        self.assertFalse(missing_db.exists())
        with closing(sqlite3.connect(self.db)) as db:
            self.assertEqual(db.execute(
                "SELECT publication_status, attempts FROM event_outbox WHERE event_id = ?",
                (discovery,)).fetchone(), ("published", 2))
        later = self.view("proof", "publish", str(output))
        self.assertEqual(self.view("proof", "publish", str(output)), later)
        self.assertEqual(len(list(output.glob("*.json"))), 2)
        altered = json.loads(saved)
        altered["records"][0]["site_id"] = "10.0.0.1"
        manifest.write_text(json.dumps(altered))
        self.assertIn("Event hash mismatch", self.command(*args, check=False).stderr)

    def test_retry_after_output_written_and_database_acknowledgment_fails(self):
        self.found_town()
        output = self.db.with_name("published")
        self.view("proof", "publish", str(output))
        self.command("step", "--days", "6")
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute("""CREATE TRIGGER block_proof_ack BEFORE UPDATE ON proof_batches
                BEGIN SELECT RAISE(ABORT, 'interrupted acknowledgment'); END""")
        self.assertNotEqual(self.command("proof", "publish", str(output), check=False).returncode, 0)
        files = {path.name: path.read_bytes() for path in output.glob("*.json")}
        self.assertEqual(len(files), 2)
        with closing(sqlite3.connect(self.db)) as db, db:
            root, = db.execute("SELECT root FROM proof_batches WHERE status = 'pending'").fetchone()
            db.execute("DROP TRIGGER block_proof_ack")
        receipt = self.view("proof", "publish", str(output))
        self.assertEqual(receipt["root"], root)
        self.assertEqual({path.name: path.read_bytes() for path in output.glob("*.json")}, files)
