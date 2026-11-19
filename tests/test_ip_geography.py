import sqlite3
from contextlib import closing
from hashlib import sha256
from ipaddress import IPv4Address, IPv4Network
from random import Random

import glitch
import test_baseline
from exploration import HOME, discover
from ip_geography import (
    GATEWAYS, GRID_SIZE, INSCRIPTION, ORIGIN, REGIONS, address_at, coordinates,
    neighbors,
)


class GeographyTests(test_baseline.SimulationFixture):
    def test_hilbert_boundaries_and_round_trips(self):
        self.assertEqual(coordinates("0.0.0.0"), (0, 0))
        self.assertEqual(coordinates("255.255.255.255"), (GRID_SIZE - 1, 0))
        rng = Random(31)
        samples = [ORIGIN, "0.0.0.0", "255.255.255.255"]
        for prefix in REGIONS:
            network = IPv4Network(prefix)
            samples.extend(map(str, (network.network_address, network.broadcast_address)))
        samples.extend(str(IPv4Address(rng.getrandbits(32))) for _ in range(1000))
        for address in samples:
            self.assertEqual(address_at(*coordinates(address)), address)
        with self.assertRaises(ValueError):
            address_at(-1, 0)
        with self.assertRaises(ValueError):
            address_at(GRID_SIZE, 0)
        self.assertEqual(len(list(neighbors("0.0.0.0"))), 2)

    def test_origin_has_only_gateway_edges(self):
        self.assertEqual(tuple(neighbors(ORIGIN)), GATEWAYS)
        for gateway, prefix in zip(GATEWAYS, REGIONS):
            self.assertIn(IPv4Address(gateway), IPv4Network(prefix))
            self.assertIn(ORIGIN, neighbors(gateway))
        x, y = coordinates(ORIGIN)
        ordinary = address_at(x + 1, y)
        self.assertNotIn(ORIGIN, neighbors(ordinary))

    def test_birth_and_lazy_discoveries_follow_legal_paths(self):
        self.found_town()
        initial = self.view("map")
        self.assertEqual(set(initial), {ORIGIN})
        self.assertEqual(initial[ORIGIN]["inscription"], INSCRIPTION)
        self.assertEqual(
            sha256(initial[ORIGIN]["inscription"].encode("utf-8")).hexdigest(),
            "2e6db24d74318a5aca5eddc0cd38d3b57db740651d3754a2a38b7d076fe5ad76",
        )
        self.assertIsNone(initial[ORIGIN]["coordinates"])
        status = self.view("status")
        self.assertEqual(status["geography"]["mapping_version"], 1)
        for resident in status["residents"]:
            self.assertEqual(resident["birth"]["site_id"], ORIGIN)
        self.assertEqual(self.view("history", "--json")[0]["site_id"], ORIGIN)
        self.command("step", "--days", "24")
        sites = self.view("map")
        self.assertGreater(len(sites), 1)
        known = {ORIGIN}
        for event in self.view("history", "--json"):
            if event["type"] == "journey.moved":
                start, end = event["effects"]["current_site_ip"]
                self.assertIn(start, known)
                self.assertIn(end, neighbors(start))
                self.assertEqual(end, event["site_id"])
            if event["type"] == "site.discovered":
                site = event["site_id"]
                self.assertNotIn(site, known)
                known.add(site)
                self.assertEqual(sites[site], event["effects"]["site"][1])
        self.assertEqual(set(sites), known)
        self.assertEqual(sites[ORIGIN], initial[ORIGIN])

    def test_existing_coordinate_map_is_preserved_and_can_expand(self):
        self.found_town()
        with closing(sqlite3.connect(self.db)) as db, db:
            world = glitch.load(db)
            world.pop("geography")
            for resident in world["residents"]:
                resident["birth"].pop("site_id")
            known = {HOME: discover("test", 0, 0, 0),
                     "cell:1:0": discover("test", 1, 0, 0)}
            world["map"] = known
            glitch.save(db, world)
        self.assertEqual(self.view("map"), known)
        self.command("step", "--days", "24")
        expanded = self.view("map")
        self.assertGreater(len(expanded), len(known))
        self.assertEqual({key: expanded[key] for key in known}, known)
        self.assertTrue(all(key.startswith("cell:") for key in expanded))
