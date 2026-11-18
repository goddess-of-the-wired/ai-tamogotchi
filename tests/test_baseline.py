import io
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import glitch
from exploration import discover

CLI = Path(glitch.__file__)


class SimulationFixture(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.db = Path(directory.name) / "source.sqlite3"

    def command(self, *args, database=None, check=True):
        return subprocess.run(
            [sys.executable, str(CLI), "--db", str(database or self.db), *args],
            capture_output=True, text=True, check=check,
        )

    def found_town(self):
        self.command("init", "--name", "Baseline", "--seed", "test",
                     "--priorities", "loyalty", "generosity", "curiosity")

    def view(self, *args, database=None):
        return json.loads(self.command(*args, database=database).stdout)


class BaselineTests(SimulationFixture):
    def test_birth_draft_can_reopen_and_preserves_choices(self):
        args = ("--name", "Baseline", "--seed", "test",
                "--priorities", "loyalty", "generosity", "curiosity")
        original = self.command("draft", *args).stdout
        self.assertEqual(self.command("draft").stdout, original)
        sheet, _ = json.JSONDecoder().raw_decode(original)
        self.assertEqual(len(sheet["scalar"]), 8)
        self.assertEqual(len(sheet["binary"]), 6)
        trait, roll = next(iter(sheet["scalar"].items()))
        habit = sheet["binary"][0]
        result = self.command("init", "--spend", f"{trait}=4", check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.command("draft").stdout, original)
        self.command("init", "--spend", f"{trait}=2", "--habit", f"{habit}=true")
        resident = self.view("status")["residents"][0]
        self.assertEqual(resident["birth"]["priorities"], list(args[-3:]))
        self.assertEqual(resident["birth"]["traits"][trait], round(min(1, roll + 0.2), 4))
        self.assertIs(resident["traits"][habit], True)
        self.assertIn(resident["birth"]["id"], self.command("status").stdout)

    def test_trait_catalog_and_legacy_birth(self):
        catalog = self.view("traits")
        self.assertEqual(len(catalog), 150)
        self.assertEqual(catalog["courage"]["type"], "scalar")
        self.assertEqual(catalog["curiosity"]["type"], "scalar")
        legacy = {"birth": {"version": 1, "seed": "test", "traits": {"courage": 4}}}
        glitch.sync_traits(legacy)
        self.assertEqual(legacy["traits"]["courage"], 0.75)
        self.assertEqual(legacy["birth"]["traits"], {"courage": 4})
        self.assertEqual(set(legacy["traits"]), set(catalog))
        self.found_town()
        private = self.view("status", "--show-hidden")
        public = self.view("status")
        self.assertNotIn("seed", public)
        self.assertNotIn("seed", public["residents"][0]["birth"])
        self.assertEqual(set(private["residents"][0]["traits"]), set(catalog))
        visible = {key for key in catalog if catalog[key]["visibility"] == "visible"}
        self.assertEqual(set(public["residents"][0]["traits"]), visible)

    def test_scheduled_turns_keep_deadlines_across_reopens(self):
        self.found_town()
        deadline = self.view("status")["next_turn_at"]
        interval = 7 * 60 * 60
        with closing(sqlite3.connect(self.db)) as db:
            with patch("glitch.time.time", return_value=deadline - 1):
                self.assertEqual(glitch.step(db, scheduled=True), 1)
            self.assertEqual(glitch.load(db)["turn"], 0)
            with redirect_stdout(io.StringIO()):
                glitch.step(db)
            self.assertEqual(glitch.load(db)["next_turn_at"], deadline)
        with closing(sqlite3.connect(self.db)) as db:
            with patch("glitch.time.time", return_value=deadline + interval):
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(glitch.step(db, scheduled=True), 0)
                    self.assertEqual(glitch.step(db, scheduled=True), 0)
                    self.assertEqual(glitch.step(db, scheduled=True), interval)
            self.assertEqual(glitch.load(db)["turn"], 3)
            self.assertEqual(glitch.load(db)["next_turn_at"], deadline + 2 * interval)

    def test_exploration_and_history_survive_cli_restarts(self):
        self.found_town()
        original = self.view("map")
        self.assertEqual(original, {"cell:0:0": discover("test", 0, 0, 0)})
        first_turns = self.command("step", "--days", "4").stdout
        self.assertEqual(self.view("status")["turn"], 4)
        first_history = self.command("history").stdout
        self.assertEqual(first_history, "Day 0: Baseline arrives in Crossing.\n" + first_turns)
        self.command("step", "--days", "20")
        expanded = self.view("map")
        self.assertGreater(len(expanded), len(original))
        self.assertEqual(expanded["cell:0:0"], original["cell:0:0"])
        ids = {r["birth"]["id"] for r in self.view("status")["residents"]}
        for key, cell in expanded.items():
            if key == "cell:0:0":
                continue
            self.assertIn(cell["discovered_by"], ids)
            self.assertLessEqual(cell["discovered_on"], 24)
            self.assertEqual(cell, discover("test", *cell["coordinates"],
                                           cell["discovered_on"], cell["discovered_by"]))
        self.assertTrue(self.command("history").stdout.startswith(first_history))

    def test_backup_preserves_world_and_pending_draft(self):
        self.command("draft", "--name", "Baseline", "--seed", "test",
                     "--priorities", "loyalty", "generosity", "curiosity")
        draft_backup = self.db.with_name("draft.sqlite3")
        self.command("backup", str(draft_backup))
        self.assertEqual(self.command("draft").stdout,
                         self.command("draft", database=draft_backup).stdout)
        self.command("init")
        self.command("step", "--days", "4")
        snapshot = self.db.with_name("snapshot.sqlite3")
        with closing(sqlite3.connect(self.db)) as db:
            self.assertEqual(db.execute("PRAGMA journal_mode=WAL").fetchone(), ("wal",))
            self.command("step")
            self.command("backup", str(snapshot))
        for args in (("status", "--show-hidden"), ("map",), ("history",)):
            self.assertEqual(self.command(*args).stdout,
                             self.command(*args, database=snapshot).stdout)
        self.command("step", database=snapshot)
        self.assertEqual(self.view("status", database=snapshot)["turn"], 6)
        self.assertEqual(self.view("status")["turn"], 5)

    def test_backup_rejects_missing_source_and_existing_destination(self):
        snapshot = self.db.with_name("snapshot.sqlite3")
        self.assertNotEqual(self.command("backup", str(snapshot), check=False).returncode, 0)
        self.assertFalse(self.db.exists())
        self.assertFalse(snapshot.exists())
        self.found_town()
        snapshot.write_bytes(b"keep me")
        self.assertNotEqual(self.command("backup", str(snapshot), check=False).returncode, 0)
        self.assertEqual(snapshot.read_bytes(), b"keep me")
