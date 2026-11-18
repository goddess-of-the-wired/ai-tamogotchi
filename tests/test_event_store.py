import hashlib
import sqlite3
from contextlib import closing

from event_store import encode
import test_baseline


class EventStoreTests(test_baseline.SimulationFixture):
    def test_legacy_migration_preserves_snapshot_and_prose(self):
        self.found_town()
        self.command("step", "--days", "4")
        original = self.command("status", "--show-hidden").stdout
        prose = self.command("history").stdout
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute("DROP TABLE event_outbox")
            db.execute("DROP TABLE canonical_events")
            db.execute("PRAGMA user_version = 0")
        backup = self.db.with_name("legacy.sqlite3")
        self.command("backup", str(backup))
        self.assertEqual(self.view("history", "--json"), [])
        with closing(sqlite3.connect(self.db)) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone(), (0,))
        self.assertEqual(self.command("status", "--show-hidden").stdout, original)
        self.assertEqual(self.command("history").stdout, prose)
        self.command("step")
        self.assertEqual(self.view("history", "--json")[0]["turn"], 5)
        self.assertTrue(self.command("history").stdout.startswith(prose))
        with closing(sqlite3.connect(backup)) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone(), (0,))

    def test_order_hash_chain_causality_and_outbox(self):
        self.found_town()
        self.command("step", "--days", "24")
        records = self.view("history", "--json")
        self.assertEqual(records[0]["type"], "resident.born")
        seen = {}
        previous = None
        for event in records:
            event_id = event["event_id"]
            payload = {k: v for k, v in event.items() if k not in ("event_id", "event_hash")}
            digest = hashlib.sha256(b"glitch-event-v1\0" + encode(payload).encode("utf-8"))
            self.assertEqual(event_id, f"sha256:{digest.hexdigest()}")
            self.assertEqual(event["event_hash"], event_id)
            self.assertEqual(event["previous_hash"], previous)
            self.assertTrue(set(event["causes"]).issubset(seen))
            self.assertTrue(event["effects"])
            self.assertTrue(event["actor_ids"])
            seen[event_id] = event
            previous = event_id
        job = next(event for event in records if event["type"] == "resident.job_assigned")
        self.assertEqual(seen[job["causes"][0]]["type"], "workshop.reopened")
        self.assertEqual(job["effects"]["role"], ["resident", "apprentice"])
        site = next(event for event in records if event["type"] == "site.discovered")
        self.assertEqual(site["effects"]["site"][1], self.view("map")[site["site_id"]])
        with closing(sqlite3.connect(self.db)) as db:
            outbox = db.execute("SELECT event_id, publication_status, attempts FROM event_outbox")
            self.assertEqual(set(outbox), {(key, "pending", 0) for key in seen})

    def test_outbox_failure_rolls_back_snapshot_and_both_histories(self):
        self.found_town()
        status = self.command("status", "--show-hidden").stdout
        prose = self.command("history").stdout
        records = self.command("history", "--json").stdout
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute("""CREATE TRIGGER fail_outbox BEFORE INSERT ON event_outbox
                BEGIN SELECT RAISE(ABORT, 'test outbox failure'); END""")
        result = self.command("step", check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("test outbox failure", result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(self.command("status", "--show-hidden").stdout, status)
        self.assertEqual(self.command("history").stdout, prose)
        self.assertEqual(self.command("history", "--json").stdout, records)
        with closing(sqlite3.connect(self.db)) as db, db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM event_outbox").fetchone(), (1,))
            db.execute("DROP TRIGGER fail_outbox")
        self.command("step")
        self.assertEqual(self.view("status")["turn"], 1)
