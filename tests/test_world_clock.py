import io
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, redirect_stdout
from threading import Barrier
from unittest.mock import patch

import glitch
import test_baseline
from world_clock import CATCH_UP_LIMIT, CATCH_UP_PAUSE_SECONDS, WORLD_DAY_SECONDS


class WorldClockTests(test_baseline.SimulationFixture):
    def make_legacy(self, deadline):
        self.found_town()
        with closing(sqlite3.connect(self.db)) as db, db:
            world = glitch.load(db)
            for key in ("genesis_at", "clock_policy_version", "world_day_seconds",
                        "scheduled_days", "last_committed_day", "next_turn_at"):
                world.pop(key)
            if deadline is not None:
                world["next_turn_at"] = deadline
            glitch.save(db, world)

    def test_deadline_migration_is_explicit_idempotent_and_preserves_next_tick(self):
        legacy_deadline = 150000.25
        self.make_legacy(legacy_deadline)
        pending = self.view("clock", "status")
        self.assertTrue(pending["migration_required"])
        self.assertEqual(pending, self.view("clock", "status"))
        self.command("step")
        self.assertTrue(self.view("clock", "status")["migration_required"])
        migrated = self.view("clock", "migrate")
        self.assertEqual(migrated["clock_policy_version"], 1)
        self.assertEqual(migrated["last_committed_day"], 1)
        self.assertEqual(migrated["genesis_at"], legacy_deadline - WORLD_DAY_SECONDS)
        self.assertEqual(migrated["next_turn_at"], legacy_deadline)
        self.assertEqual(migrated, self.view("clock", "migrate"))
        with closing(sqlite3.connect(self.db)) as db, redirect_stdout(io.StringIO()):
            glitch.step(db, scheduled=True, now=legacy_deadline)
        status = self.view("clock", "status")
        self.assertEqual(status["next_turn_at"], legacy_deadline + WORLD_DAY_SECONDS)
        self.assertEqual(status["scheduled_days"], 1)
        self.assertEqual(status["last_committed_day"], 2)

    def test_save_without_deadline_waits_six_hours_after_initialization(self):
        self.make_legacy(None)
        now = 12345.25
        with closing(sqlite3.connect(self.db)) as db:
            self.assertEqual(glitch.step(db, scheduled=True, now=now), WORLD_DAY_SECONDS)
        status = self.view("clock", "status")
        self.assertEqual(status["genesis_at"], now)
        self.assertEqual(status["next_turn_at"], now + WORLD_DAY_SECONDS)
        self.assertEqual(status["last_committed_day"], 0)

    def test_concurrent_runners_commit_a_due_slot_once(self):
        self.found_town()
        initial = self.view("clock", "status")
        barrier = Barrier(2)

        def run_turn():
            with closing(sqlite3.connect(self.db)) as db:
                barrier.wait(timeout=5)
                return glitch.step(db, scheduled=True, now=initial["next_turn_at"])

        with redirect_stdout(io.StringIO()), ThreadPoolExecutor(2) as executor:
            first = executor.submit(run_turn)
            second = executor.submit(run_turn)
            self.assertCountEqual([first.result(), second.result()], [0, WORLD_DAY_SECONDS])
        current = self.view("clock", "status")
        self.assertEqual(current["last_committed_day"], 1)
        self.assertEqual(current["scheduled_days"], 1)

    def test_failed_scheduled_write_leaves_slot_available_after_restart(self):
        self.found_town()
        initial = self.view("clock", "status")
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute("""CREATE TRIGGER fail_scheduled_outbox BEFORE INSERT ON event_outbox
                BEGIN SELECT RAISE(ABORT, 'outbox unavailable'); END""")
        with closing(sqlite3.connect(self.db)) as db:
            with self.assertRaisesRegex(sqlite3.IntegrityError, "outbox unavailable"):
                glitch.step(db, scheduled=True, now=initial["next_turn_at"])
        self.assertEqual(self.view("clock", "status"), initial)
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute("DROP TRIGGER fail_scheduled_outbox")
        with closing(sqlite3.connect(self.db)) as db, redirect_stdout(io.StringIO()):
            self.assertEqual(glitch.step(db, scheduled=True, now=initial["next_turn_at"]), 0)
            self.assertEqual(glitch.step(db, scheduled=True, now=initial["next_turn_at"]),
                             WORLD_DAY_SECONDS)

    def test_runner_yields_when_catch_up_budget_is_exhausted(self):
        self.found_town()
        initial = self.view("clock", "status")
        late = initial["next_turn_at"] + 10 * WORLD_DAY_SECONDS
        with closing(sqlite3.connect(self.db)) as db, redirect_stdout(io.StringIO()):
            with (
                patch("glitch.time.time", return_value=late),
                patch("glitch.time.sleep", side_effect=KeyboardInterrupt) as sleep,
                self.assertRaises(KeyboardInterrupt),
            ):
                glitch.run_scheduled(db)
            sleep.assert_called_once_with(CATCH_UP_PAUSE_SECONDS)
        status = self.view("clock", "status")
        self.assertEqual(status["scheduled_days"], CATCH_UP_LIMIT)
        self.assertEqual(status["next_turn_at"],
                         initial["next_turn_at"] + CATCH_UP_LIMIT * WORLD_DAY_SECONDS)
