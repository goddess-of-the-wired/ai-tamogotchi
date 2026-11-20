import json
import shutil
import subprocess
from copy import deepcopy
from pathlib import Path

import test_baseline
from economy import advance_economy
from residents import daily_decisions


class RoadmapTests(test_baseline.SimulationFixture):
    def test_external_witness_confirms_retained_birth_and_discovery(self):
        self.found_town()
        self.command("step", "--days", "6")
        receipt = self.view("proof", "publish", str(self.db.with_name("proofs")))
        root = receipt["root"]
        manifest = Path(receipt["manifest"])
        records = json.loads(manifest.read_text())["records"]
        birth = next(e["event_id"] for e in records if e["type"] == "resident.born")
        discovery = next(e["event_id"] for e in records if e["type"] == "site.discovered")
        self.assertEqual(self.view("proof", "status", root)["status"], "published")
        witness_dir = self.db.with_name("witness")
        witness_dir.mkdir()
        copy = witness_dir / "proof.json"
        shutil.copyfile(manifest, copy)
        missing_db = witness_dir / "unused.sqlite3"
        payload = self.command("proof", "witness-payload", str(copy), "--root", root,
                               "--event", birth, "--event", discovery, database=missing_db).stdout
        self.assertFalse(missing_db.exists())
        message = witness_dir / "payload"
        message.write_text(payload)
        private, public, signature = (witness_dir / name for name in ("key.pem", "key.pub", "proof.sig"))
        for command in (
            ["openssl", "genpkey", "-algorithm", "ED25519", "-out", private],
            ["openssl", "pkey", "-in", private, "-pubout", "-out", public],
            ["openssl", "pkeyutl", "-sign", "-inkey", private, "-rawin", "-in", message, "-out", signature],
        ):
            subprocess.run(command, check=True, capture_output=True)
        confirm = ("proof", "confirm", root, str(signature), "--public-key", str(public))
        self.assertEqual(self.view(*confirm), self.view(*confirm))
        self.assertEqual(self.view("proof", "status", root),
                         {"root": root, "status": "confirmed", "witnesses": 1})
        signature.write_bytes(bytes(64))
        self.assertNotEqual(self.command(*confirm, check=False).returncode, 0)

    def test_residents_trade_and_council_lifecycle_preserves_stock(self):
        def resident(name, inventory, peer):
            return {"birth": {"id": name}, "current_site_ip": "10.0.0.1",
                    "inventory": inventory, "relationships": {peer: 2},
                    "goal": "work", "savings": 5}

        a, b = resident("a", {}, "b"), resident("b", {"clay": 3}, "a")
        site = a["current_site_ip"]
        world = {"residents": [a, b], "turn": 1, "map": {site: {"terrain": "forest"},
                 "10.0.0.2": {"terrain": "forest"}, "10.0.0.3": {"terrain": "forest"}}}
        events = list(advance_economy(world))
        self.assertIn("economy.trade", [event.type for event in events])
        self.assertEqual((a["savings"], b["savings"]), (4, 6))
        world["turn"] = 2
        self.assertIn("institution.formed", [e.type for e in advance_economy(world)])
        self.assertEqual(world["economy"]["settlements"][site]["treasury"], {"fiber": 2})
        a["current_site_ip"], b["current_site_ip"] = "10.0.0.2", "10.0.0.3"
        world["turn"] = 5
        self.assertIn("institution.dissolved", [e.type for e in advance_economy(world)])
        self.assertEqual(world["economy"]["settlements"][site]["treasury"], {"fiber": 2})

    def test_three_resident_evidence_lineage_preserves_distinct_belief(self):
        self.command("init", "--name", "Alex", "--seed", "acceptance-15",
                     "--priorities", "loyalty", "generosity", "curiosity")
        self.command("step", "--days", "43")
        site = "172.16.0.51"
        state = self.view("status")
        subject, entry = next((key, e) for key, e in state["scholarship"].items() if e["site"] == site)
        self.assertEqual(len(set(entry["lineage"])), 3)
        self.assertEqual(entry["facts"]["terrain"], self.view("observer", "map")[site]["terrain"])
        self.assertEqual(entry["interpretation"], "misread")
        author = next(r for r in state["residents"] if r["birth"]["id"] == entry["lineage"][1])
        self.assertEqual(author["beliefs"][subject]["assessment"], "misread")

    def test_replica_verifies_checkpoint_and_restores_a_new_world_database(self):
        self.found_town()
        self.command("step", "--days", "32")
        before = self.view("status")
        before_history = self.view("history", "--json")
        archive = self.db.with_name("checkpoint.tar")
        receipt = self.view("checkpoint", "export", str(archive))
        replica_dir = self.db.with_name("replica")
        replica_dir.mkdir()
        replica = replica_dir / "copy.tar"
        shutil.copyfile(archive, replica)
        self.command("step", "--days", "3")
        options = ("--digest", receipt["database_sha256"])
        restored = replica_dir / "recovered.sqlite3"
        self.assertEqual(self.view("checkpoint", "verify", str(replica), *options), receipt)
        self.assertEqual(self.view("checkpoint", "restore", str(replica), str(restored), *options), receipt)
        self.assertEqual(self.view("status", database=restored), before)
        self.assertEqual(self.view("history", "--json", database=restored), before_history)
        with replica.open("r+b") as stream:
            stream.seek(2048)
            current = stream.read(1)
            stream.seek(2048)
            stream.write(bytes([current[0] ^ 1]))
        self.assertNotEqual(self.command("checkpoint", "verify", str(replica), *options,
                                         check=False).returncode, 0)

    def test_reduced_scheduling_preserves_resident_state_and_actions(self):
        state = {"turn": 0, "residents": [
            {"birth": {"id": str(n), "name": str(n)}, "current_site_ip": site,
             "energy": n % 5, "goal": None, "memories": [], "relationships": {}}
            for n, site in enumerate(("10.0.0.4", "172.16.0.3", "172.16.0.3", "192.168.0.5"))
        ]}
        reference = deepcopy(state)
        for turn in range(1, 15):
            state["turn"] = reference["turn"] = turn
            self.assertEqual(list(daily_decisions(state, "127.0.0.1")),
                             list(daily_decisions(reference, "127.0.0.1", reduced=False)))
        self.assertEqual(state, reference)
        metrics = self.view("benchmark", "--population", "20", "--days", "4")
        self.assertEqual(metrics["inference_calls"], 0)
        self.assertGreater(metrics["database_bytes"], metrics["snapshot_bytes"])
        self.assertGreater(metrics["events"], 4)
