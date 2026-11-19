import io
import sqlite3
from contextlib import closing, redirect_stdout
from unittest.mock import patch
from random import Random

import glitch
import test_baseline
from ip_geography import ORIGIN, neighbors
from navigation import ROUTE_EDGES, choose_route


class NavigationTests(test_baseline.SimulationFixture):
    def test_ten_edge_journey_survives_restart_and_surveys_after_arrival(self):
        self.found_town()
        started = None
        for _ in range(24):
            self.command("step")
            started = next((e for e in self.view("history", "--json")
                            if e["type"] == "journey.started"), None)
            if started:
                break
        self.assertIsNotNone(started)
        actor, = started["actor_ids"]
        path = started["effects"]["path"]
        journey_id = started["effects"]["journey_id"]
        self.assertEqual(len(path), ROUTE_EDGES + 1)
        self.assertEqual(path[0], ORIGIN)

        def resident():
            return next(r for r in self.view("status")["residents"] if r["birth"]["id"] == actor)

        self.assertEqual(resident()["current_site_ip"], path[1])
        self.command("step", "--days", "4")
        halfway = resident()
        self.assertEqual(halfway["journey_id"], journey_id)
        self.assertEqual(halfway["journey"]["route_index"], 5)
        self.assertEqual(halfway["current_site_ip"], path[5])
        self.assertEqual(halfway["site_studied"], {})
        self.command("step", "--days", "5")
        arrived = resident()
        destination = path[-1]
        self.assertEqual(arrived["current_site_ip"], destination)
        self.assertEqual(arrived["journey"]["path"], path)
        self.assertIn(destination, arrived["site_seen"])
        self.assertNotIn(destination, arrived["site_studied"])
        self.command("step")
        studied = resident()
        self.assertIsNone(studied["journey_id"])
        self.assertEqual(studied["current_site_ip"], destination)
        self.assertEqual(studied["site_studied"][destination]["day"],
                         studied["site_seen"][destination]["day"] + 1)
        history = self.view("history", "--json")
        moves = [e for e in history if e["type"] == "journey.moved"
                 and e["effects"]["journey_id"] == journey_id]
        self.assertEqual([e["turn"] for e in moves],
                         list(range(started["turn"], started["turn"] + ROUTE_EDGES)))
        self.assertEqual([e["effects"]["current_site_ip"] for e in moves],
                         [[a, b] for a, b in zip(path, path[1:])])

    def test_two_residents_share_canonical_site_but_record_separate_observations(self):
        self.found_town()
        shared_route = choose_route(ORIGIN, Random(3))

        def plan(start, rng):
            return shared_route.copy() if start == ORIGIN else choose_route(start, rng)

        with (closing(sqlite3.connect(self.db)) as db, redirect_stdout(io.StringIO()),
              patch("navigation.choose_route", side_effect=plan)):
            for _ in range(45):
                glitch.step(db)
        history = self.view("history", "--json")
        observers = {}
        discoveries = {}
        for event in history:
            site = event["site_id"]
            if event["type"] == "site.seen":
                observers.setdefault(site, set()).update(event["actor_ids"])
            if event["type"] == "site.discovered":
                self.assertNotIn(site, discoveries)
                discoveries[site] = event["effects"]["site"][1]
        shared = {site for site, actors in observers.items() if len(actors) > 1}
        self.assertTrue(shared)
        cells = self.view("map")
        for site in shared:
            self.assertEqual(discoveries[site], cells[site])

    def test_route_stays_in_grid_at_boundary(self):
        path = choose_route("255.255.255.255", Random(3))
        self.assertEqual(len(set(path)), ROUTE_EDGES + 1)
        for start, end in zip(path, path[1:]):
            self.assertIn(end, neighbors(start))
